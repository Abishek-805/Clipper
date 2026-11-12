"""Optional VLM diagnostics. Samples only an explicit video or generated test images."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

from model_manager import gpu_memory_usage
from vision import (MODEL_ID, MODEL_REVISION, VisionModelManager, extract_candidate_frames,
                    vision_installation_status)


class _PeakGpuSampler:
    def __init__(self):
        self.peak = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.is_set():
            memory = gpu_memory_usage()
            if memory and (self.peak is None or memory["used_mib"] > self.peak["used_mib"]):
                self.peak = memory
            self.stop.wait(.25)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.thread.join(timeout=2)


def _dependencies() -> dict:
    import importlib.util
    return {name: importlib.util.find_spec(module) is not None for name, module in
            (("torch", "torch"), ("transformers", "transformers"), ("accelerate", "accelerate"),
             ("Pillow", "PIL"), ("huggingface_hub", "huggingface_hub"))}


def _synthetic_frames(folder: Path) -> list[dict]:
    import cv2
    import numpy as np
    frames = []
    for index, color in enumerate(((30, 80, 210), (30, 180, 70))):
        image = np.zeros((256, 256, 3), dtype=np.uint8)
        cv2.rectangle(image, (30 + index * 30, 40), (220, 220), color, -1)
        cv2.circle(image, (128, 128), 45, (240, 240, 240), -1)
        path = folder / f"synthetic_{index}.jpg"
        if not cv2.imwrite(str(path), image):
            raise RuntimeError("Could not create a synthetic inference image")
        frames.append({"path": path, "timestamp": float(index), "width": 256, "height": 256})
    return frames


def benchmark(video: Path | None = None, ground_truth: Path | None = None, synthetic_smoke: bool = False) -> dict:
    if video and synthetic_smoke:
        raise ValueError("Choose either --video or --synthetic-smoke")
    if video and not video.is_file():
        raise FileNotFoundError(video)
    if not video and not synthetic_smoke:
        installation = vision_installation_status()
        return {"model": MODEL_ID, "revision": MODEL_REVISION, "hardware": gpu_memory_usage(),
                "dependencies": _dependencies(), "weights_status": "VERIFIED" if installation["weights_verified"] else "MISSING_OR_UNVERIFIED",
                "model_status": "NOT RUN", "real_inference": "NOT RUN",
                "quality_metrics": "NOT RUN", "reason": "Pass one explicit --video or use --synthetic-smoke."}
    manager = VisionModelManager()
    result = {"model": MODEL_ID, "revision": MODEL_REVISION, "hardware_before": gpu_memory_usage(),
              "dependencies": _dependencies(), "video": str(video.resolve()) if video else None,
              "sample_type": "synthetic_images" if synthetic_smoke else "explicit_video",
              "model_status": "NOT RUN", "real_inference": "NOT RUN", "quality_metrics": "NOT RUN"}
    installation = vision_installation_status()
    result["weights_status"] = "VERIFIED" if installation["weights_verified"] else "MISSING_OR_UNVERIFIED"
    missing = [name for name, available in result["dependencies"].items() if not available]
    if synthetic_smoke:
        with tempfile.TemporaryDirectory(prefix="clipper-vision-smoke-") as tempdir:
            result["synthetic_images_generated"] = len(_synthetic_frames(Path(tempdir)))
    if missing:
        result.update(model_status="DEPENDENCIES_MISSING", reason="Missing: " + ", ".join(missing))
        return result
    if not installation["weights_verified"]:
        result.update(model_status="WEIGHTS_MISSING", reason="Use the separately confirmed setup_vision_model.py --allow-download command first.")
        if synthetic_smoke:
            with tempfile.TemporaryDirectory(prefix="clipper-vision-smoke-") as tempdir:
                result["synthetic_images_generated"] = len(_synthetic_frames(Path(tempdir)))
        return result
    temp = tempfile.TemporaryDirectory(prefix="clipper-vision-smoke-") if synthetic_smoke else None
    frame_folder = Path(temp.name) if temp else None
    try:
        if synthetic_smoke:
            frames = _synthetic_frames(frame_folder)
        else:
            from clipper import probe
            duration = probe(video).duration
            if duration < .1:
                raise ValueError("Video is too short to sample")
            frames, frame_folder = extract_candidate_frames(video, 0, min(30, duration))
        result["frames_processed"] = len(frames)
        result["vram_before_load"] = gpu_memory_usage()
        with _PeakGpuSampler() as sampler:
            init_started = time.perf_counter()
            status = manager.initialize()
            result["initialization_seconds"] = round(time.perf_counter() - init_started, 3)
            result.update(model_status="LOADED", device=status.get("device"), precision=status.get("precision"),
                          vram_after_load=status.get("vram_after_load"))
            inference_started = time.perf_counter()
            prediction = manager.analyze(frames)
            result["inference_seconds"] = round(time.perf_counter() - inference_started, 3)
        result.update(real_inference="PASSED", prediction=prediction,
                      predicted_category=prediction["category"], json_valid=True,
                      peak_vram=sampler.peak, vram_after_inference=gpu_memory_usage())
        if ground_truth:
            labels = json.loads(ground_truth.read_text(encoding="utf-8"))
            expected = labels.get("category")
            result["quality_metrics"] = {"category_accuracy": prediction["category"] == expected,
                                         "expected_category": expected, "predicted_category": prediction["category"]}
    except Exception as exc:
        status = manager.status()
        result.update(model_status="INITIALIZATION_OR_INFERENCE_FAILED", reason=f"{type(exc).__name__}: {exc}",
                      failure_state=status.get("state"), device=status.get("device"), precision=status.get("precision"))
    finally:
        manager.release()
        if frame_folder:
            shutil.rmtree(frame_folder, ignore_errors=True)
        if temp:
            temp.cleanup()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, help="One explicit local video file; directories are rejected")
    parser.add_argument("--synthetic-smoke", action="store_true", help="Generate two local images and run real model inference if weights are cached")
    parser.add_argument("--ground-truth", type=Path, help="JSON with a category label for the supplied video")
    parser.add_argument("--json", type=Path, default=Path("reports/task-04-benchmark.json"))
    parser.add_argument("--markdown", type=Path, default=Path("reports/task-04-benchmark.md"))
    args = parser.parse_args()
    if args.video and args.video.is_dir():
        parser.error("--video must be a single file; directory scanning is not supported")
    result = benchmark(args.video, args.ground_truth, args.synthetic_smoke)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    args.markdown.write_text("# Task 04 vision benchmark\n\n" + "\n".join(f"- **{key}:** {value}" for key, value in result.items()) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
