import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class StartupSafetyTests(unittest.TestCase):
    DIAGNOSTICS = {"gpu_name": None, "cuda_devices": 0, "encoders": {"libx264": True},
                   "ffmpeg": True, "ffprobe": True, "checks": [{"name": "Subtitle filter", "status": "available"},
                   {"name": "Faster-Whisper", "status": "available"}]}

    def _app(self, state_dir):
        with patch.object(main, "APP_STATE", state_dir), \
             patch.object(main, "PREFERENCES", state_dir / "preferences.json"), \
             patch.object(main, "collect_diagnostics", return_value=self.DIAGNOSTICS), \
             patch.object(main.ClippingApp, "after", return_value=None, create=True):
            try:
                app = main.ClippingApp()
            except tk.TclError as exc:
                self.skipTest(f"Tk desktop unavailable: {exc}")
        app.withdraw()
        return app

    def test_launch_restores_folder_without_starting_queue_or_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "videos"
            folder.mkdir()
            (root / "preferences.json").write_text(json.dumps({"last_folder": str(folder)}), encoding="utf-8")
            with patch.object(main, "NightlySessionRunner") as runner:
                app = self._app(root)
                try:
                    app.update_idletasks()
                    self.assertEqual(str(folder), app.folder.get())
                    self.assertFalse(app.running)
                    self.assertIsNone(app.runner)
                    runner.assert_not_called()
                finally:
                    app._close_store()
                    app.destroy()

    def test_application_source_has_no_desktop_capture_api(self):
        source = "\n".join((Path(main.__file__).parent / name).read_text(encoding="utf-8").lower()
                            for name in ("main.py", "clipper.py", "queue_runner.py"))
        for token in ("pyautogui.screenshot", "imagegrab.grab", "mss.mss", "desktop_duplication", "screenrecord"):
            self.assertNotIn(token, source)

    def test_interrupted_episode_requires_confirmation_at_explicit_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "videos"
            folder.mkdir()
            source = folder / "episode.mp4"
            source.write_bytes(b"video")
            (root / "preferences.json").write_text(json.dumps({"last_folder": str(folder)}), encoding="utf-8")
            app = self._app(root)
            try:
                row, = app.queue_store.scan(folder, [source])
                app.queue_store.set_episode_status(row["id"], "INTERRUPTED")
                app.folder.set(str(folder))
                with patch.object(main, "PREFERENCES", root / "preferences.json"), \
                     patch.object(main, "_whisper_model_cached", return_value=True), \
                     patch.object(main.messagebox, "askyesno", return_value=False) as confirm, \
                     patch.object(main.threading, "Thread") as thread:
                    app._start()
                confirm.assert_called_once()
                thread.assert_called_once()
                self.assertTrue(app.running)
                self.assertFalse(app.runner.resume_interrupted)
            finally:
                app._close_store()
                app.destroy()

    def test_legacy_interrupted_ledger_is_imported_without_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "videos"
            folder.mkdir()
            source = folder / "episode.mp4"
            source.write_bytes(b"video")
            ledger = root / "interrupted_job.json"
            ledger.write_text(json.dumps({"status": "interrupted", "folder": str(folder),
                                          "videos": [str(source)], "completed": []}), encoding="utf-8")
            with patch.object(main, "INTERRUPTED_JOB", ledger), patch.object(main, "NightlySessionRunner") as runner:
                app = self._app(root)
                try:
                    app.folder.set(str(folder))
                    app.queue_store.scan(folder, [source])
                    app._import_legacy_interrupted_job()
                    self.assertEqual("INTERRUPTED", app.queue_store.episodes()[0]["status"])
                    self.assertFalse(app.running)
                    runner.assert_not_called()
                finally:
                    app._close_store()
                    app.destroy()

    def test_close_requests_safe_stop_and_never_auto_resumes(self):
        app = self._app(Path(tempfile.mkdtemp()))
        class Runner:
            stopped = False
            def stop(self):
                self.stopped = True
        try:
            app.running = True
            app.runner = Runner()
            with patch.object(main.messagebox, "askyesno", return_value=True):
                app._on_close()
            self.assertTrue(app.runner.stopped)
            self.assertTrue(app.close_when_finished)
        finally:
            app._close_store()
            app.destroy()


if __name__ == "__main__":
    unittest.main()
