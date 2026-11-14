import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import clipper
from clipper import Word, ffmpeg_path, probe, rerender_single_clip


class SingleClipRerenderTests(unittest.TestCase):
    def make_job(self, root: Path):
        source = root / "source.mp4"
        args = [ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "color=c=black:s=320x240:r=24:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)]
        import subprocess
        subprocess.run(args, check=True, capture_output=True)
        job = root / "job"
        job.mkdir()
        output = job / "clip_001.mp4"
        output.write_bytes(b"original mp4")
        output.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nold caption\n", encoding="utf-8")
        stat = source.stat()
        (job / "analysis.json").write_text(json.dumps({"source_path": str(source), "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns, "settings": {"audio_stream_index": 1, "encoder": "libx264"},
            "clips": [{"filename": output.name, "start": 0.0, "end": 1.0,
                       "reframing": {"shots": [{"start": 0, "end": 1, "composition": "full_frame_contain"}]}}]}), encoding="utf-8")
        return source, job, output

    def test_identical_contain_runs_merge_without_mutating_caption_plan(self):
        info = clipper.VideoInfo(10, 320, 240, 24, False, 0)
        plan = {"shots": [{"start": 0, "end": 1, "composition": "full_frame_contain"},
                          {"start": 1, "end": 2, "composition": "full_frame_contain"}]}
        graph = clipper._render_filter(info, {"start": 0, "end": 2}, Path("caption.ass"), plan)
        self.assertNotIn("concat=", graph)
        self.assertEqual(1, graph.count("boxblur="))
        self.assertEqual(1, plan["shots"][0]["end"])
        fast = clipper._render_filter(info, {"start": 0, "end": 2}, Path("caption.ass"), plan, "fast")
        self.assertIn("crop=270:480", fast)

    @unittest.skipUnless(ffmpeg_path(), "FFmpeg unavailable")
    def test_manual_caption_is_burned_in_and_versioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, job, output = self.make_job(Path(tmp))
            result = rerender_single_clip(job, output.name, 0, 1.5,
                "1\n00:00:00,000 --> 00:00:01,000\nUniqueCaptionXYZ\n", "bottom-safe")
            self.assertEqual("clip_001.v1.mp4", result["versioned_mp4"])
            self.assertEqual("clip_001.v1.srt", result["versioned_srt"])
            self.assertTrue((job / result["versioned_mp4"]).is_file())
            srt = output.with_suffix(".srt").read_text(encoding="utf-8")
            self.assertIn("UniqueCaptionXYZ", srt)
            import cv2
            capture = cv2.VideoCapture(str(output))
            capture.set(cv2.CAP_PROP_POS_MSEC, 500)
            ok, frame = capture.read()
            capture.release()
            self.assertTrue(ok)
            self.assertGreater(int((frame > 220).all(axis=2).sum()), 50)
            self.assertEqual((1080, 1920), (probe(output).width, probe(output).height))

    @unittest.skipUnless(ffmpeg_path(), "FFmpeg unavailable")
    def test_failed_render_and_cancellation_preserve_existing_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, job, output = self.make_job(Path(tmp))
            before_mp4 = output.read_bytes()
            before_srt = output.with_suffix(".srt").read_bytes()
            def fail_validation(_source, target, *_args, **_kwargs):
                Path(target).write_bytes(b"invalid partial output")
                Path(target).with_suffix(".srt").write_text("bad", encoding="utf-8")
                raise RuntimeError("validation failed")
            with patch.object(clipper, "render", side_effect=fail_validation):
                with self.assertRaisesRegex(RuntimeError, "validation failed"):
                    rerender_single_clip(job, output.name, 0, 1.5, "edited caption")
            self.assertEqual(before_mp4, output.read_bytes())
            self.assertEqual(before_srt, output.with_suffix(".srt").read_bytes())

            cancel = threading.Event()
            def cancelled(*args, **kwargs):
                cancel.set()
                return {"ok": True}
            with patch.object(clipper, "render", side_effect=cancelled):
                with self.assertRaises(InterruptedError):
                    rerender_single_clip(job, output.name, 0, 1.5, "edited caption", cancel=cancel)
            self.assertEqual(before_mp4, output.read_bytes())
            self.assertEqual(before_srt, output.with_suffix(".srt").read_bytes())

    @unittest.skipUnless(ffmpeg_path(), "FFmpeg unavailable")
    def test_bounds_and_duration_are_validated_before_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, job, output = self.make_job(Path(tmp))
            with patch.object(clipper, "render") as render:
                for interval in ((-.1, .5), (1.5, 1)):
                    with self.assertRaises(ValueError):
                        rerender_single_clip(job, output.name, *interval, "caption")
                with self.assertRaisesRegex(ValueError, "source duration"):
                    rerender_single_clip(job, output.name, 0, 3, "caption")
                with patch.object(clipper, "probe", return_value=type("Info", (), {"duration": 200, "has_audio": True})()):
                    with self.assertRaisesRegex(ValueError, "58s limit"):
                        rerender_single_clip(job, output.name, 0, 59, "caption")
                render.assert_not_called()

    def test_rerender_rejects_captions_from_another_audio_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, job, output = self.make_job(Path(tmp))
            key = __import__("hashlib").sha256(str(source.resolve()).casefold().encode()).hexdigest()[:20]
            cache = Path(tmp) / "Clipper" / "analysis_cache" / key
            cache.mkdir(parents=True)
            settings = {"audio_stream_index": 99, "task": "transcribe"}
            (cache / "transcription.json").write_text(json.dumps({"settings": settings,
                "fingerprint": clipper._transcription_fingerprint(source, settings), "words": []}))
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch.object(clipper, "render") as render:
                with self.assertRaisesRegex(ValueError, "do not match"):
                    rerender_single_clip(job, output.name, 0, 1)
                render.assert_not_called()

    @unittest.skipUnless(ffmpeg_path(), "FFmpeg unavailable")
    def test_time_nudge_uses_cached_words_not_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, job, output = self.make_job(Path(tmp))
            key = __import__("hashlib").sha256(str(source.resolve()).casefold().encode("utf-8")).hexdigest()[:20]
            cache = Path(tmp) / "Clipper" / "analysis_cache" / key
            cache.mkdir(parents=True)
            (cache / "transcription.json").write_text(json.dumps({"words": [
                {"start": .1, "end": .4, "text": " cached"}, {"start": .4, "end": .8, "text": " word."}]}), encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch.object(clipper, "render", return_value={"validated": True}) as render:
                def fake_render(_source, target, _info, clip, words, *_args, **kwargs):
                    self.assertEqual((.1, 1.5), (clip["start"], clip["end"]))
                    self.assertEqual([" cached", " word."], [word.text for word in words])
                    self.assertIsNone(kwargs.get("caption_cues"))
                    Path(target).write_bytes(b"new mp4")
                    Path(target).with_suffix(".srt").write_text("new captions", encoding="utf-8")
                    return {"validated": True}
                render.side_effect = fake_render
                rerender_single_clip(job, output.name, .1, 1.5)
            self.assertEqual(b"new mp4", output.read_bytes())
            history = json.loads((job / "analysis.json").read_text(encoding="utf-8"))["clips"][0]["edit_history"]
            self.assertEqual((.1, 1.5), (history[0]["start"], history[0]["end"]))


if __name__ == "__main__":
    unittest.main()
