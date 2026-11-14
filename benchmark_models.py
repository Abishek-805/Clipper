"""Explicit, local-cache-only Whisper hardware and transcription benchmark."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from clipper_runtime.cuda_dlls import register_cuda_dll_directories

register_cuda_dll_directories()
from faster_whisper.utils import download_model

from model_manager import ModelManager


def cached_model_path(model: str) -> Path | None:
    try:
        return Path(download_model(model, local_files_only=True))
    except Exception:
        return None


def audio_duration(path: Path) -> float:
    import av
    with av.open(str(path)) as container:
        if container.duration:
            return float(container.duration / av.time_base)
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream and stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
    raise ValueError("Could not determine sample audio duration")


def benchmark(model: str = "small", sample: Path | None = None) -> dict:
    cache_path = cached_model_path(model)
    hardware = ModelManager(model, local_files_only=True).get_hardware_info()
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "model_identity": ModelManager.model_identity(model),
        "model_cached": cache_path is not None,
        "model_cache_path": str(cache_path) if cache_path else None,
        "hardware": hardware,
        "speech_sample": str(sample.resolve()) if sample else None,
        "transcription_benchmark": "NOT RUN" if sample is None else "RUN",
        "settings": {"beam_size": 5, "word_timestamps": True, "vad_filter": True, "batch_size": 1, "language": "auto"},
        "modes": [],
    }
    if sample is not None and not sample.is_file():
        raise FileNotFoundError(sample)
    duration = audio_duration(sample) if sample else None

    for mode in ("cuda-int8_float16", "cuda-float16", "cpu-int8"):
        manager = ModelManager(model, mode=mode, local_files_only=True)
        entry = {"mode": mode, "status": "NOT RUN", "audio_seconds": duration}
        if cache_path is None:
            entry.update(status="SKIPPED", reason="Model is not in the local Hugging Face cache; benchmark does not download models")
            result["modes"].append(entry)
            continue
        if mode.startswith("cuda-"):
            if not hardware.get("cuda_available"):
                entry.update(status="SKIPPED", reason=hardware.get("cuda_error") or "No CUDA device detected")
                result["modes"].append(entry)
                continue
            compute_type = mode.removeprefix("cuda-")
            if compute_type not in hardware.get("cuda_compute_types", []):
                entry.update(status="SKIPPED", reason=f"Unsupported compute type: {compute_type}")
                result["modes"].append(entry)
                continue
            if compute_type == "float16" and (hardware.get("gpu") or {}).get("free_mib", 0) < manager.FP16_MIN_FREE_MIB:
                entry.update(status="SKIPPED", reason=f"Less than {manager.FP16_MIN_FREE_MIB} MiB VRAM free; safe float16 smoke test not attempted")
                result["modes"].append(entry)
                continue
        try:
            manager.initialize()
            entry.update(status="INITIALIZATION PASSED", initialization_seconds=manager.get_status().get("init_seconds"),
                         vram_before=manager.get_status().get("vram_before"),
                         vram_after_load=manager.get_status().get("vram_after_load"),
                         device=manager.get_status().get("device"), compute_type=manager.get_status().get("compute_type"))
            if sample:
                for run_name in ("first", "warm"):
                    segments, info, elapsed, peak = manager.transcribe(
                        str(sample), language=None, beam_size=5, word_timestamps=True, vad_filter=True,
                    )
                    entry[f"{run_name}_inference_seconds"] = round(elapsed, 3)
                    entry[f"{run_name}_segment_count"] = len(segments)
                    entry[f"{run_name}_language"] = info.language
                    entry[f"{run_name}_rtf"] = round(elapsed / duration, 4) if duration else None
                    entry[f"{run_name}_peak_vram"] = peak
                entry["status"] = "PASSED"
        except Exception as exc:
            entry.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        finally:
            entry["vram_after_release"] = manager.release_whisper()
        result["modes"].append(entry)
    return result


def write_reports(result: dict, json_path: Path, markdown_path: Path):
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = [
        "# Task 02 Whisper hardware benchmark",
        "",
        f"- Model: `{result['model_identity']}`",
        f"- Model cached: **{result['model_cached']}**",
        f"- CUDA: **{result['hardware'].get('cuda_available')}** ({result['hardware'].get('cuda_devices')} device(s))",
        f"- CUDA compute types: `{', '.join(result['hardware'].get('cuda_compute_types', [])) or 'unavailable'}`",
        f"- GPU memory before modes: `{result['hardware'].get('gpu')}`",
        f"- Speech transcription benchmark: **{result['transcription_benchmark']}**",
        "",
        "| Mode | Result | Init seconds | First RTF | Warm RTF | Remarks |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for mode in result["modes"]:
        lines.append("| {mode} | {status} | {init} | {first} | {warm} | {remarks} |".format(
            mode=mode["mode"], status=mode["status"], init=mode.get("initialization_seconds", "—"),
            first=mode.get("first_rtf", "—"), warm=mode.get("warm_rtf", "—"),
            remarks=(mode.get("reason") or mode.get("error") or "") .replace("|", "\\|").replace("\n", " "),
        ))
    if result["speech_sample"] is None:
        lines.extend(["", "No approved speech sample was provided. Transcription timing, language, segment count, RTF, and inference peak VRAM were not measured."])
    if not result["model_cached"]:
        lines.extend(["", "The model was not downloaded. Run a benchmark again after the user has explicitly installed the model."])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="small", help="Existing local Faster-Whisper model name (default: small)")
    parser.add_argument("--audio-sample", type=Path, help="Explicitly supplied speech sample; never scans user folders")
    parser.add_argument("--json", type=Path, default=Path("reports/task-02-benchmark.json"))
    parser.add_argument("--markdown", type=Path, default=Path("reports/task-02-benchmark.md"))
    args = parser.parse_args()
    started = time.perf_counter()
    result = benchmark(args.model, args.audio_sample)
    result["benchmark_runtime_seconds"] = round(time.perf_counter() - started, 3)
    write_reports(result, args.json, args.markdown)
    print(args.markdown)
    print(args.json)
    for mode in result["modes"]:
        print(f"{mode['mode']}: {mode['status']}" + (f" · {mode.get('reason') or mode.get('error')}" if mode.get("reason") or mode.get("error") else ""))


if __name__ == "__main__":
    main()
