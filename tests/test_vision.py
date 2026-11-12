import tempfile
import io
import unittest
import threading
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from vision import (MODEL_REVISION, VisionModelManager, build_vision_messages, parse_semantic_response,
                    prepare_model_inputs, read_semantic_cache, semantic_cache_key, write_semantic_cache)
from setup_vision_model import download_model_snapshot


class VisionTests(unittest.TestCase):
    def test_response_schema_and_evidence_are_validated(self):
        valid = parse_semantic_response('{"category":"ACTION_COMBAT","score":0.8,"reason":"Movement","evidence":[10]}', [10])
        self.assertEqual("ACTION_COMBAT", valid["category"])
        with self.assertRaises(ValueError):
            parse_semantic_response('{"category":"ACTION","score":2,"evidence":[99]}', [10])

    def test_semantic_cache_is_atomic_and_fingerprint_changes_with_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "movie.mp4"
            source.write_bytes(b"video")
            first = semantic_cache_key(source, {"start": 1, "end": 25}, "hello", "en")
            second = semantic_cache_key(source, {"start": 2, "end": 25}, "hello", "en")
            self.assertNotEqual(first, second)
            cache = Path(tmp) / "semantic_cache.json"
            write_semantic_cache(cache, {"schema": 1, "entries": {first: {"score": .8}}})
            self.assertEqual(.8, read_semantic_cache(cache)["entries"][first]["score"])

    def test_manager_loads_only_when_initialize_is_called(self):
        calls = []
        with patch("vision.gpu_memory_usage", return_value=None), \
             patch("vision.VisionModelManager._model_path", return_value=Path("local")):
            manager = VisionModelManager(loader=lambda path, device: (calls.append((path, device)) or (object(), object())))
            self.assertEqual([], calls)
            self.assertEqual("cpu", manager.initialize()["device"])
            self.assertEqual(1, len(calls))
            manager.release()

    def test_processor_floats_cast_but_integer_tokens_and_masks_keep_dtype(self):
        class Tensor:
            def __init__(self, dtype, floating=False):
                self.dtype, self.floating, self.calls = dtype, floating, []
            def is_floating_point(self):
                return self.floating
            def to(self, **kwargs):
                self.calls.append(kwargs)
                return self
        class Model:
            device, dtype = "cuda:0", "float16"
        tensors = {"input_ids": Tensor("int64"), "attention_mask": Tensor("int64"),
                   "pixel_values": Tensor("float32", True), "pixel_attention_mask": Tensor("int64")}
        prepared = prepare_model_inputs(tensors, Model())
        self.assertEqual({"device": "cuda:0", "dtype": "float16"}, tensors["pixel_values"].calls[0])
        self.assertEqual({"device": "cuda:0"}, tensors["input_ids"].calls[0])
        self.assertEqual("int64", prepared["attention_mask"].dtype)
        self.assertEqual("int64", prepared["pixel_attention_mask"].dtype)

    def test_multiple_local_image_paths_keep_timestamp_order(self):
        paths = [Path("first.jpg"), Path("second.jpg"), Path("third.jpg")]
        messages = build_vision_messages([{"path": p, "timestamp": i} for i, p in enumerate(paths)])
        images = [item["path"] for item in messages[0]["content"] if item["type"] == "image"]
        self.assertEqual([str(path) for path in paths], images)

    def test_setup_download_requires_explicit_authorization(self):
        with self.assertRaises(PermissionError):
            download_model_snapshot(False, hub=(lambda *_args, **_kwargs: self.fail("download must not run"), Path(".")))

    def test_setup_download_checks_pinned_snapshot_and_required_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshots" / MODEL_REVISION
            snapshot.mkdir(parents=True)
            filenames = ("config.json", "model.safetensors", "preprocessor_config.json", "chat_template.json",
                         "processor_config.json", "tokenizer.json", "tokenizer_config.json")
            plan = [SimpleNamespace(commit_hash=MODEL_REVISION, filename=name, file_size=100) for name in filenames]
            calls = []
            def fake_download(_model, **kwargs):
                calls.append(kwargs)
                if kwargs.get("dry_run"):
                    return plan
                for name in filenames:
                    (snapshot / name).touch()
                return snapshot
            with patch("setup_vision_model.cached_model_path", return_value=root / "not-cached"), \
                 patch("setup_vision_model.shutil.disk_usage", return_value=SimpleNamespace(free=2**32)):
                result = download_model_snapshot(True, hub=(fake_download, root))
            self.assertEqual(snapshot, result)
            self.assertEqual([MODEL_REVISION, MODEL_REVISION], [call["revision"] for call in calls])
            self.assertEqual(MODEL_REVISION, result.name)

    def test_setup_reports_missing_huggingface_hub(self):
        import builtins
        real_import = builtins.__import__
        def without_hub(name, *args, **kwargs):
            if name == "huggingface_hub":
                raise ImportError("missing for test")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=without_hub), self.assertRaisesRegex(RuntimeError, "huggingface_hub"):
            download_model_snapshot(True)

    def test_setup_diagnostics_distinguish_missing_pytorch_and_model(self):
        from vision import vision_installation_status
        with patch("vision.importlib.util.find_spec", side_effect=lambda name: None if name in ("torch", "PIL") else object()), \
             patch("vision.cached_model_path", return_value=Path("missing")), \
             patch("vision.verify_model_snapshot", return_value=False):
            status = vision_installation_status()
        self.assertTrue(status["torch_missing"])
        self.assertFalse(status["weights_verified"])
        self.assertIn("Pillow", status["missing_dependencies"])

    def test_setup_button_invokes_download_only_script_with_explicit_flag(self):
        import main
        class Process:
            stdout = io.StringIO("Checking files\nMODEL_SNAPSHOT_VERIFIED C:/cache/snapshot\n")
            def wait(self):
                return 0
        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target
            def start(self):
                self.target()
        ready = {"missing_dependencies": [], "torch_missing": False, "weights_verified": True,
                 "ready": True, "device": "CPU fallback", "snapshot": "local"}
        with patch.object(main, "collect_diagnostics", return_value={"cuda_devices": 0}), \
             patch.object(main, "gpu_memory_usage", return_value=None), \
             patch.object(main.ClippingApp, "after", return_value=None, create=True), \
             patch.object(main.messagebox, "askyesno", return_value=True), \
             patch.object(main.subprocess, "Popen", return_value=Process()) as popen, \
             patch.object(main, "VisionModelManager") as vision_manager, \
             patch.object(main, "vision_installation_status", return_value=ready), \
             patch.object(main.threading, "Thread", ImmediateThread):
            try:
                app = main.ClippingApp()
            except main.tk.TclError as exc:
                self.skipTest(f"Tk desktop unavailable: {exc}")
            try:
                popen.assert_not_called()
                vision_manager.assert_not_called()
                app._setup_vision()
                args = popen.call_args.args[0]
                self.assertTrue(args[1].endswith("setup_vision_model.py"))
                self.assertEqual("--allow-download", args[2])
                app._poll()
                self.assertIn("downloaded and verified", app.vision_status.get().lower())
            finally:
                app.destroy()

    def test_each_semantic_source_releases_whisper_then_releases_vision(self):
        import clipper
        from clipper import TranscriptionResult, VideoInfo, process_video
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = [root / "one.mp4", root / "two.mp4"]
            for source in sources:
                source.write_bytes(b"source")
            candidate = {"start": 0.0, "end": 25.0, "duration": 25.0, "score": .5, "boundary_valid": True,
                         "feature_scores": {"average_motion": .4, "average_audio_energy": .3, "speech_density": .2,
                                            "dialogue_completeness": .2, "scene_transition_quality": .3}}
            selection = {"selected": [candidate], "candidates": [candidate], "candidate_count": 1,
                         "rejected_count": 0, "short_source_policy": "none", "activity_regions": [],
                         "speech_regions": [], "rejections": []}
            whisper = Mock()
            vision = Mock()
            frame_folder = root / "frames"
            frame_folder.mkdir()
            vision.initialize.return_value = {"device": "cpu"}
            vision.analyze.return_value = {"score": .8, "category": "ACTION_COMBAT", "reason": "visible motion", "evidence": [0.0]}
            with patch.object(clipper, "probe", return_value=VideoInfo(60, 640, 480, 30, False, 0)), \
                 patch.object(clipper, "_motion", return_value=[.5] * 60), \
                 patch.object(clipper, "_scene_cuts", return_value=([], "none")), \
                 patch.object(clipper, "transcribe", return_value=TranscriptionResult(None, [], "no_audio_track", "none", "none", outcome="NO_AUDIO")), \
                 patch.object(clipper, "select_clips", return_value=selection), \
                 patch.object(clipper, "extract_candidate_frames", return_value=([{"path": Path("a"), "timestamp": 0.0},
                                                                                    {"path": Path("b"), "timestamp": 1.0}], frame_folder)), \
                 patch.object(clipper, "render", return_value={"encoder": "libx264", "encoder_note": "mock"}):
                for source in sources:
                    process_video(source, root / "out", "tiny", 1, 20, 55, threading.Event(), lambda _msg: None,
                                  "auto", whisper, True, vision)
            self.assertEqual(2, whisper.release_whisper.call_count)
            self.assertEqual(2, vision.release.call_count)
            self.assertEqual(2, vision.analyze.call_count)
            for source in sources:
                manifest = next((root / "out").glob(f"{source.stem}_*/analysis.json"))
                data = json.loads(manifest.read_text(encoding="utf-8"))
                self.assertEqual("ACTION_COMBAT", data["clips"][0]["category"])

    def test_cuda_inference_oom_retries_once_on_cpu(self):
        attempts = []
        def loader(_path, device):
            def infer(*_args):
                attempts.append(device)
                if device == "cuda":
                    raise RuntimeError("CUDA out of memory")
                return '{"category":"UNCERTAIN","score":0.1,"reason":"unknown","evidence":[]}'
            return None, infer
        manager = VisionModelManager(loader=loader)
        frame = {"path": Path("unused.jpg"), "timestamp": 0.0}
        with patch("vision.gpu_memory_usage", return_value={"free_mib": 3000, "total_mib": 4094}), \
             patch("vision.VisionModelManager._model_path", return_value=Path("local")):
            output = manager.analyze([frame, {**frame, "timestamp": 1.0}])
        self.assertEqual(["cuda", "cpu"], attempts)
        self.assertEqual("UNCERTAIN", output["category"])
        self.assertEqual("cpu", manager.device)


if __name__ == "__main__":
    unittest.main()
