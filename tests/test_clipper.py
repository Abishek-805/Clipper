import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from clipper import Word, _scene_cuts, _srt, probe, rank_clips, render, scan_videos, transcribe


class ClipperTests(unittest.TestCase):
    def test_scan_recurses_and_filters_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "nested" / "episode.MKV").touch()
            (root / "output").mkdir()
            (root / "output" / "finished.mp4").touch()
            (root / "readme.txt").touch()
            self.assertEqual(["episode.MKV"], [p.name for p in scan_videos(root, root / "output")])

    def test_rank_selects_valid_distributed_segments(self):
        motion = [0.0] * 120
        audio = [0.0] * 120
        motion[20:30] = [8.0] * 10
        audio[70:80] = [5.0] * 10
        words = [Word(25, 25.3, "hello")]
        clips = rank_clips(120, motion, audio, [20, 75], words, count=2, min_length=20, max_length=25)
        self.assertEqual(2, len(clips))
        self.assertTrue(all(0 <= c["start"] < c["end"] <= 120 for c in clips))
        self.assertTrue(all("reason" in c and "score" in c for c in clips))

    def test_srt_times_are_relative_and_speech_only(self):
        srt = _srt([Word(11, 11.5, " hello"), Word(11.5, 12, "world.")], 10, 20)
        self.assertIn("00:00:01,000 --> 00:00:02,000", srt)
        self.assertIn("hello world.", srt)

    def test_boundaries_stay_inside_source_and_length_limit(self):
        words = [Word(19.7, 20.0, "before"), Word(24, 24.2, "inside"), Word(45, 45.2, "after")]
        clips = rank_clips(60, [0.0] * 60, [0.0] * 60, [], words, count=1, min_length=20, max_length=25)
        self.assertLessEqual(clips[0]["end"] - clips[0]["start"], 25)
        self.assertGreaterEqual(clips[0]["start"], 0)
        self.assertLessEqual(clips[0]["end"], 60)

    def test_transcription_gracefully_skips_missing_optional_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "source.mp4"
            video.touch()
            with patch.dict(sys.modules, {"faster_whisper": None}):
                self.assertEqual((None, []), transcribe(video, True, "small", Path(tmp) / "cache.json", threading.Event()))

    def test_scene_detection_optional_failure_returns_no_cuts(self):
        with patch.dict(sys.modules, {"scenedetect": None}):
            self.assertEqual([], _scene_cuts(Path("missing.mp4")))

    def test_probe_rejects_video_without_video_stream(self):
        with patch("clipper._run") as run:
            run.return_value.stdout = '{"streams": [{"codec_type": "audio"}], "format": {"duration": "1"}}'
            with self.assertRaisesRegex(ValueError, "No video stream"):
                probe(Path("audio.mp3"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe unavailable")
    def test_metadata_and_vertical_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
            info = probe(source)
            self.assertEqual((320, 240), (info.width, info.height))
            output = root / "clip.mp4"
            render(source, output, info, {"start": 0, "end": 3}, [], True, threading.Event())
            data = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type,width,height:format=format_name", "-of", "json", str(output)]))
            streams = data["streams"]
            video = next(s for s in streams if s["codec_type"] == "video")
            self.assertEqual((1080, 1920, "h264"), (video["width"], video["height"], video["codec_name"]))
            self.assertTrue(any(s.get("codec_name") == "aac" for s in streams))
            self.assertAlmostEqual(3, float(data["format"].get("duration", 0)), delta=0.2)


if __name__ == "__main__":
    unittest.main()
