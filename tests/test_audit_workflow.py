import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import browser_preview as browser
import clipper
import diagnostics
import main
from daily_workflow import transcript_rows
from tests import test_rerender as fixtures


@pytest.fixture
def local_console(tmp_path, monkeypatch):
    before, prior_cancel = dict(browser._STATE), browser._CANCEL
    for key, value in dict(OUTPUT=tmp_path / "output", ANALYSIS_CACHE=tmp_path / "cache",
                           APP_STATE=tmp_path / "state", PREFERENCES=tmp_path / "prefs.json").items():
        monkeypatch.setattr(browser, key, value)
    browser._STATE.clear()
    browser._STATE.update(state="idle", rendering=False, reviewing=False, importing=False)
    server = browser.LocalServer(("127.0.0.1", 0), browser.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
        browser._STATE.clear()
        browser._STATE.update(before)
        browser._CANCEL = prior_cancel


def post(base, path, body):
    with urlopen(Request(base + path, data=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json"}), timeout=30) as response:
        return json.load(response)


def test_source_transcript_preview_and_invalid_track_stay_isolated(local_console, tmp_path):
    source, _, _ = fixtures.SingleClipRerenderTests().make_job(tmp_path)
    words = [clipper.Word(.1, .4, "Synthetic"), clipper.Word(.4, .8, " answer."),
             clipper.Word(1., 1.5, "Context.")]
    result = clipper.TranscriptionResult("en", words, "transcribed", "seeded-test", "not-run")
    body = dict(source=str(source), audio_index=1, caption_mode="Whisper transcription", allow_transcription=False)
    with patch.object(browser, "whisper_transcript", return_value=result) as transcript, \
         patch.object(browser, "_run_job", side_effect=AssertionError("No automatic processing")):
        data = post(local_console, "/api/transcript", body)
        assert data["rows"][0] == dict(start=.1, end=.8, text="Synthetic answer.")
        assert transcript.call_args.args[5] is False
        preview = post(local_console, "/api/source-preview", {**body, "start": .1, "end": 1.6})
        request = Request(local_console + preview["url"], headers={"Range": "bytes=-64"})
        with urlopen(request) as response:
            assert response.status == 206
            assert len(response.read()) == 64
        with pytest.raises(HTTPError) as error:
            post(local_console, "/api/transcript", {**body, "audio_index": 999})
        assert error.value.code == 400
        error.value.close()
        assert not browser._operation_active()
        assert browser._CANCEL is None


def test_captionless_automatic_path_never_loads_transcript_or_editorial_model(tmp_path):
    source, _, _ = fixtures.SingleClipRerenderTests().make_job(tmp_path)
    with patch.object(clipper, "transcribe", side_effect=AssertionError("None must not read cached speech")), \
         patch.object(clipper, "local_endpoint", side_effect=AssertionError("Daily workflow has no model calls")):
        clipper.process_video(source, tmp_path / "output", "small", 1, 1, 2, threading.Event(), lambda _: None,
                              analysis_only=True, caption_source="None", transcription_enabled=False,
                              editorial_enabled=False, strict_captions=True, job_output_folder=tmp_path / "job")
    data = json.loads((tmp_path / "job" / "analysis.json").read_text())
    assert data["transcription_device"] == "not_run"


def test_static_speech_uses_source_time_not_window_index():
    words = [clipper.Word(10, 11, "A complete"), clipper.Word(11, 12, " answer.")]
    features = clipper._candidate_features(dict(start=10, end=12, sources=[]), [0.] * 15, [0.] * 15, words, [], [])
    assert features["static_interval"] == 0


def test_cached_model_requires_weights_not_empty_directory(tmp_path):
    with patch("faster_whisper.utils.download_model", return_value=str(tmp_path)):
        assert not main._whisper_model_cached("small")
        (tmp_path / "model.bin").write_bytes(b"synthetic test weight marker")
        assert main._whisper_model_cached("small")


def test_winget_discovery_uses_valid_glob(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    binary = tmp_path / "Microsoft/WinGet/Packages/Gyan.FFmpeg_test/ffmpeg-test/bin/ffmpeg.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"test path only")
    assert binary in diagnostics._tool_candidates("ffmpeg")


def test_transcript_rows_break_on_sentence_and_pause():
    rows = transcript_rows([clipper.Word(0, .2, "First."), clipper.Word(.3, .5, "Next"),
                            clipper.Word(2, 2.2, "Later.")])
    assert [r["text"] for r in rows] == ["First.", "Next", "Later."]


@pytest.mark.parametrize("complete", [True, False])
def test_explicit_setup_checks_download_result_without_starting_server(tmp_path, complete):
    if complete:
        (tmp_path / "model.bin").write_bytes(b"test marker")
    with patch("faster_whisper.utils.download_model", return_value=str(tmp_path)) as download, \
         patch.object(browser, "serve", side_effect=AssertionError("Setup must not launch server")):
        if complete:
            browser.setup_whisper()
        else:
            with pytest.raises(SystemExit, match="incomplete"):
                browser.setup_whisper()
        download.assert_called_once_with("small")


def test_early_cancellation_reaches_daily_worker(local_console, tmp_path):
    source, _, _ = fixtures.SingleClipRerenderTests().make_job(tmp_path)
    reserved = threading.Event()
    reserved.set()
    browser._CANCEL = reserved
    browser._STATE["state"] = "running"
    with patch.object(browser, "ModelManager", return_value=SimpleNamespace(release_whisper=lambda: None)), \
         patch.object(browser, "process_video", return_value=0) as process:
        browser._run_job(source, 1, "None", "cancel-before-worker")
        assert process.call_args.args[6] is reserved
        assert process.call_args.args[6].is_set()
        assert process.call_args.kwargs["editorial_enabled"] is False
    assert browser._STATE["state"] == "interrupted"
    assert browser._CANCEL is None
