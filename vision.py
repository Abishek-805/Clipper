"""Optional local semantic scoring; imports large ML dependencies only on demand."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from diagnostics import ffmpeg_path
from model_manager import gpu_memory_usage

MODEL_ID = "HuggingFaceTB/SmolVLM2-500M-Video-Instruct"
MODEL_REVISION = "7b375e1b73b11138ff12fe22c8f2822d8fe03467"
PROMPT_VERSION = "candidate-v1"
SCHEMA_VERSION = 1
ALLOWED_CATEGORIES = {"ACTION_COMBAT", "COMEDY_REACTION", "EMOTIONAL", "SUSPENSE_TENSION",
                      "DIALOGUE_CONFRONTATION", "TRANSFORMATION_REVEAL", "VISUALLY_INTERESTING", "OTHER", "UNCERTAIN"}
FRAME_FRACTIONS = (0.0, 0.2, 0.4, 0.6, 0.8, 0.96)
REQUIRED_MODEL_FILES = ("config.json", "model.safetensors", "preprocessor_config.json", "chat_template.json",
                        "processor_config.json", "tokenizer.json", "tokenizer_config.json")


def cached_model_path() -> Path:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        root = Path(HF_HUB_CACHE)
    except ImportError:
        root = Path(os.environ.get("HF_HUB_CACHE", Path.home() / ".cache/huggingface/hub"))
    return root / "models--HuggingFaceTB--SmolVLM2-500M-Video-Instruct" / "snapshots" / MODEL_REVISION


def verify_model_snapshot(path: Path) -> bool:
    return path.is_dir() and all((path / filename).is_file() for filename in REQUIRED_MODEL_FILES)


def vision_installation_status() -> dict:
    missing = [name for name, module in (("transformers", "transformers"), ("accelerate", "accelerate"),
                                          ("Pillow", "PIL"), ("huggingface_hub", "huggingface_hub"))
               if importlib.util.find_spec(module) is None]
    torch_missing = importlib.util.find_spec("torch") is None
    snapshot = cached_model_path()
    weights_ready = verify_model_snapshot(snapshot)
    if missing or torch_missing or not weights_ready:
        device = "unknown"
    else:
        import torch
        device = "CUDA" if torch.cuda.is_available() else "CPU fallback"
    return {"missing_dependencies": missing, "torch_missing": torch_missing,
            "weights_verified": weights_ready, "snapshot": str(snapshot), "device": device,
            "ready": not missing and not torch_missing and weights_ready}


def semantic_cache_key(source: Path, candidate: dict, transcript: str, language: str | None,
                       frame_settings: tuple = FRAME_FRACTIONS) -> str:
    stat = source.stat()
    values = {"source": str(source.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
              "interval": [round(candidate["start"], 3), round(candidate["end"], 3)],
              "model": MODEL_ID, "revision": MODEL_REVISION, "prompt": PROMPT_VERSION,
              "frames": frame_settings, "schema": SCHEMA_VERSION,
              "transcript": hashlib.sha256(transcript.encode("utf-8")).hexdigest(), "language": language}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def read_semantic_cache(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value.get("schema") == SCHEMA_VERSION and isinstance(value.get("entries"), dict) else {"schema": SCHEMA_VERSION, "entries": {}}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {"schema": SCHEMA_VERSION, "entries": {}}


def write_semantic_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def extract_candidate_frames(source: Path, start: float, end: float, cancel=None) -> tuple[list[dict], Path]:
    directory = Path(tempfile.mkdtemp(prefix="clipper-vision-"))
    frames = []
    try:
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            raise FileNotFoundError("FFmpeg is required for semantic frame sampling")
        duration = end - start
        for index, fraction in enumerate(FRAME_FRACTIONS):
            if cancel and cancel.is_set():
                raise InterruptedError("Processing cancelled")
            timestamp = min(end - 0.05, start + duration * fraction)
            target = directory / f"frame_{index:02}.jpg"
            result = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(source),
                                     "-frames:v", "1", "-vf", "scale='min(512,iw)':-2", "-q:v", "4", str(target)],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode or not target.is_file() or target.stat().st_size < 1000:
                continue
            import cv2
            image = cv2.imread(str(target), cv2.IMREAD_GRAYSCALE)
            if image is None or image.size == 0:
                target.unlink(missing_ok=True)
                continue
            if float(image.mean()) < 2.0 or float(image.std()) < 1.0:
                target.unlink(missing_ok=True)
                continue
            digest = hashlib.sha1(image.tobytes()).hexdigest()
            if any(item["hash"] == digest for item in frames):
                target.unlink(missing_ok=True)
                continue
            frames.append({"path": target, "timestamp": round(timestamp, 3), "width": image.shape[1], "height": image.shape[0], "hash": digest})
        return frames, directory
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def parse_semantic_response(text: str, frame_timestamps: list[float]) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("Model response did not contain a JSON object")
    data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object")
    category = data.get("category")
    score = data.get("score")
    evidence = data.get("evidence", [])
    if category not in ALLOWED_CATEGORIES or isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
        raise ValueError("Model category or score is invalid")
    if not isinstance(evidence, list) or any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                                             not any(abs(value - t) <= 0.02 for t in frame_timestamps) for value in evidence):
        raise ValueError("Evidence timestamps must match sampled frames")
    reason = data.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 500:
        raise ValueError("Model reason is invalid")
    return {"category": category, "score": round(float(score), 4), "reason": reason,
            "evidence": [round(float(value), 3) for value in evidence], "calibration": "uncalibrated_model_score"}


def build_vision_messages(frames: list[dict], transcript: str = "", language: str | None = None,
                          retry: bool = False) -> list[dict]:
    content = []
    for frame in frames:
        content.extend(([{"type": "text", "text": f"Frame at {frame['timestamp']:.3f} seconds."},
                         {"type": "image", "path": str(frame["path"])}]))
    prompt = (f"Transcript (English only, may be imperfect): {transcript[:1800]}\n" if language == "en" else "")
    prompt += ('Return only JSON: {"category":"...","score":0.0,"reason":"...","evidence":[timestamps]}. '
               "Describe visible action/mood; use UNCERTAIN when unclear. Scores are 0 to 1.")
    if retry:
        prompt += " Your previous response was invalid; return a valid object using only supplied frame timestamps."
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


class VisionModelManager:
    MIN_FREE_MIB = 2200

    def __init__(self, allow_download: bool = False, status_callback=None, loader=None):
        self.allow_download = allow_download
        self.status_callback = status_callback
        self.loader = loader
        self.model = self.processor = None
        self.device = "unloaded"
        self._status = {"state": "unloaded", "model": MODEL_ID, "revision": MODEL_REVISION}

    def _notify(self, **values):
        self._status.update(values)
        if self.status_callback:
            self.status_callback(dict(self._status))

    def status(self):
        return {**self._status, "loaded": self.model is not None,
                "cached": verify_model_snapshot(cached_model_path()), "gpu": gpu_memory_usage()}

    def _model_path(self) -> Path:
        from huggingface_hub import snapshot_download
        patterns = ["*.json", "*.model", "*.txt", "*.safetensors", "*.py", "*.tiktoken"]
        try:
            path = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_files_only=True, allow_patterns=patterns))
            if not verify_model_snapshot(path):
                raise FileNotFoundError(f"Pinned model snapshot is incomplete: {path}")
            return path
        except Exception as exc:
            if not self.allow_download:
                raise FileNotFoundError("SmolVLM2 weights are not cached. Use the explicit --allow-model-download setup action.") from exc
            path = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_files_only=False, allow_patterns=patterns))
            if not verify_model_snapshot(path):
                raise FileNotFoundError(f"Downloaded pinned model snapshot is incomplete: {path}")
            return path

    def initialize(self):
        if self.model is not None:
            return self.status()
        gpu = gpu_memory_usage()
        device = "cuda" if gpu and gpu["free_mib"] >= self.MIN_FREE_MIB else "cpu"
        self._notify(state="loading", device=device, vram=gpu)
        try:
            model_path = self._model_path()
            if self.loader:
                self.processor, self.model = self.loader(model_path, device)
            else:
                import torch
                from transformers import AutoModelForImageTextToText, AutoProcessor
                dtype = torch.float16 if device == "cuda" else torch.float32
                self.processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
                self.model = AutoModelForImageTextToText.from_pretrained(
                    model_path, torch_dtype=dtype, attn_implementation="sdpa", local_files_only=True)
                self.model.to(device).eval()
            self.device = device
            precision = str(getattr(self.model, "dtype", "float16" if device == "cuda" else "float32"))
            self._notify(state="loaded", device=device, precision=precision, vram_after_load=gpu_memory_usage())
            return self.status()
        except Exception as exc:
            self.release()
            if device == "cuda" and ("out of memory" in str(exc).lower() or "cuda" in str(exc).lower()):
                self._notify(state="cpu_retry", reason=str(exc))
                model_path = self._model_path()
                if self.loader:
                    self.processor, self.model = self.loader(model_path, "cpu")
                else:
                    import torch
                    from transformers import AutoModelForImageTextToText, AutoProcessor
                    self.processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
                    self.model = AutoModelForImageTextToText.from_pretrained(model_path, torch_dtype=torch.float32,
                                                                             attn_implementation="sdpa", local_files_only=True).to("cpu").eval()
                self.device = "cpu"
                self._notify(state="loaded", device="cpu", precision="float32", fallback_reason=str(exc))
                return self.status()
            self._notify(state="unavailable", reason=f"{type(exc).__name__}: {exc}")
            raise

    def analyze(self, frames: list[dict], transcript: str = "", language: str | None = None) -> dict:
        try:
            return self._analyze(frames, transcript, language)
        except Exception as exc:
            if self.device != "cuda" or "out of memory" not in str(exc).lower():
                raise
            self.release()
            model_path = self._model_path()
            if self.loader:
                self.processor, self.model = self.loader(model_path, "cpu")
            else:
                import torch
                from transformers import AutoModelForImageTextToText, AutoProcessor
                self.processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
                self.model = AutoModelForImageTextToText.from_pretrained(model_path, torch_dtype=torch.float32,
                                                                         attn_implementation="sdpa", local_files_only=True).to("cpu").eval()
            self.device = "cpu"
            self._notify(state="loaded", device="cpu", precision="float32", fallback_reason=f"CUDA inference ran out of memory: {exc}")
            return self._analyze(frames, transcript, language)

    def _analyze(self, frames: list[dict], transcript: str = "", language: str | None = None) -> dict:
        self.initialize()
        for attempt in range(2):
            if self.loader:
                response = self.model(frames, transcript, language)
            else:
                import torch
                messages = build_vision_messages(frames, transcript, language, retry=bool(attempt))
                inputs = self.processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=True,
                                                            return_dict=True, return_tensors="pt")
                inputs = prepare_model_inputs(inputs, self.model)
                with torch.inference_mode():
                    generated = self.model.generate(**inputs, max_new_tokens=128, do_sample=False)
                response = self.processor.batch_decode(generated[:, inputs["input_ids"].shape[-1]:], skip_special_tokens=True)[0]
            try:
                return parse_semantic_response(response if isinstance(response, str) else str(response), [f["timestamp"] for f in frames])
            except (ValueError, json.JSONDecodeError):
                if attempt:
                    raise
        raise ValueError("Semantic response validation failed")

    def release(self):
        self.model = self.processor = None
        try:
            import gc
            gc.collect()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        self.device = "unloaded"
        self._notify(state="unloaded", vram_after_release=gpu_memory_usage())


def prepare_model_inputs(inputs, model) -> dict:
    """Move processor tensors without changing integer token or mask dtypes."""
    if not hasattr(inputs, "items") or not hasattr(model, "device"):
        raise TypeError("Processor must return a mapping and model must expose its device")
    prepared = {}
    for key in ("input_ids", "attention_mask", "pixel_attention_mask"):
        value = inputs.get(key)
        if value is not None and (not hasattr(value, "to") or value.is_floating_point()):
            raise TypeError(f"Processor {key} must be an integer or boolean tensor")
    pixels = inputs.get("pixel_values")
    if pixels is None or not hasattr(pixels, "to") or not pixels.is_floating_point():
        raise TypeError("Processor pixel_values must be a floating-point tensor")
    for key, value in inputs.items():
        if not hasattr(value, "to"):
            prepared[key] = value
        elif key == "pixel_values" and value.is_floating_point():
            prepared[key] = value.to(device=model.device, dtype=model.dtype)
        else:
            prepared[key] = value.to(device=model.device)
    for required in ("input_ids", "attention_mask", "pixel_values"):
        if required not in prepared:
            raise ValueError(f"Processor output is missing {required}")
    return prepared

