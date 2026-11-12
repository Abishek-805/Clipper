"""Optional vision diagnostic. Never searches for videos or downloads without the explicit flag."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from model_manager import gpu_memory_usage
from vision import MODEL_ID, MODEL_REVISION, VisionModelManager, extract_candidate_frames


def benchmark(video: Path | None = None, ground_truth: Path | None = None, allow_model_download: bool = False) -> dict:
    manager = VisionModelManager(allow_download=allow_model_download)
    result = {"model": MODEL_ID, "revision": MODEL_REVISION, "hardware": gpu_memory_usage(),
              "dependencies": {}, "model_status": "NOT RUN", "video": str(video.resolve()) if video else None,
              "real_inference": "NOT RUN", "quality_metrics": "NOT RUN", "reason": None}
    for dependency in ("torch", "transformers", "accelerate", "PIL"):
        try:
            module = __import__(dependency)
            result["dependencies"][dependency] = getattr(module, "__version__", "available")
        except ImportError:
            result["dependencies"][dependency] = "missing"
    if video is None:
        result["reason"] = "Pass --video with one explicit local video path to run inference."
        return result
    if not video.is_file():
        raise FileNotFoundError(video)
    try:
        start = time.perf_counter()
        status = manager.initialize()
        result.update(model_status="LOADED", device=status.get("device"), vram_after_load=status.get("vram_after_load"))
        frames, folder = extract_candidate_frames(video, 0, min(30, 60))
        try:
            if len(frames) < 2:
                raise ValueError("Fewer than two distinct usable frames were extracted")
            prediction = manager.analyze(frames)
        finally:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
        result.update(real_inference="PASSED", prediction=prediction, frames_used=len(frames),
                      inference_seconds=round(time.perf_counter() - start, 3), vram_after_inference=gpu_memory_usage())
        if ground_truth:
            labels = json.loads(ground_truth.read_text(encoding="utf-8"))
            expected = labels.get("category")
            result["quality_metrics"] = {"category_accuracy": prediction["category"] == expected,
                                         "expected_category": expected, "predicted_category": prediction["category"]}
    except Exception as exc:
        result.update(model_status="UNAVAILABLE", real_inference="NOT RUN", reason=f"{type(exc).__name__}: {exc}")
    finally:
        manager.release()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, help="One explicit local video file; directories are rejected")
    parser.add_argument("--ground-truth", type=Path, help="JSON with a category label for the supplied video")
    parser.add_argument("--allow-model-download", action="store_true", help="Explicitly authorize downloading pinned model files")
    parser.add_argument("--json", type=Path, default=Path("reports/task-04-benchmark.json"))
    parser.add_argument("--markdown", type=Path, default=Path("reports/task-04-benchmark.md"))
    args = parser.parse_args()
    if args.video and args.video.is_dir():
        parser.error("--video must be a single file; directory scanning is not supported")
    result = benchmark(args.video, args.ground_truth, args.allow_model_download)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    args.markdown.write_text("# Task 04 vision benchmark\n\n" + "\n".join(f"- **{key}:** {value}" for key, value in result.items()) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
