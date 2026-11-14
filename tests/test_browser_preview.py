import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen

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
                    "clips": [{"filename": "clip_001.mp4", "validation": "VALIDATED"}],
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
            finally:
                browser_preview._STATE.clear()
                browser_preview._STATE.update(previous_state)


if __name__ == "__main__":
    unittest.main()
