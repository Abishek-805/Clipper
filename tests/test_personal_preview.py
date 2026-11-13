import json
import os
import queue
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from clipper import _transcription_fingerprint


class PersonalPreviewTests(unittest.TestCase):
    DIAGNOSTICS = {"gpu_name": None, "cuda_devices": 0, "encoders": {"libx264": True},
                   "ffmpeg": True, "ffprobe": True,
                   "checks": [{"name": "Subtitle filter", "status": "available"},
                              {"name": "Faster-Whisper", "status": "available"}]}

    def app(self, root):
        with patch.object(main, "APP_STATE", root), \
             patch.object(main, "PREFERENCES", root / "preferences.json"), \
             patch.object(main, "collect_diagnostics", return_value=self.DIAGNOSTICS), \
             patch.object(main.ClippingApp, "after", return_value=None, create=True):
            try:
                app = main.ClippingApp()
            except tk.TclError as exc:
                self.skipTest(f"Tk desktop unavailable: {exc}")
        app.withdraw()
        return app

    @staticmethod
    def info(streams):
        return SimpleNamespace(duration=1422.5, width=1920, height=1080, streams=streams)

    def test_transcription_fingerprint_tracks_source_and_audio_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "episode.mkv"
            source.write_bytes(b"source")
            settings = {"model": "small", "language": None, "task": "transcribe",
                        "audio_stream_index": 2, "beam_size": 5}
            initial = _transcription_fingerprint(source, settings)
            changed_stream = {**settings, "audio_stream_index": 1}
            self.assertEqual(initial, _transcription_fingerprint(source, dict(settings)))
            self.assertNotEqual(initial, _transcription_fingerprint(source, changed_stream))
            source.write_bytes(b"changed source")
            self.assertNotEqual(initial, _transcription_fingerprint(source, settings))

    def test_file_selection_is_idle_and_ambiguous_default_does_not_choose_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mkv"
            source.write_bytes(b"source")
            streams = [
                {"index": 0, "type": "video", "codec": "hevc", "language": None, "title": None, "channels": None, "default": True},
                {"index": 1, "type": "audio", "codec": "aac", "language": "jpn", "title": "Japanese", "channels": 2, "default": True},
                {"index": 2, "type": "audio", "codec": "aac", "language": "fra", "title": "French", "channels": 2, "default": False},
            ]
            app = self.app(root)
            try:
                with patch.object(main.filedialog, "askopenfilename", return_value=str(source)), \
                     patch.object(main, "probe", return_value=self.info(streams)), \
                     patch.object(main.threading, "Thread") as worker:
                    app._choose_single()
                    worker.assert_not_called()
                self.assertEqual("Choose audio track…", app.audio_track.get())
                self.assertEqual(str(source), app.single_file.get())
                self.assertFalse(app.running)
                app.caption_source.set("Whisper translation to English")
                self.assertFalse(app.running)
                app.caption_source.set("Whisper transcription")
                app.audio_track.set(next(label for label, index in app.audio_track_indices.items() if index == 2))
                with patch.object(main, "_whisper_model_cached", return_value=True), \
                     patch.object(main, "ModelManager"), \
                     patch.object(main, "PREFERENCES", root / "preferences.json"), \
                     patch.object(main.threading, "Thread") as worker:
                    app._start_single()
                worker.assert_called_once()
                self.assertEqual(2, worker.call_args.kwargs["args"][1][1])
                self.assertEqual(2, worker.call_args.kwargs["args"][5])
                self.assertEqual(5, app.count.get())  # Nightly queue's existing clip count remains unchanged.
                app._start_single()
                worker.assert_called_once()
            finally:
                app._close_store()
                app.destroy()

    def test_one_audio_track_is_selected_and_only_explicit_start_creates_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mp4"
            source.write_bytes(b"source")
            streams = [{"index": 2, "type": "audio", "codec": "aac", "language": "eng",
                        "title": "English", "channels": 2, "default": False}]
            app = self.app(root)
            try:
                with patch.object(main.filedialog, "askopenfilename", return_value=str(source)), \
                     patch.object(main, "probe", return_value=self.info(streams)), \
                     patch.object(main.threading, "Thread") as worker:
                    app._choose_single()
                    worker.assert_not_called()
                self.assertEqual(2, app.audio_track_indices[app.audio_track.get()])
                self.assertFalse(app.running)
                with patch.object(main, "_whisper_model_cached", return_value=True), \
                     patch.object(main, "ModelManager"), patch.object(main, "PREFERENCES", root / "preferences.json"), \
                     patch.object(main.threading, "Thread") as worker:
                    app._start_single()
                self.assertEqual(2, worker.call_args.kwargs["args"][1][1])
                self.assertEqual(2, worker.call_args.kwargs["args"][5])
            finally:
                app._close_store()
                app.destroy()

    def test_unambiguous_english_track_preferred_over_default_foreign_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mkv"
            source.write_bytes(b"source")
            streams = [{"index": 1, "type": "audio", "codec": "aac", "language": "jpn", "title": "Japanese", "channels": 2, "default": True},
                       {"index": 4, "type": "audio", "codec": "aac", "language": "eng", "title": "English", "channels": 2, "default": False}]
            app = self.app(root)
            try:
                with patch.object(main.filedialog, "askopenfilename", return_value=str(source)), \
                     patch.object(main, "probe", return_value=self.info(streams)):
                    app._choose_single()
                self.assertEqual(4, app.audio_track_indices[app.audio_track.get()])
            finally:
                app._close_store()
                app.destroy()

    def test_worker_creates_isolated_provisional_job_folders_and_retains_prior_files(self):
        class Manager:
            def __init__(self): self.releases = 0
            def release_whisper(self): self.releases += 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mkv"
            source.write_bytes(b"unchanged source")
            app = self.app(root)
            app.single_file.set(str(source))
            app.source_info = self.info([])
            app.source_streams = []
            app.single_cancel = threading.Event()
            app.queue = queue.Queue()
            manager = Manager()
            render_count = 0

            def process(_source, _out, model, clips, _min, _max, _cancel, _progress, *_args, **kwargs):
                nonlocal render_count
                render_count += 1
                self.assertEqual(2, clips)
                folder = kwargs["job_output_folder"]
                (folder / "clip_001.mp4").write_bytes(f"job {render_count}".encode())
                (folder / "clip_001.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
                (folder / "analysis.json").write_text(json.dumps({"clips": [{"filename": "clip_001.mp4", "validation": "VALIDATED", "start": 1, "end": 2}],
                    "candidate_selection": {"candidate_count": 4}, "analysis_cache_status": "reused",
                    "transcription_cache_status": "reused", "transcription_outcome": "SUCCESS",
                    "transcription_device": "cuda", "transcription_precision": "float16"}), encoding="utf-8")
                return 1

            try:
                with patch.object(main, "OUTPUT", root / "output"), patch.object(main, "ANALYSIS_CACHE", root / "cache"), \
                     patch.object(main, "process_video", side_effect=process):
                    for _ in range(2):
                        app._single_worker(source, ("small", 2, 20, 60, "auto", False), manager, None, True, 2)
                events = [app.queue.get_nowait(), app.queue.get_nowait()]
                jobs = [Path(event[1][2]) for event in events]
                self.assertNotEqual(jobs[0], jobs[1])
                self.assertEqual(b"job 1", (jobs[0] / "clip_001.mp4").read_bytes())
                self.assertTrue(all((path / "analysis.json").is_file() for path in jobs))
                reports = [json.loads((path / "analysis.json").read_text(encoding="utf-8")) for path in jobs]
                self.assertTrue(all(report["selection_status"] == "PROVISIONAL" for report in reports))
                self.assertTrue(all(report["selected_audio_stream_index"] == 2 for report in reports))
                self.assertTrue(all(report["transcription_device"] == "cuda" for report in reports))
                self.assertEqual(2, manager.releases)
            finally:
                app._close_store()
                app.destroy()

    def test_worker_records_failure_if_source_disappears_after_selection(self):
        class Manager:
            def __init__(self): self.releases = 0
            def release_whisper(self): self.releases += 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "episode.mkv"
            app = self.app(root)
            app.single_file.set(str(source))
            app.single_cancel = threading.Event()
            app.queue = queue.Queue()
            manager = Manager()
            try:
                with patch.object(main, "OUTPUT", root / "output"), patch.object(main, "ANALYSIS_CACHE", root / "cache"):
                    app._single_worker(source, ("small", 2, 20, 60, "auto", False), manager, None, False)
                event, = [app.queue.get_nowait()]
                _name, count, folder, outcome, report = event[1]
                self.assertEqual(0, count)
                self.assertEqual("failed", outcome)
                self.assertIn("FileNotFoundError", report["failure"])
                self.assertEqual("PROVISIONAL", report["selection_status"])
                self.assertTrue((folder / "analysis.json").is_file())
                self.assertEqual(1, manager.releases)
            finally:
                app._close_store()
                app.destroy()


if __name__ == "__main__":
    unittest.main()
