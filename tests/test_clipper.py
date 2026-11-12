import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from clipper import VideoInfo, Word, _srt, probe, rank_clips, render, scan_videos


class ClipperTests(unittest.TestCase):
    def test_scan_recurses_and_filters_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "nested" / "episode.MKV").touch()
            (root / "readme.txt").touch()
            self.assertEqual(["episode.MKV"], [p.name for p in scan_videos(root)])

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


if __name__ == "__main__":
    unittest.main()
