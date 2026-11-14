import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import clipper
import structure
from daily_workflow import manual_clip
from reframing import build_reframe_plan
from source_profiles import FULL_FRAME, validate_profile
from tests import test_rerender as fixtures


def test_browser_validates_profile_before_start_and_passes_it_to_worker():
    import browser_preview as browser
    before, cancel_before = dict(browser._STATE), browser._CANCEL
    server = browser.LocalServer(("127.0.0.1", 0), browser.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    entered = threading.Event()
    received = []

    def worker(*args):
        received.append(args)
        entered.set()

    try:
        with patch.object(browser, "_probe_source", return_value=(Path("recording.mp4"), SimpleNamespace(streams=[]))), \
             patch.object(browser, "_run_job", side_effect=worker):
            for profile in ["broadcast", "sports_gaming", "presentation", "animation", "general", "invalid"]:
                browser._STATE.update(state="idle", rendering=False, importing=False, reviewing=False)
                entered.clear()
                body = dict(source="recording.mp4", audio_index=None, caption_mode="None", source_profile=profile)
                request = Request(base + "/api/start", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                if profile == "invalid":
                    with pytest.raises(HTTPError) as error:
                        urlopen(request, timeout=3)
                    assert error.value.code == 400
                    error.value.close()
                    assert not entered.is_set()
                else:
                    with urlopen(request, timeout=3) as response:
                        assert response.status == 202
                    assert entered.wait(3)
                    assert received[-1][-1] == profile
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        browser._STATE.clear()
        browser._STATE.update(before)
        browser._CANCEL = cancel_before


@pytest.mark.parametrize("value", [None, "anime", "", [], 1])
def test_profile_boundary_validation(value):
    with pytest.raises(ValueError, match="source profile"):
        validate_profile(value)


def test_profile_policy_preserves_raw_detection_cache_and_owner_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "broadcast.mkv"
        source.write_bytes(b"synthetic source")
        chapter = {"start_time": "0", "end_time": "60", "tags": {"title": "Opening remarks"}}
        detected = structure.detect_zones([chapter], [], 1200)
        raw = dict(version=structure.VERSION, duration=1200, chapters=[chapter], tracks=[], detected=detected)
        with patch.object(structure, "inspect_source", return_value=raw) as inspect:
            first = structure.prepare_structure(source, root / "cache", source_profile="general")
            artifact = next((root / "cache").glob(structure.VERSION + "-*.json"))
            original = artifact.read_bytes()
            assert not first["active"]
            assert {z["id"] for z in first["profile_ignored"]} == {z["id"] for z in detected}
            overrides = [{"id": detected[0]["id"], "action": "edit", "zone": {"reason": "Owner verified"}}]
            structure.write_json(first["override_file"], overrides)
            broadcast = structure.prepare_structure(source, root / "cache", source_profile="broadcast")
            animation = structure.prepare_structure(source, root / "cache", source_profile="animation")
            assert len(broadcast["active"]) == 1
            assert broadcast["active"][0]["reason"] == "Owner verified"
            assert len(animation["active"]) == len(detected)
            assert inspect.call_count == 1
            assert artifact.read_bytes() == original
            assert broadcast["detected"] == detected


def test_preserve_frame_plan_does_not_decode_or_run_face_detector():
    for profile in FULL_FRAME:
        plan = build_reframe_plan(Path("not-opened.mkv"), 10, 70, [20, 40], preserve_source=True)
        assert plan["analysis_samples"] == 0
        assert plan["shots"][0]["composition"] == "full_frame_contain"
        assert plan["shots"][0]["crop"] is None
        assert plan["shots"][0]["start"] == 10
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        build_reframe_plan(Path("unused"), 0, 1, [], cancelled, preserve_source=True)


@pytest.mark.parametrize("long_score,short_score", [(1., .9), (.9, 1.)])
def test_nested_duplicates_keep_higher_score_in_either_order(long_score, short_score):
    items = [{"start": 0, "end": 180, "score": long_score},
             {"start": 40, "end": 60, "score": short_score},
             {"start": 300, "end": 330, "score": .8}]
    kept, rejected = clipper.deduplicate_candidates(items)
    assert len(kept) == 2
    assert kept[0]["score"] == 1.
    assert len(rejected) == 1
    for item in items:
        item["feature_scores"] = dict(average_motion=.5, average_audio_energy=.2,
                                     speech_density=.3, dialogue_completeness=1.)
    diverse, _ = clipper.select_diverse_candidates(items, 3, 1000, minimum_distance=0)
    assert len(diverse) == 2


def test_short_overlap_is_not_duplicate():
    assert not clipper._duplicate_interval({"start": 0, "end": 30}, {"start": 25, "end": 55})
    assert not clipper._duplicate_interval({"start": 0, "end": 0}, {"start": 0, "end": 0})


def test_manual_broadcast_render_and_rerender_keep_source_layout(tmp_path):
    source, _, _ = fixtures.SingleClipRerenderTests().make_job(tmp_path)
    folder = tmp_path / "broadcast"
    with patch.object(clipper, "select_clips", side_effect=AssertionError("Manual must bypass selector")):
        result = manual_clip(source, folder, tmp_path / "cache", 1, 0, 1.5, "None", False,
                             threading.Event(), source_profile="broadcast")
    assert result["settings"]["source_profile"] == "broadcast"
    clip = result["clips"][0]
    assert clip["render"]["width"] == 1080
    assert clip["render"]["height"] == 1920
    assert clip["render"]["composition_modes"] == ["full_frame_contain"]
    clipper.rerender_single_clip(folder, "clip_001.mp4", 0, 1.2, None, "bottom-safe", render_preset="fast")
    saved = json.loads((folder / "analysis.json").read_text())
    assert saved["clips"][0]["render"]["composition_modes"] == ["full_frame_contain"]
    assert saved["settings"]["source_profile"] == "broadcast"
