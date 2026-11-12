"""Runtime checks for FFmpeg, Python packages, and usable NVIDIA acceleration."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def _tool_candidates(name: str) -> list[Path]:
    found = []
    env_dir = os.environ.get("CLIPPER_FFMPEG_DIR")
    if env_dir:
        found.extend((Path(env_dir) / f"{name}.exe", Path(env_dir) / "bin" / f"{name}.exe"))
    found.extend((APP_DIR / ".tools" / "ffmpeg" / "bin" / f"{name}.exe", APP_DIR / ".tools" / "ffmpeg" / f"{name}.exe"))
    system_path = shutil.which(name)
    if system_path:
        found.append(Path(system_path))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        for package in ("Gyan.FFmpeg*", "BtbN.FFmpeg*"):
            found.extend(packages.glob(f"{package}*/*/bin/{name}.exe"))
    unique = []
    seen = set()
    for path in found:
        key = str(path).casefold()
        if key not in seen and path.is_file():
            unique.append(path)
            seen.add(key)
    return unique


def ffmpeg_path() -> str | None:
    candidates = _tool_candidates("ffmpeg")
    return str(candidates[0]) if candidates else None


def ffprobe_path() -> str | None:
    ffmpeg = ffmpeg_path()
    siblings = [Path(ffmpeg).with_name("ffprobe.exe")] if ffmpeg else []
    siblings += _tool_candidates("ffprobe")
    return str(next((p for p in siblings if p.is_file()), "")) or None


def _run(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def verify_encoder(encoder: str) -> tuple[bool, str]:
    exe = ffmpeg_path()
    if not exe:
        return False, "FFmpeg is missing"
    result = _run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=1", "-frames:v", "3", "-an", "-c:v", encoder, "-pix_fmt", "yuv420p", "-preset", "p4" if encoder == "h264_nvenc" else "veryfast", "-f", "null", "-"])
    return result.returncode == 0, result.stderr.strip()[-600:]


def _package_available(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def collect_diagnostics(probe_encoders: bool = True) -> dict:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str):
        checks.append({"name": name, "status": status, "detail": detail})

    add("Python", "available", sys.version.split()[0])
    add("Virtual environment", "available" if sys.prefix != sys.base_prefix else "optional", "active" if sys.prefix != sys.base_prefix else "not active")
    for module, label in (("cv2", "OpenCV"), ("scenedetect", "PySceneDetect"), ("faster_whisper", "Faster-Whisper"), ("av", "PyAV")):
        try:
            imported = __import__(module)
            add(label, "available", getattr(imported, "__version__", "installed"))
        except Exception as exc:
            add(label, "optional", f"unavailable: {exc}")

    ffmpeg, ffprobe = ffmpeg_path(), ffprobe_path()
    add("FFmpeg", "available" if ffmpeg else "missing", ffmpeg or "Install FFmpeg; it is required for analysis and rendering")
    add("FFprobe", "available" if ffprobe else "missing", ffprobe or "Install FFprobe beside FFmpeg; it is required to verify output")
    encoders: dict[str, bool] = {}
    if ffmpeg:
        result = _run([ffmpeg, "-hide_banner", "-encoders"])
        listing = result.stdout + result.stderr
        for encoder in ("libx264", "h264_nvenc"):
            listed = encoder in listing
            usable, detail = verify_encoder(encoder) if listed and probe_encoders else (listed, "listed; runtime probe not run")
            encoders[encoder] = usable
            status = "available" if usable else "unsupported" if listed else "missing"
            add(encoder, status, detail or ("encoder self-test passed" if usable else "encoder self-test failed" if listed else "not present in this FFmpeg build"))
        filters = _run([ffmpeg, "-hide_banner", "-filters"])
        has_subtitles = "subtitles" in filters.stdout + filters.stderr
        add("Subtitle filter", "available" if has_subtitles else "missing", "libass subtitles filter" if has_subtitles else "FFmpeg build lacks the subtitles filter")
    else:
        for encoder in ("libx264", "h264_nvenc"):
            encoders[encoder] = False
            add(encoder, "unsupported", "FFmpeg is missing")
        add("Subtitle filter", "unsupported", "FFmpeg is missing")

    gpu_name = ""
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        result = _run([nvidia, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
        if result.returncode == 0 and result.stdout.strip():
            gpu_name = result.stdout.strip().splitlines()[0]
            add("NVIDIA hardware/driver", "available", gpu_name)
        else:
            add("NVIDIA hardware/driver", "unsupported", result.stderr.strip() or "nvidia-smi could not query a GPU")
    else:
        add("NVIDIA hardware/driver", "optional", "nvidia-smi not found")
    try:
        import ctranslate2
        devices = ctranslate2.get_cuda_device_count()
        types = sorted(ctranslate2.get_supported_compute_types("cuda")) if devices else []
        add("CTranslate2 CUDA", "available" if devices else "unsupported", f"{devices} device(s); compute types: {', '.join(types)}" if devices else "no usable CUDA device; CPU fallback available")
    except Exception as exc:
        devices, types = 0, []
        add("CTranslate2 CUDA", "unsupported", f"{exc}; CPU fallback available")
    package = _package_available("faster_whisper")
    add("CPU transcription fallback", "available" if package else "optional", "Faster-Whisper CPU int8" if package else "Faster-Whisper package missing")
    return {"python": sys.version.split()[0], "virtual_environment": sys.prefix != sys.base_prefix, "gpu_name": gpu_name, "cuda_devices": devices, "cuda_compute_types": types, "encoders": encoders, "ffmpeg": ffmpeg, "ffprobe": ffprobe, "checks": checks}


def format_diagnostics(report: dict) -> str:
    return "\n".join(f"{c['status'].upper():<11} {c['name']}: {c['detail']}" for c in report["checks"])


if __name__ == "__main__":
    report = collect_diagnostics()
    print(json.dumps(report, indent=2) if "--json" in sys.argv else format_diagnostics(report))
