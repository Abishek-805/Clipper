"""Lazy, single-instance Faster-Whisper lifecycle and hardware diagnostics."""
from __future__ import annotations

import gc
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


def gpu_memory_usage() -> dict | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        line = result.stdout.strip().splitlines()[0]
        index, name, total, free, used = [part.strip() for part in line.split(",", 4)]
        return {"index": int(index), "name": name, "total_mib": int(total), "free_mib": int(free), "used_mib": int(used)}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class _MemorySampler:
    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.peak: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self):
        while not self._stop.is_set():
            current = gpu_memory_usage()
            if current and (self.peak is None or current["used_mib"] > self.peak["used_mib"]):
                self.peak = current
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        current = gpu_memory_usage()
        if current and (self.peak is None or current["used_mib"] > self.peak["used_mib"]):
            self.peak = current


class ModelManager:
    """Loads at most one Whisper instance and serializes its inference calls."""

    MODES = {"auto", "cuda-int8_float16", "cuda-float16", "cpu-int8"}
    FP16_MIN_FREE_MIB = 2048

    def __init__(self, model_size: str = "small", mode: str = "auto", local_files_only: bool = False,
                 status_callback: Callable[[dict], None] | None = None):
        if mode not in self.MODES:
            raise ValueError(f"Unsupported Whisper mode: {mode}")
        self.model_size = model_size
        self.mode = mode
        self.local_files_only = local_files_only
        self.status_callback = status_callback
        self._lock = threading.RLock()
        self._model = None
        self._loaded_key: tuple | None = None
        self._status = {
            "state": "unloaded", "model": model_size, "device": "unloaded", "compute_type": "unloaded",
            "cuda_available": False, "cuda_error": "", "fallback_reason": "", "init_seconds": None,
            "vram_before": None, "vram_after_load": None, "vram_after_release": None,
            "last_peak_vram": None, "last_transcription_seconds": None,
        }

    def _notify(self):
        if self.status_callback:
            self.status_callback(self.get_status())

    def get_memory_usage(self) -> dict | None:
        return gpu_memory_usage()

    def get_hardware_info(self) -> dict:
        info = {"cuda_available": False, "cuda_devices": 0, "cuda_compute_types": [], "cpu_compute_types": [],
                "gpu": self.get_memory_usage(), "ctranslate2_version": None}
        try:
            import ctranslate2
            info["ctranslate2_version"] = getattr(ctranslate2, "__version__", "unknown")
            info["cuda_devices"] = int(ctranslate2.get_cuda_device_count())
            info["cuda_available"] = info["cuda_devices"] > 0
            info["cpu_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cpu"))
            if info["cuda_available"]:
                info["cuda_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cuda"))
        except Exception as exc:
            info["cuda_error"] = f"{type(exc).__name__}: {exc}"
        return info

    def get_status(self) -> dict:
        with self._lock:
            return {**self._status, "loaded": self._model is not None}

    @staticmethod
    def model_identity(model_size: str) -> str:
        try:
            import faster_whisper
            version = getattr(faster_whisper, "__version__", "unknown")
        except ImportError:
            version = "unavailable"
        return f"Systran/faster-whisper-{model_size}@faster-whisper-{version}"

    def _whisper_model_class(self):
        from faster_whisper import WhisperModel
        return WhisperModel

    def _load(self, device: str, compute_type: str):
        self._status.update(state="loading", device=device, compute_type=compute_type)
        self._notify()
        started = time.perf_counter()
        self._model = self._whisper_model_class()(
            self.model_size, device=device, compute_type=compute_type, num_workers=1,
            local_files_only=self.local_files_only,
        )
        self._loaded_key = (self.model_size, device, compute_type)
        self._status.update(state="loaded", device=device, compute_type=compute_type,
                            init_seconds=round(time.perf_counter() - started, 3),
                            vram_after_load=self.get_memory_usage())
        self._notify()

    def initialize(self, model_size: str | None = None) -> dict:
        with self._lock:
            if model_size and model_size != self.model_size:
                self.release_whisper()
                self.model_size = model_size
                self._status["model"] = model_size
            if self._model is not None and self._loaded_key:
                return self.get_status()

            hardware = self.get_hardware_info()
            self._status.update(cuda_available=hardware["cuda_available"], cuda_error=hardware.get("cuda_error", ""),
                                vram_before=hardware["gpu"], fallback_reason="")
            supported = set(hardware["cuda_compute_types"])
            if self.mode == "cpu-int8":
                device, compute_type = "cpu", "int8"
            elif self.mode == "cuda-int8_float16":
                if not hardware["cuda_available"] or "int8_float16" not in supported:
                    raise RuntimeError("CUDA int8_float16 is not supported by the detected runtime")
                device, compute_type = "cuda", "int8_float16"
            elif self.mode == "cuda-float16":
                gpu = hardware["gpu"] or {}
                if not hardware["cuda_available"] or "float16" not in supported:
                    raise RuntimeError("CUDA float16 is not supported by the detected runtime")
                if gpu.get("free_mib", 0) < self.FP16_MIN_FREE_MIB:
                    raise RuntimeError(f"CUDA float16 not attempted: need at least {self.FP16_MIN_FREE_MIB} MiB free VRAM")
                device, compute_type = "cuda", "float16"
            elif hardware["cuda_available"] and "int8_float16" in supported:
                device, compute_type = "cuda", "int8_float16"
            elif hardware["cuda_available"] and "float16" in supported and (hardware["gpu"] or {}).get("free_mib", 0) >= self.FP16_MIN_FREE_MIB:
                device, compute_type = "cuda", "float16"
            else:
                device, compute_type = "cpu", "int8"
                self._status["fallback_reason"] = hardware.get("cuda_error") or "CUDA_UNAVAILABLE: no usable CUDA compute mode; using CPU int8"

            try:
                self._load(device, compute_type)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._status["cuda_error"] = error
                self._model = None
                self._loaded_key = None
                gc.collect()
                if device != "cuda" or self.mode != "auto":
                    self._status.update(state="failed", fallback_reason=error)
                    self._notify()
                    raise
                self._status["fallback_reason"] = f"CUDA {compute_type} initialization failed ({error}); using CPU int8"
                self._notify()
                try:
                    self._load("cpu", "int8")
                except Exception as cpu_exc:
                    self._status.update(state="failed", fallback_reason=f"{self._status['fallback_reason']}; CPU int8 failed ({type(cpu_exc).__name__}: {cpu_exc})")
                    self._notify()
                    raise
            return self.get_status()

    def get_whisper(self, model_size: str | None = None):
        self.initialize(model_size)
        with self._lock:
            return self._model

    def load_whisper(self, model_size: str | None = None):
        return self.initialize(model_size)

    @staticmethod
    def _is_cuda_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(token in message for token in ("cuda", "cublas", "cudnn", "out of memory", "device-side", "alloc_failed"))

    def _run_once(self, audio, cancelled: Callable[[], bool] | None, options: dict):
        model = self.get_whisper()
        sampler = _MemorySampler()
        started = time.perf_counter()
        with sampler:
            segments, info = model.transcribe(audio, **options)
            consumed = []
            for segment in segments:
                if cancelled and cancelled():
                    raise InterruptedError("Transcription cancelled")
                consumed.append(segment)
        elapsed = time.perf_counter() - started
        self._status.update(last_peak_vram=sampler.peak, last_transcription_seconds=round(elapsed, 3))
        self._notify()
        return consumed, info, elapsed, sampler.peak

    def transcribe(self, audio, cancelled: Callable[[], bool] | None = None, **options):
        """Consume Faster-Whisper's lazy segment generator while holding the model lock."""
        with self._lock:
            if cancelled and cancelled():
                raise InterruptedError("Transcription cancelled")
            try:
                return self._run_once(audio, cancelled, options)
            except InterruptedError:
                raise
            except Exception as exc:
                if self._status.get("device") != "cuda" or not self._is_cuda_error(exc):
                    raise
                cuda_error = f"{type(exc).__name__}: {exc}"
                self._status["cuda_error"] = cuda_error
                self._status["fallback_reason"] = f"CUDA transcription failed ({cuda_error}); retrying once with CPU int8"
                self.release_whisper()
                self._notify()
                self._load("cpu", "int8")
                if cancelled and cancelled():
                    raise InterruptedError("Transcription cancelled")
                return self._run_once(audio, cancelled, options)

    def release_whisper(self) -> dict | None:
        with self._lock:
            self._model = None
            self._loaded_key = None
            gc.collect()
            after = self.get_memory_usage()
            self._status.update(state="unloaded", vram_after_release=after)
            self._notify()
            return after

    def unload_all(self):
        return self.release_whisper()
