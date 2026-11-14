import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import browser_preview as browser
from structure import detect_zones


class BrowserZonesTests(unittest.TestCase):
    def test_saved_draft_retains_soft_warning_and_signal_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "jobs" / "example"
            job.mkdir(parents=True)
            (job / "clip_001.mp4").write_bytes(b"saved-output")
            warning = dict(label="OP", reason="Position only")
            (job / "analysis.json").write_text(json.dumps(dict(clips=[dict(filename="clip_001.mp4", zone_warnings=[warning], reason="High motion")])) )
            with patch.object(browser, "OUTPUT", root), patch.object(browser, "_publish") as publish:
                browser._open_job("example")
                draft = publish.call_args.kwargs["report"]["drafts"][0]
                self.assertEqual(draft["zone_warnings"], [warning])
                self.assertEqual(draft["selection_reason"], "High motion")

    def test_inspection_save_and_source_mismatch_are_safe(self):
        previous = dict(browser._STATE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mkv"
            source.write_bytes(b"source")
            value = dict(version="episode-structure-v1", duration=100, source_hash="a"*64, tracks=[],
                         detected=detect_zones([dict(start_time=0, end_time=30, tags=dict(title="OP"))], [], 100),
                         overrides=[], override_file=str(root / "overrides.json"))
            value["active"] = value["detected"]
            server = browser.LocalServer(("127.0.0.1", 0), browser.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                browser._STATE.clear()
                browser._STATE.update(state="idle", rendering=False)
                with patch.object(browser, "prepare_structure", return_value=value), \
                     patch.object(browser, "_probe_source", return_value=(source, None)), \
                     patch.object(browser, "PREFERENCES", root / "prefs.json"), \
                     patch.object(browser, "OUTPUT", root), \
                     patch.object(browser, "_run_job", side_effect=AssertionError("Must stay idle")):
                    with urlopen(base + "/api/zones?source=episode.mkv") as response:
                        self.assertEqual(json.load(response)["active"][0]["level"], "HARD")
                    records = [dict(action="suppress", id=value["detected"][0]["id"])]
                    def save(**extra):
                        payload = dict(source=str(source), overrides=records, filter_enabled=True, **extra)
                        return urlopen(Request(base + "/api/zones", data=json.dumps(payload).encode(),
                                               headers={"Content-Type": "application/json"}))
                    with save() as response:
                        self.assertEqual(json.load(response)["active"], [])
                    self.assertEqual(json.loads((root / "overrides.json").read_text()), records)
                    self.assertEqual(browser._STATE["state"], "idle")
                    job = root / "jobs" / "different"
                    job.mkdir(parents=True)
                    (job / "analysis.json").write_text(json.dumps(dict(source_path=str(root / "other.mkv"))))
                    (root / "overrides.json").write_text("[]")
                    with self.assertRaises(HTTPError) as error:
                        save(job_id="different")
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
                    self.assertEqual((root / "overrides.json").read_text(), "[]")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)
                browser._STATE.clear()
                browser._STATE.update(previous)
