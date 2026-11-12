import json
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class StartupSafetyTests(unittest.TestCase):
    DIAGNOSTICS = {"gpu_name": None, "cuda_devices": 0, "encoders": {"libx264": True}, "ffmpeg": True, "ffprobe": True, "checks": [{"name": "Subtitle filter", "status": "available"}]}

    def _app(self):
        with patch.object(main, "collect_diagnostics", return_value=self.DIAGNOSTICS), patch.object(main.ClippingApp, "after", return_value=None, create=True):
            try:
                app = main.ClippingApp()
            except tk.TclError as exc:
                self.skipTest(f"Tk desktop unavailable: {exc}")
        app.withdraw()
        return app

    def test_gui_launch_restores_folder_without_starting_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "videos"
            folder.mkdir()
            pref = root / "preferences.json"
            pref.write_text(json.dumps({"last_folder": str(folder)}), encoding="utf-8")
            with patch.object(main, "PREFERENCES", pref), patch.object(main, "INTERRUPTED_JOB", root / "missing-job.json"), patch.object(main, "process_video") as process:
                app = self._app()
                try:
                    app.update_idletasks()
                    self.assertEqual(str(folder), app.folder.get())
                    self.assertFalse(app.running)
                    process.assert_not_called()
                finally:
                    app.destroy()

    def test_resume_requires_confirmation_and_decline_keeps_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "videos"
            folder.mkdir()
            source = folder / "episode.mp4"
            source.touch()
            pref, job_path = root / "preferences.json", root / "interrupted_job.json"
            pref.write_text(json.dumps({"last_folder": str(folder)}), encoding="utf-8")
            job = {"status": "running", "folder": str(folder), "videos": [str(source)], "completed": [], "settings": ["small", 5, 20, 55, "auto"]}
            job_path.write_text(json.dumps(job), encoding="utf-8")
            with patch.object(main, "PREFERENCES", pref), patch.object(main, "INTERRUPTED_JOB", job_path), patch.object(main.messagebox, "askyesno", return_value=False) as confirm, patch.object(main, "process_video") as process:
                app = self._app()
                try:
                    app._offer_resume(job)
                    confirm.assert_called_once()
                    process.assert_not_called()
                    self.assertFalse(app.running)
                    self.assertEqual("interrupted", json.loads(job_path.read_text(encoding="utf-8"))["status"])
                finally:
                    app.destroy()

    def test_accepting_resume_confirmation_stages_job_until_start_is_clicked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "videos"
            folder.mkdir()
            source = folder / "episode.mp4"
            source.touch()
            job = {"status": "interrupted", "folder": str(folder), "videos": [str(source)], "completed": [], "settings": ["small", 5, 20, 55, "auto"]}
            job_path = root / "interrupted_job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            with patch.object(main, "PREFERENCES", root / "preferences.json"), patch.object(main, "INTERRUPTED_JOB", job_path), patch.object(main.messagebox, "askyesno", return_value=True), patch.object(main, "process_video") as process:
                app = self._app()
                try:
                    app._offer_resume(job)
                    self.assertEqual(job, app.resume_job)
                    self.assertFalse(app.running)
                    process.assert_not_called()
                    with patch("main.threading.Thread") as thread:
                        app.start_button.invoke()
                        thread.assert_called_once()
                        self.assertEqual(job, thread.call_args.kwargs["args"][2])
                    self.assertTrue(app.running)
                    process.assert_not_called()
                finally:
                    app.destroy()

    def test_worker_records_completed_videos_when_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "first.mp4", root / "second.mp4"
            first.touch()
            second.touch()
            job_path, output = root / "interrupted_job.json", root / "output"
            job = {"status": "running", "folder": str(root), "videos": [str(first), str(second)], "completed": [], "settings": ["small", 5, 20, 55, "auto"]}
            with patch.object(main, "PREFERENCES", root / "preferences.json"), patch.object(main, "INTERRUPTED_JOB", job_path), patch.object(main, "OUTPUT", output):
                app = self._app()
                try:
                    app.cancel_event = threading.Event()

                    def finish_first(path, *_args):
                        app.cancel_event.set()
                        return 1

                    with patch.object(main, "process_video", side_effect=finish_first):
                        app._worker([first, second], tuple(job["settings"]), job)
                    saved = json.loads(job_path.read_text(encoding="utf-8"))
                    self.assertEqual("interrupted", saved["status"])
                    self.assertEqual([str(first)], saved["completed"])
                    messages = []
                    while not app.queue.empty():
                        messages.append(app.queue.get_nowait())
                    self.assertIn(("finished", (1, 2, True)), messages)
                finally:
                    app.destroy()

    def test_close_during_processing_confirms_then_requests_cooperative_cancel(self):
        app = self._app()
        try:
            app.running = True
            app.cancel_event = threading.Event()
            with patch.object(main.messagebox, "askyesno", side_effect=[False, True]) as confirm:
                app._on_close()
                self.assertFalse(app.cancel_event.is_set())
                app._on_close()
            self.assertEqual(2, confirm.call_count)
            self.assertTrue(app.cancel_event.is_set())
            self.assertTrue(app.close_when_finished)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
