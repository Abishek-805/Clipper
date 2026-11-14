import json
import re
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import clipper
from clipper import TranscriptionResult, Word, _ass_text, _scene_cuts, _srt, parse_embedded_srt, probe, process_video, rank_clips, render, scan_videos, transcribe, validate_srt
from diagnostics import collect_diagnostics, ffmpeg_path, ffprobe_path, verify_encoder


class ClipperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = ffmpeg_path()
        cls.ffprobe = ffprobe_path()

    def _make_video(self, folder: Path, with_audio: bool, duration: float = 2.0) -> Path:
        if not self.ffmpeg:
            self.skipTest("FFmpeg unavailable")
        path = folder / ("with_audio.mp4" if with_audio else "silent.mp4")
        cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=24:duration={duration}"]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "aac", "-shortest"] if with_audio else ["-an"]
        cmd.append(str(path))
        _run(cmd)
        return path

    def test_explicit_heuristic_session_does_not_initialize_whisper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "audio.mp4"
            source.write_bytes(b"fixture")
            manager = types.SimpleNamespace(
                model_identity=lambda _model: "cached-model",
                transcribe=lambda *_args, **_kwargs: self.fail("Whisper inference must not start"),
            )
            result = transcribe(source, True, "small", root / "transcription.json", threading.Event(),
                                model_manager=manager, allow_inference=False)
            self.assertEqual("INFERENCE_DISABLED", result.outcome)
            self.assertEqual("unavailable", result.status)

    def test_scan_recurses_and_filters_extensions_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "nested" / "episode.MKV").touch()
            (root / "output").mkdir()
            (root / "output" / "finished.mp4").touch()
            (root / "readme.txt").touch()
            self.assertEqual(["episode.MKV"], [p.name for p in scan_videos(root, root / "output")])

    def test_candidate_ranking_and_boundaries(self):
        motion, audio = [0.0] * 120, [0.0] * 120
        motion[20:30], audio[70:80] = [8.0] * 10, [5.0] * 10
        words = [Word(19.7, 20.0, "before"), Word(24, 24.2, "inside"), Word(45, 45.2, "after")]
        clips = rank_clips(120, motion, audio, [20, 75], words, count=2, min_length=20, max_length=25)
        self.assertEqual(2, len(clips))
        self.assertTrue(all(0 <= c["start"] < c["end"] <= 120 for c in clips))
        self.assertTrue(all(c["end"] - c["start"] <= 25 and "reason" in c and "score" in c for c in clips))

    def test_srt_generation_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.srt"
            path.write_text(_srt([Word(11, 11.5, " hello"), Word(11.5, 12, " world.")], 10, 20), encoding="utf-8")
            self.assertIn("00:00:01,000 --> 00:00:02,000", path.read_text())
            self.assertIn("hello world.", path.read_text())
            self.assertEqual(1, validate_srt(path, 10, allow_empty=False))
            path.write_text("1\n00:00:09,000 --> 00:00:12,000\nlate\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "timestamps"):
                validate_srt(path, 10)

    def test_subtitle_formatters_preserve_whisper_token_spacing(self):
        words = [Word(1.0, 1.3, " Akihiko"), Word(1.3, 1.6, " Kaya"),
                 Word(1.6, 1.8, "-la"), Word(1.8, 2.1, " created")]
        self.assertIn("Akihiko Kaya-la created", _srt(words, 0, 3))
        ass = _ass_text(words, 0, 3)
        visible_text = re.sub(r"\{\\kf\d+\}", "", ass)
        self.assertIn("Akihiko Kaya-la created", visible_text)
        self.assertNotIn("Kaya -la", visible_text)
        ass_cues = [re.sub(r"\{[^}]*\}", "", line.split(",,")[-1]).replace(r"\N", "\n")
                    for line in visible_text.splitlines() if line.startswith("Dialogue:")]
        srt_cues = ["\n".join(cue.splitlines()[2:]) for cue in _srt(words, 0, 3).split("\n\n")]
        self.assertEqual(srt_cues, ass_cues)

    def test_glossary_replacement_matches_rendered_ass_and_srt_captions(self):
        glossary = [{"from": "Kaya -la", "to": "Kayaba", "whole_word": True, "case_sensitive": False}]
        words = [Word(.1, .3, " Kaya"), Word(.3, .5, "-la")]
        srt = _srt(words, 0, 1, glossary=glossary)
        ass = _ass_text(words, 0, 1, glossary=glossary)
        visible_ass = re.sub(r"\{\\kf\d+\}", "", ass)
        self.assertIn("Kayaba", srt)
        self.assertIn("Kayaba", visible_ass)
        self.assertNotIn("Kaya-la", visible_ass)

    def test_embedded_srt_cues_keep_source_times_and_split_words(self):
        words = parse_embedded_srt("1\n00:01:00,000 --> 00:01:02,000\n<font>hello world</font>\n")
        self.assertEqual(["hello", " world"], [word.text for word in words])
        self.assertEqual((60.0, 62.0), (words[0].start, words[-1].end))
        self.assertIn("hello world", _srt(words, 60, 62))

    def test_requested_caption_validation_rejects_zero_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output, srt = root / "clip.mp4", root / "clip.srt"
            output.write_bytes(b"output")
            srt.write_text("", encoding="utf-8")
            metadata = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080,
                         "height": 1920, "pix_fmt": "yuv420p", "avg_frame_rate": "30/1", "duration": "2", "start_time": "0"},
                         {"codec_type": "audio", "codec_name": "aac", "duration": "2", "start_time": "0"}],
                        "format": {"duration": "2", "format_name": "mp4"}}
            with patch("clipper._probe_output", return_value=metadata):
                with self.assertRaisesRegex(ValueError, "empty"):
                    clipper.validate_output(output, 2, True, srt, deep_verify=False, captions_required=True)

    def test_transcription_states_and_cuda_precision_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "source.mp4"
            video.write_bytes(b"sample")
            cache = Path(tmp) / "cache.json"
            self.assertEqual("no_audio_track", transcribe(video, False, "small", cache, threading.Event()).status)
            with patch.dict(sys.modules, {"faster_whisper": None}):
                self.assertEqual("unavailable", transcribe(video, True, "small", cache, threading.Event()).status)

            class FakeModel:
                def __init__(self, size, device, compute_type, **_kwargs):
                    self.device, self.precision = device, compute_type
                    if device == "cuda" and compute_type == "int8_float16":
                        raise RuntimeError("mock CUDA failure")

                def transcribe(self, *_args, **_kwargs):
                    word = types.SimpleNamespace(start=0.2, end=0.5, word="hello")
                    segment = types.SimpleNamespace(words=[word])
                    info = types.SimpleNamespace(language="en")
                    return [segment], info

            whisper = types.ModuleType("faster_whisper")
            whisper.WhisperModel = FakeModel
            ctranslate = types.ModuleType("ctranslate2")
            ctranslate.get_cuda_device_count = lambda: 1
            ctranslate.get_supported_compute_types = lambda device: {"int8"} if device == "cpu" else {"int8_float16"}
            with patch.dict(sys.modules, {"faster_whisper": whisper, "ctranslate2": ctranslate}), \
                 patch("clipper._run", return_value=types.SimpleNamespace(stdout=b"\0" * 64000, stderr=b"", returncode=0)):
                result = transcribe(video, True, "small", cache, threading.Event(), audio_index=0)
            self.assertEqual(("transcribed", "cpu", "int8"), (result.status, result.device, result.precision))
            self.assertIn("initialization failed", result.message)

    def test_scene_detection_failure_is_reported(self):
        with patch.dict(sys.modules, {"scenedetect": None}):
            cuts, status = _scene_cuts(Path("missing.mp4"))
        self.assertEqual([], cuts)
        self.assertIn("unavailable", status)

    def test_probe_rejects_corrupt_video_and_missing_ffmpeg_is_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = Path(tmp) / "corrupt.mp4"
            corrupt.write_text("not a video", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                probe(corrupt)
        with patch("clipper.ffmpeg_path", return_value=None), self.assertRaisesRegex(FileNotFoundError, "FFmpeg is required"):
            clipper._run(["ffmpeg", "-version"])

    def test_probe_rejects_video_without_video_stream(self):
        with patch("clipper._run") as run:
            run.return_value.stdout = '{"streams": [{"codec_type": "audio"}], "format": {"duration": "1"}}'
            with self.assertRaisesRegex(ValueError, "No video stream"):
                probe(Path("audio.mp3"))

    def test_diagnostics_and_encoder_fallback(self):
        report = collect_diagnostics()
        self.assertIn("FFmpeg", {item["name"] for item in report["checks"]})
        with patch("clipper.verify_encoder", side_effect=[(False, "no CUDA encoder"), (True, "")]):
            self.assertEqual(("libx264", "NVENC unavailable (no CUDA encoder); using libx264"), clipper.select_encoder("auto"))

    @unittest.skipUnless(ffmpeg_path() and ffprobe_path(), "FFmpeg/FFprobe unavailable")
    def test_render_audio_subtitles_cpu_and_output_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_video(root, True)
            output = root / "cpu.mp4"
            info = probe(source)
            words = [Word(0.25, 0.6, "hello"), Word(0.6, 1.0, " world.")]
            rendered = render(source, output, info, {"start": 0, "end": 2}, words, True, threading.Event(), "libx264")
            self.assertEqual("libx264", rendered["encoder"])
            self.assertEqual((1080, 1920, 30.0, "yuv420p"), (rendered["width"], rendered["height"], rendered["fps"], rendered["pixel_format"]))
            self.assertTrue(rendered["audio_video_sync_checked"])
            self.assertTrue(output.with_suffix(".srt").is_file())
            self.assertFalse(output.with_name("cpu.partial.mp4").exists())
            self.assertEqual(1, validate_srt(output.with_suffix(".srt"), 2, allow_empty=False))

    @unittest.skipUnless(ffmpeg_path() and ffprobe_path(), "FFmpeg/FFprobe unavailable")
    def test_render_silent_video_has_no_audio_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_video(root, False)
            output = root / "silent_output.mp4"
            rendered = render(source, output, probe(source), {"start": 0, "end": 2}, [], False, threading.Event(), "libx264", captions_required=False)
            self.assertIsNone(rendered["audio_codec"])
            self.assertFalse(rendered["audio_video_sync_checked"])

    @unittest.skipUnless(ffmpeg_path() and ffprobe_path(), "FFmpeg/FFprobe unavailable")
    def test_render_uses_shot_specific_crop_then_safe_contain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_video(root, False)
            crop = {"x": 0, "y": 0, "width": 134, "height": 240}
            plan = {"shots": [
                {"shot_id": 0, "start": 0, "end": 1, "composition": "saliency_crop", "crop": crop,
                 "focus_path": [{"sample_time": .5, "center_x": 67}], "obstructions": []},
                {"shot_id": 1, "start": 1, "end": 2, "composition": "full_frame_contain", "crop": None,
                 "focus_path": [], "obstructions": []}]}
            output = root / "shot_plan.mp4"
            result = render(source, output, probe(source), {"start": 0, "end": 2}, [], False,
                            threading.Event(), "libx264", plan,
                            [{"start": 0, "end": 1, "anchor": "lower"}, {"start": 1, "end": 2, "anchor": "upper"}],
                            captions_required=False)
            self.assertEqual(["saliency_crop", "full_frame_contain"], result["composition_modes"])
            self.assertEqual((1080, 1920), (result["width"], result["height"]))

    @unittest.skipUnless(ffmpeg_path() and ffprobe_path(), "FFmpeg/FFprobe unavailable")
    def test_nvenc_encode_if_runtime_probe_passes(self):
        usable, reason = verify_encoder("h264_nvenc")
        if not usable:
            self.skipTest(f"NVENC self-test unavailable: {reason}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_video(root, False, duration=1)
            rendered = render(source, root / "nvenc.mp4", probe(source), {"start": 0, "end": 1}, [], False, threading.Event(), "h264_nvenc", captions_required=False)
            self.assertEqual("h264_nvenc", rendered["encoder"])

    @unittest.skipUnless(ffmpeg_path() and ffprobe_path(), "FFmpeg/FFprobe unavailable")
    def test_process_video_pipeline_end_to_end_with_synthetic_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_video(root, True)
            fake = TranscriptionResult("en", [Word(0.3, 0.6, "test")], "transcribed", "test", "int8")
            progress = []
            with patch("clipper.transcribe", return_value=fake):
                count = process_video(source, root / "Shorts_Output", "tiny", 1, 1, 2, threading.Event(), progress.append, "libx264")
            self.assertEqual(1, count)
            manifest = next((root / "Shorts_Output").rglob("analysis.json"))
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual("unavailable", data["editorial_analysis"]["status"])
            self.assertIn("not configured", data["editorial_analysis"]["reason"])
            self.assertTrue((manifest.parent / "clip_review.md").is_file())
            self.assertTrue((manifest.parent / "clip_contact_sheet.png").is_file())
            self.assertEqual("transcribed", data["transcription_status"])
            self.assertEqual("libx264", data["clips"][0]["render"]["encoder"])
            self.assertIn("Scene detection:", "\n".join(progress))
            self.assertIn("Candidates ·", "\n".join(progress))
            self.assertIn("candidate_selection", data)
            self.assertEqual(1, data["candidate_selection"]["selected_count"])
            self.assertEqual((1080, 1920), (data["clips"][0]["render"]["width"], data["clips"][0]["render"]["height"]))
            self.assertGreater(data["stage_timings_by_pass"]["render"]["motion_frames_decoded"], 0)
            self.assertGreaterEqual(data["clips"][0]["render"]["validation_seconds"], 0)


def _run(args):
    import subprocess
    subprocess.run(args, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
