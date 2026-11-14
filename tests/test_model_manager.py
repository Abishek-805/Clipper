import json
import sys
import tempfile
import threading
import concurrent.futures
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from clipper import transcribe
from model_manager import ModelManager
import benchmark_models


class FakeModel:
    instances = []

    def __init__(self, size, device, compute_type, **_kwargs):
        self.device = device
        self.compute_type = compute_type
        self.calls = []
        self.instances.append(self)

    def transcribe(self, _audio, **options):
        self.calls.append(options)
        word = types.SimpleNamespace(start=0.1, end=0.4, word=" hello")
        segment = types.SimpleNamespace(words=[word], text="hello")
        info = types.SimpleNamespace(language="en")
        return iter([segment]), info


def whisper_module(model_class=FakeModel):
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = model_class
    module.__version__ = "test-version"
    return module


class ModelManagerTests(unittest.TestCase):
    def setUp(self):
        FakeModel.instances.clear()

    def _hardware(self, cuda=False, types_=()):
        return {"cuda_available": cuda, "cuda_devices": 1 if cuda else 0,
                "cuda_compute_types": list(types_), "cpu_compute_types": ["int8"],
                "gpu": {"total_mib": 4094, "free_mib": 3000, "used_mib": 1094} if cuda else None}

    def test_lazy_initialization_and_model_reuse(self):
        manager = ModelManager(mode="cpu-int8")
        with patch.dict(sys.modules, {"faster_whisper": whisper_module()}):
            self.assertFalse(manager.get_status()["loaded"])
            self.assertIs(manager.get_whisper(), manager.get_whisper())
            self.assertEqual(1, len(FakeModel.instances))
            self.assertEqual("int8", manager.get_status()["compute_type"])
        manager.release_whisper()
        self.assertFalse(manager.get_status()["loaded"])

    def test_cuda_initialization_failure_records_error_and_falls_back_once(self):
        class BrokenCuda(FakeModel):
            def __init__(self, size, device, compute_type, **kwargs):
                if device == "cuda":
                    raise RuntimeError("CUDA init failed: out of memory")
                super().__init__(size, device, compute_type, **kwargs)

        manager = ModelManager()
        manager.get_hardware_info = lambda: self._hardware(True, ["int8_float16", "float16"])
        manager.get_memory_usage = lambda: {"total_mib": 4094, "free_mib": 3000, "used_mib": 1094}
        with patch.dict(sys.modules, {"faster_whisper": whisper_module(BrokenCuda)}):
            manager.initialize()
        state = manager.get_status()
        self.assertEqual(("cpu", "int8"), (state["device"], state["compute_type"]))
        self.assertIn("CUDA init failed", state["fallback_reason"])
        self.assertIn("CUDA init failed", state["cuda_error"])
        self.assertEqual(1, len(FakeModel.instances))

    def test_unsupported_precision_fails_without_model_load(self):
        manager = ModelManager(mode="cuda-float16")
        manager.get_hardware_info = lambda: self._hardware(True, ["int8_float16"])
        with patch.dict(sys.modules, {"faster_whisper": whisper_module()}):
            with self.assertRaisesRegex(RuntimeError, "not supported"):
                manager.initialize()
        self.assertEqual([], FakeModel.instances)

    def test_concurrent_transcription_calls_are_serialized(self):
        state = {"active": 0, "peak": 0}

        class SlowModel(FakeModel):
            def transcribe(self, _audio, **options):
                self.calls.append(options)
                def segments():
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                    time.sleep(0.05)
                    state["active"] -= 1
                    yield types.SimpleNamespace(words=[], text="")
                return segments(), types.SimpleNamespace(language="en")

        manager = ModelManager(mode="cpu-int8")
        with patch.dict(sys.modules, {"faster_whisper": whisper_module(SlowModel)}), patch("model_manager.gpu_memory_usage", return_value=None):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: manager.transcribe("sample.wav"), range(2)))
        self.assertEqual(1, len(SlowModel.instances))
        self.assertEqual(1, state["peak"])

    def test_cancelled_transcription_has_explicit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "voice.wav"
            source.write_bytes(b"sample")
            cancelled = threading.Event()
            cancelled.set()
            result = transcribe(source, True, "small", Path(tmp) / "transcription.json", cancelled,
                                model_manager=ModelManager(mode="cpu-int8"))
        self.assertEqual("CANCELLED", result.outcome)
        self.assertEqual("cancelled", result.status)

    def test_cuda_oom_while_consuming_generator_retries_once_on_cpu(self):
        class OomGenerator(FakeModel):
            def transcribe(self, _audio, **options):
                self.calls.append(options)
                if self.device == "cuda":
                    def failed():
                        raise RuntimeError("CUDA out of memory")
                        yield None
                    return failed(), types.SimpleNamespace(language="en")
                word = types.SimpleNamespace(start=0.1, end=0.4, word="hello")
                segment = types.SimpleNamespace(words=[word], text="hello")
                return iter([segment]), types.SimpleNamespace(language="en")

        manager = ModelManager()
        manager.get_hardware_info = lambda: self._hardware(True, ["int8_float16"])
        manager.get_memory_usage = lambda: {"total_mib": 4094, "free_mib": 2500, "used_mib": 1594}
        with patch.dict(sys.modules, {"faster_whisper": whisper_module(OomGenerator)}):
            segments, info, _elapsed, _peak = manager.transcribe("sample.wav", word_timestamps=True)
        self.assertEqual(1, len(segments))
        self.assertEqual("en", info.language)
        self.assertEqual("cpu", manager.get_status()["device"])
        self.assertIn("CUDA out of memory", manager.get_status()["fallback_reason"])
        self.assertEqual(2, len(OomGenerator.instances))

    def test_cache_fingerprint_tracks_settings_and_failed_runs_do_not_replace_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, cache = root / "voice.wav", root / "transcription.json"
            source.write_bytes(b"approved-sample")
            manager = ModelManager(mode="cpu-int8")
            decoded = types.SimpleNamespace(stdout=b"\0" * 64000, stderr=b"", returncode=0)
            with patch.dict(sys.modules, {"faster_whisper": whisper_module()}), patch("clipper._run", return_value=decoded):
                first = transcribe(source, True, "small", cache, threading.Event(), model_manager=manager, audio_seconds=1, audio_index=0)
                self.assertEqual("CPU_FALLBACK", first.outcome)
                self.assertIn("fallback_reason", json.loads(cache.read_text(encoding="utf-8")))
                saved = cache.read_bytes()
                transcribe(source, True, "small", cache, threading.Event(), model_manager=manager, audio_seconds=1, audio_index=0)
                self.assertEqual(1, len(FakeModel.instances[0].calls))
                transcribe(source, True, "small", cache, threading.Event(), model_manager=manager, audio_seconds=1, language="fr", audio_index=0)
                self.assertEqual(2, len(FakeModel.instances[0].calls))
                second_source = root / "second.wav"
                second_source.write_bytes(b"another-approved-sample")
                transcribe(second_source, True, "small", root / "second.json", threading.Event(), model_manager=manager, audio_seconds=1, audio_index=0)
                self.assertEqual(1, len(FakeModel.instances))
                before_failed_run = cache.read_bytes()
                source.write_bytes(b"changed-sample")
                manager.release_whisper()

                class FailedModel(FakeModel):
                    def transcribe(self, *_args, **_kwargs):
                        raise ValueError("bad audio")

                with patch.object(manager, "_whisper_model_class", return_value=FailedModel):
                    failed = transcribe(source, True, "small", cache, threading.Event(), model_manager=manager, language="de", audio_index=0)
                self.assertEqual("TRANSCRIPTION_FAILED", failed.outcome)
                self.assertEqual(before_failed_run, cache.read_bytes())
                self.assertNotEqual(saved, before_failed_run)

    def test_gui_startup_does_not_construct_or_load_a_model_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(main, "PREFERENCES", Path(tmp) / "preferences.json"), \
                 patch.object(main, "INTERRUPTED_JOB", Path(tmp) / "job.json"), \
                 patch.object(main, "collect_diagnostics", return_value={"gpu_name": None, "cuda_devices": 0, "encoders": {}, "checks": []}), \
                 patch.object(main.ClippingApp, "after", return_value=None, create=True), \
                 patch.object(main, "ModelManager") as manager_class:
                try:
                    app = main.ClippingApp()
                except Exception as exc:
                    import tkinter
                    if isinstance(exc, tkinter.TclError):
                        self.skipTest(f"Tk desktop unavailable: {exc}")
                    raise
                try:
                    app.withdraw()
                    app.update_idletasks()
                    manager_class.assert_not_called()
                    self.assertFalse(app.running)
                finally:
                    app.destroy()

    def test_benchmark_skips_missing_local_model_without_download_or_init(self):
        with patch.object(benchmark_models, "download_model", return_value=None) as download, \
             patch.object(benchmark_models.ModelManager, "get_hardware_info", return_value=self._hardware(True, ["int8_float16"])), \
             patch.object(benchmark_models.ModelManager, "initialize") as initialize:
            result = benchmark_models.benchmark("small")
        download.assert_called_once_with("small", local_files_only=True)
        initialize.assert_not_called()
        self.assertEqual("NOT RUN", result["transcription_benchmark"])
        self.assertTrue(all(mode["status"] == "SKIPPED" for mode in result["modes"]))


if __name__ == "__main__":
    unittest.main()
