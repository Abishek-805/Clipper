import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import browser_preview
from browser_preview import Handler, LocalServer, _probe_source, serve


class BrowserPreviewTests(unittest.TestCase):
    def test_server_refuses_non_loopback_bind(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            serve("0.0.0.0", 0)

    def test_probe_rejects_missing_or_unsupported_source(self):
        with self.assertRaises((FileNotFoundError, ValueError)):
            _probe_source("Z:/missing-clipper-test.mkv")

    def test_local_page_and_status_api_are_served(self):
        server = LocalServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            page = urlopen(base + "/", timeout=3).read().decode()
            self.assertIn("Generate up to two provisional drafts", page)
            self.assertIn("Applied glossary replacements", page)
            self.assertIn("Save &amp; Re-render Clip", page)
            self.assertIn("data-nudge", page)
            self.assertIn("bottom-safe", page)
            self.assertIn("center-lower", page)
            self.assertIn("rights not verified", page)
            self.assertIn("overflow-wrap:anywhere", page)
            self.assertIn("@media(max-width:650px)", page)
            status = json.loads(urlopen(base + "/api/status", timeout=3).read())
            self.assertIn("state", status)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_interrupted_status_counts_persisted_completed_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mkv"
            source.write_bytes(b"source")
            streams = [{"index": 2, "type": "audio", "language": "eng", "codec": "aac"}]

            def cancel_after_first_clip(*_args, **kwargs):
                job_dir = kwargs["job_output_folder"]
                (job_dir / "clip_001.mp4").write_bytes(b"completed")
                (job_dir / "clip_001.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
                (job_dir / "analysis.json").write_text(json.dumps({
                    "clips": [{"filename": "clip_001.mp4", "validation": "VALIDATED",
                               "render": {"glossary_replacements": [{"original_text": "Kaya-la",
                                   "replacement": "Kayaba", "cue_number": 1}]}}],
                    "analysis_cache_status": "reused", "transcription_cache_status": "reused",
                }), encoding="utf-8")
                raise InterruptedError("Processing cancelled")

            previous_state = dict(browser_preview._STATE)
            try:
                with patch.object(browser_preview, "OUTPUT", root / "output"), \
                     patch.object(browser_preview, "ANALYSIS_CACHE", root / "cache"), \
                     patch.object(browser_preview, "APP_STATE", root / "state"), \
                     patch.object(browser_preview, "PREFERENCES", root / "preferences.json"), \
                     patch.object(browser_preview, "_read_json", return_value={}), \
                     patch.object(browser_preview, "probe", return_value=SimpleNamespace(streams=streams)), \
                     patch.object(browser_preview, "ModelManager", return_value=SimpleNamespace(release_whisper=lambda: None)), \
                     patch.object(browser_preview, "process_video", side_effect=cancel_after_first_clip):
                    browser_preview._run_job(source, 2, "Whisper transcription", "cancel-test")
                self.assertIn("Interrupted · 1 draft(s)", browser_preview._STATE["status"])
                self.assertEqual(1, browser_preview._STATE["report"]["selected_draft_count"])
                self.assertEqual("Kayaba", browser_preview._STATE["report"]["drafts"][0]["glossary_replacements"][0]["replacement"])
            finally:
                browser_preview._STATE.clear()
                browser_preview._STATE.update(previous_state)

    def test_rapid_analysis_start_reserves_one_worker(self):
        from concurrent.futures import ThreadPoolExecutor
        previous = dict(browser_preview._STATE)
        release = threading.Event()
        entered = []
        server = LocalServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def worker(*args):
            entered.append(args)
            release.wait(3)
        try:
            browser_preview._STATE.update(state="idle", rendering=False)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            body = json.dumps({"source":"episode.mkv", "audio_index":2, "caption_mode":"Whisper transcription"}).encode()
            def request():
                try:
                    with urlopen(Request(base+"/api/start",data=body,headers={"Content-Type":"application/json"}),timeout=3) as response:
                        return response.status
                except HTTPError as exc:
                    code = exc.code
                    exc.close()
                    return code
            info = SimpleNamespace(streams=[{"type":"audio","index":2}])
            with patch.object(browser_preview,"_probe_source",return_value=(Path("episode.mkv"),info)), patch.object(browser_preview,"_whisper_model_cached",return_value=True), patch.object(browser_preview,"_run_job",side_effect=worker):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    codes = list(pool.map(lambda _: request(), range(2)))
                self.assertEqual([202,409],sorted(codes))
                self.assertEqual(1,len(entered))
        finally:
            release.set()
            server.shutdown();server.server_close();thread.join(3)
            browser_preview._STATE.clear();browser_preview._STATE.update(previous)

    def test_second_editor_save_is_rejected_while_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "Shorts_Output"
            job_id = "test-job"
            job = output / "jobs" / job_id
            job.mkdir(parents=True)
            (job / "analysis.json").write_text("{}", encoding="utf-8")
            previous_state = dict(browser_preview._STATE)
            entered, release = threading.Event(), threading.Event()
            def blocked_worker(*_args):
                entered.set()
                release.wait(2)
            server = LocalServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                browser_preview._STATE.update(state="completed", job_id=job_id, rendering=False,
                    report={"drafts": [{"filename": "clip_001.mp4"}]})
                base = f"http://127.0.0.1:{server.server_address[1]}"
                body = json.dumps({"job_id": job_id, "clip_id": "clip_001.mp4", "start": 0,
                    "end": 1, "captions": None, "caption_position": "bottom-safe"}).encode()
                with patch.object(browser_preview, "OUTPUT", output), patch.object(browser_preview, "_run_rerender", side_effect=blocked_worker):
                    request = lambda: urlopen(Request(base + "/api/rerender", data=body,
                        headers={"Content-Type": "application/json"}, method="POST"), timeout=3)
                    self.assertEqual(202, request().status)
                    self.assertTrue(entered.wait(1))
                    with self.assertRaises(HTTPError) as conflict:
                        request()
                    self.assertEqual(409, conflict.exception.code)
                    conflict.exception.close()
                release.set()
            finally:
                release.set()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                browser_preview._STATE.clear()
                browser_preview._STATE.update(previous_state)


if __name__ == "__main__":
    unittest.main()
