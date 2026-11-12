"""Local video analysis and Shorts rendering pipeline."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import subprocess
import threading
import time
from bisect import bisect_left
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from diagnostics import ffmpeg_path, ffprobe_path, verify_encoder
from captions import PRESETS, anchor_override, ass_style, group_words, shot_placements
from reframing import PLAN_VERSION, build_reframe_plan, crop_x_expression, write_reframe_debug
from storyboard import STORYBOARD_VERSION, write_candidate_storyboard
from editorial import (QUALITY_FLOOR, apply_assessment, assess_candidate, local_endpoint,
                       transcript_windows)
from model_manager import ModelManager
from vision import (VisionModelManager, extract_candidate_frames, read_semantic_cache,
                    semantic_cache_key, write_semantic_cache)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
ANALYSIS_ENGINE_VERSION = "task04-signal-engine-v2"
CANDIDATE_SCORE_WEIGHTS = {
    "average_motion": 0.22, "peak_motion": 0.10, "motion_consistency": 0.08,
    "average_audio_energy": 0.20, "audio_energy_variation": 0.08,
    "speech_density": 0.10, "dialogue_completeness": 0.08,
    "scene_transition_quality": 0.08, "boundary_confidence": 0.06,
}
CANDIDATE_SCORE_PENALTIES = {"excessive_silence": 0.12, "static_interval": 0.04}


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    subtitle_tracks: int


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    language: str | None
    words: list[Word]
    status: str
    device: str
    precision: str
    message: str = ""
    outcome: str = "SUCCESS"
    model: str = ""
    initialization_seconds: float | None = None
    transcription_seconds: float | None = None
    audio_seconds: float | None = None
    real_time_factor: float | None = None
    vram_before: dict | None = None
    vram_after: dict | None = None
    peak_vram: dict | None = None


_DEFAULT_MODEL_MANAGER: ModelManager | None = None
_DEFAULT_MANAGER_LOCK = threading.Lock()


def _default_model_manager(model_size: str) -> ModelManager:
    global _DEFAULT_MODEL_MANAGER
    with _DEFAULT_MANAGER_LOCK:
        if _DEFAULT_MODEL_MANAGER is None or _DEFAULT_MODEL_MANAGER.model_size != model_size:
            if _DEFAULT_MODEL_MANAGER is not None:
                _DEFAULT_MODEL_MANAGER.release_whisper()
            _DEFAULT_MODEL_MANAGER = ModelManager(model_size)
        return _DEFAULT_MODEL_MANAGER


def scan_videos(folder: Path, exclude: Path | None = None) -> list[Path]:
    excluded = exclude.resolve() if exclude else None
    return sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and (not excluded or excluded not in p.resolve().parents)), key=lambda p: str(p).casefold())


def _run(args: list[str], cancel: threading.Event | None = None, **kwargs) -> subprocess.CompletedProcess:
    if cancel and cancel.is_set():
        raise InterruptedError("Processing cancelled")
    if args and args[0] == "ffmpeg":
        args = [ffmpeg_path() or _missing_tool("FFmpeg"), *args[1:]]
    elif args and args[0] == "ffprobe":
        args = [ffprobe_path() or _missing_tool("FFprobe"), *args[1:]]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel and cancel.is_set():
                proc.terminate()
                try:
                    proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                raise InterruptedError("Processing cancelled")
    result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    if result.returncode:
        raise RuntimeError(result.stderr[-2500:] or f"Command failed: {args[0]}")
    return result


def _missing_tool(name: str) -> str:
    raise FileNotFoundError(f"{name} is required but was not found. Install it or set CLIPPER_FFMPEG_DIR.")


def probe(path: Path) -> VideoInfo:
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate", "-of", "json", str(path)])
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("No video stream found")
    try:
        numerator, denominator = video.get("avg_frame_rate", "0/1").split("/")
        fps = float(numerator) / float(denominator) if float(denominator) else 0
    except (ValueError, ZeroDivisionError):
        fps = 0
    return VideoInfo(float(data.get("format", {}).get("duration", 0)), int(video["width"]), int(video["height"]), fps, any(s.get("codec_type") == "audio" for s in streams), sum(s.get("codec_type") == "subtitle" for s in streams))


def _audio_energy(path: Path, duration: float, cancel: threading.Event) -> list[float]:
    import numpy as np
    cmd = [ffmpeg_path() or _missing_tool("FFmpeg"), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    bins = max(1, math.ceil(duration))
    totals = np.zeros(bins, dtype=np.float64)
    counts = np.zeros(bins, dtype=np.int64)
    sample_offset = 0
    try:
        while True:
            if cancel.is_set():
                proc.terminate()
                raise InterruptedError("Processing cancelled")
            chunk = proc.stdout.read(16000)
            if not chunk:
                break
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            first = sample_offset // 8000
            for idx in range(first, min(bins, (sample_offset + len(samples) + 7999) // 8000)):
                lo, hi = max(0, idx * 8000 - sample_offset), min(len(samples), (idx + 1) * 8000 - sample_offset)
                if hi > lo:
                    totals[idx] += float(np.dot(samples[lo:hi], samples[lo:hi]))
                    counts[idx] += hi - lo
            sample_offset += len(samples)
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait():
            raise RuntimeError(err[-1000:] or "Could not decode audio")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
    return [math.sqrt(totals[i] / counts[i]) if counts[i] else 0.0 for i in range(bins)]


def _motion(path: Path, duration: float, cancel: threading.Event, stats: dict | None = None) -> list[float]:
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not decode video: {path.name}")
    values = [0.0] * max(1, math.ceil(duration))
    previous = None
    second = -1
    frames_read = 0
    try:
        while True:
            if cancel.is_set():
                raise InterruptedError("Processing cancelled")
            ok, frame = cap.read()
            if not ok:
                break
            frames_read += 1
            pos = max(0, int(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000))
            if pos != second:
                gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
                if previous is not None and second < len(values):
                    values[second] = float(cv2.absdiff(previous, gray).mean())
                previous, second = gray, pos
    finally:
        cap.release()
    if not frames_read:
        raise RuntimeError(f"OpenCV decoded no frames: {path.name}")
    if stats is not None:
        stats["frames_decoded"] = frames_read
    return values


def _scene_cuts(path: Path) -> tuple[list[float], str]:
    try:
        from scenedetect import ContentDetector, detect
        scenes = detect(str(path), ContentDetector(threshold=27.0), show_progress=False)
        cuts = [float(a.get_seconds()) for a, _ in scenes[1:]]
        return cuts, f"{len(cuts)} cut(s) found"
    except ImportError as exc:
        return [], f"scene detection unavailable: {exc}"
    except Exception as exc:
        return [], f"scene detection failed: {type(exc).__name__}: {exc}"


def _analysis_fingerprint(path: Path, stat) -> str:
    values = {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
              "analysis_schema": 2, "analysis_engine_version": ANALYSIS_ENGINE_VERSION}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def _read_analysis_cache(path: Path, fingerprint: str) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("schema") == 2 and data.get("fingerprint") == fingerprint and
                isinstance(data.get("motion"), list) and isinstance(data.get("audio_energy"), list) and
                isinstance(data.get("scene_cuts"), list)):
            motion = [float(value) for value in data["motion"]]
            energy = [float(value) for value in data["audio_energy"]]
            cuts = [float(value) for value in data["scene_cuts"]]
            if all(math.isfinite(value) for value in motion + energy + cuts):
                data.update(motion=motion, audio_energy=energy, scene_cuts=cuts)
                return data
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError, OverflowError):
        pass
    return None


def _write_analysis_cache(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _write_manifest(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _manifest_clip_path(folder: Path, filename: object) -> Path | None:
    if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() != ".mp4":
        return None
    candidate = folder / filename
    return candidate if candidate.resolve().parent == folder.resolve() else None


def _write_contact_sheet(folder: Path, clips: list[dict]) -> Path | None:
    """Create a local review thumbnail grid; failure never invalidates rendered clips."""
    import shutil
    import tempfile
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    temporary = Path(tempfile.mkdtemp(prefix="clipper-contact-", dir=folder))
    try:
        cards = []
        for index, item in enumerate(clips, 1):
            source = _manifest_clip_path(folder, item.get("filename"))
            if source is None or not source.is_file():
                continue
            image_path = temporary / f"{index:03}.jpg"
            try:
                timestamp = min(1.0, max(0.0, float(item.get("duration", 1)) * .25))
                _run(["ffmpeg", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(source), "-frames:v", "1",
                      "-vf", "scale=320:568:force_original_aspect_ratio=decrease,pad=320:568:(ow-iw)/2:(oh-ih)/2",
                      "-q:v", "4", str(image_path)])
                image = cv2.imread(str(image_path))
                if image is not None:
                    cv2.imwrite(str(folder / f"{source.stem}_thumb.png"), cv2.resize(image, (320, 568)))
                    cards.append((image, f"{index:03} · {item.get('category', 'clip')} · {item.get('duration', 0):.0f}s"))
            except Exception:
                continue
        if not cards:
            return None
        columns, cell_w, cell_h = 3, 360, 640
        sheet = np.full((((len(cards) + columns - 1) // columns) * cell_h, columns * cell_w, 3), 24, dtype=np.uint8)
        for index, (image, label) in enumerate(cards):
            image = cv2.resize(image, (320, 568))
            x, y = (index % columns) * cell_w + 20, (index // columns) * cell_h + 18
            sheet[y:y + 568, x:x + 320] = image
            cv2.putText(sheet, label, (x, y + 600), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1, cv2.LINE_AA)
        target = folder / "clip_contact_sheet.png"
        return target if cv2.imwrite(str(target), sheet) else None
    except Exception:
        return None
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def transcribe(path: Path, has_audio: bool, model_size: str, cache: Path, cancel: threading.Event,
               progress: Callable[[str], None] | None = None, model_manager: ModelManager | None = None,
               audio_seconds: float | None = None, language: str | None = None,
               allow_inference: bool = True) -> TranscriptionResult:
    if not has_audio:
        return TranscriptionResult(None, [], "no_audio_track", "none", "none", outcome="NO_AUDIO", model=model_size)
    manager = model_manager or _default_model_manager(model_size)
    settings = {"model": manager.model_identity(model_size), "language": language, "beam_size": 5,
                "word_timestamps": True, "vad_filter": True}
    stat = path.stat()
    fingerprint_data = {"source_path": str(path.resolve()), "source_size": stat.st_size,
                        "source_mtime_ns": stat.st_mtime_ns, "settings": settings}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode("utf-8")).hexdigest()
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("schema") == 2 and data.get("fingerprint") == fingerprint:
                words = [Word(**w) for w in data.get("words", [])]
                status = "transcribed" if words else "no_speech"
                return TranscriptionResult(
                    data.get("language"), words, status, data.get("device", "cache"), data.get("precision", "cache"),
                    "transcription cache hit", data.get("outcome", "SUCCESS"), settings["model"],
                    data.get("initialization_seconds"), data.get("transcription_seconds"), data.get("audio_seconds"),
                    data.get("real_time_factor"), data.get("vram_before"), data.get("vram_after"), data.get("peak_vram"),
                )
        except (OSError, ValueError, TypeError, KeyError):
            pass
    if not allow_inference:
        return TranscriptionResult(None, [], "unavailable", "not_run", "not_run",
                                   "Whisper inference was disabled for this heuristic-only session",
                                   "INFERENCE_DISABLED", settings["model"])
    try:
        segments, info, transcription_seconds, peak_vram = manager.transcribe(
            str(path), cancelled=cancel.is_set, language=language, beam_size=5,
            word_timestamps=True, vad_filter=True,
        )
        words = []
        for segment in segments:
            for word in segment.words or []:
                if word.start is not None and word.end is not None:
                    words.append(Word(float(word.start), float(word.end), word.word))
    except InterruptedError as exc:
        state = manager.get_status()
        return TranscriptionResult(None, [], "cancelled", state["device"], state["compute_type"], str(exc),
                                   "CANCELLED", settings["model"])
    except ImportError as exc:
        return TranscriptionResult(None, [], "unavailable", "none", "none", str(exc),
                                   "DEPENDENCY_UNAVAILABLE", settings["model"])
    except Exception as exc:
        state = manager.get_status()
        return TranscriptionResult(None, [], "failed", state["device"], state["compute_type"],
                                   f"{type(exc).__name__}: {exc}", "TRANSCRIPTION_FAILED", settings["model"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    status = "transcribed" if words else "no_speech"
    manager_status = manager.get_status()
    outcome = "NO_SPEECH" if not words else "CPU_FALLBACK" if manager_status["device"] == "cpu" else "SUCCESS"
    if manager_status.get("fallback_reason"):
        if progress:
            progress(manager_status["fallback_reason"])
    elapsed_audio = audio_seconds if audio_seconds and audio_seconds > 0 else None
    result = TranscriptionResult(
        info.language, words, status, manager_status["device"], manager_status["compute_type"],
        manager_status.get("fallback_reason", ""), outcome, settings["model"], manager_status.get("init_seconds"),
        round(transcription_seconds, 3), elapsed_audio,
        round(transcription_seconds / elapsed_audio, 4) if elapsed_audio else None,
        manager_status.get("vram_before"), manager.get_memory_usage(), peak_vram,
    )
    cache_data = {"schema": 2, "fingerprint": fingerprint, "settings": settings, "language": info.language,
                  "device": result.device, "precision": result.precision, "outcome": result.outcome,
                  "initialization_seconds": result.initialization_seconds,
                  "transcription_seconds": result.transcription_seconds, "audio_seconds": result.audio_seconds,
                  "real_time_factor": result.real_time_factor, "vram_before": result.vram_before,
                  "vram_after": result.vram_after, "peak_vram": result.peak_vram,
                  "words": [asdict(w) for w in words]}
    temporary = cache.with_suffix(cache.suffix + ".tmp")
    temporary.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(cache)
    return result


def _robust_normalize(values: list[float]) -> list[float]:
    numeric = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        numeric.append(number if math.isfinite(number) else 0.0)
    clean = sorted(numeric)
    if not clean:
        return [0.0] * len(values)
    low = clean[int((len(clean) - 1) * 0.1)]
    high = clean[int((len(clean) - 1) * 0.9)]
    if high <= low:
        high = clean[-1]
    if high <= low:
        return [0.0] * len(values)
    return [max(0.0, min(1.0, (value - low) / (high - low))) for value in numeric]


def _valid_words(words: list[Word], duration: float | None = None) -> list[Word]:
    valid = []
    for word in words:
        try:
            start, end, text = float(word.start), float(word.end), str(word.text)
        except (AttributeError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start or start < 0 or (duration is not None and end > duration):
            continue
        valid.append(Word(start, end, text))
    return sorted(valid, key=lambda word: word.start)


def detect_activity_regions(duration: float, motion: list[float], audio: list[float], words: list[Word],
                           gap_tolerance: int = 2) -> list[dict]:
    """Find sustained activity using normalized visual, audio, and speech signals."""
    bins = max(1, math.ceil(duration))
    visual = _robust_normalize((motion + [0.0] * bins)[:bins])
    sound = _robust_normalize((audio + [0.0] * bins)[:bins])
    speech = [0.0] * bins
    for word in words:
        index = int(word.start)
        if 0 <= index < bins:
            speech[index] += 1.0
    speech = _robust_normalize(speech)
    activity = [max(0.48 * visual[i], 0.65 * sound[i], 0.25 * speech[i]) for i in range(bins)]
    active = [i for i, value in enumerate(activity) if value >= 0.34]
    if not active:
        return []
    groups, group = [], [active[0]]
    for index in active[1:]:
        if index - group[-1] <= gap_tolerance + 1:
            group.append(index)
        else:
            groups.append(group)
            group = [index]
    groups.append(group)
    return [{"start": float(group[0]), "end": min(duration, float(group[-1] + 1)),
             "peak_activity": round(max(activity[i] for i in group), 3),
             "motion": round(max(visual[i] for i in group), 3),
             "audio": round(max(sound[i] for i in group), 3)}
            for group in groups if group[-1] - group[0] >= 1 or max(activity[i] for i in group) >= 0.6]


def extract_speech_regions(words: list[Word], max_pause: float = 1.15) -> list[dict]:
    """Group timestamped words into sentence/pause-delimited dialogue units."""
    valid = _valid_words(words)
    units, current = [], []
    for word in valid:
        boundary = current and (word.start - current[-1].end > max_pause or current[-1].text.rstrip().endswith((".", "?", "!")))
        if boundary:
            units.append(current)
            current = []
        current.append(word)
    if current:
        units.append(current)
    return [{"start": float(unit[0].start), "end": float(unit[-1].end),
             "word_count": len(unit), "complete_sentence": unit[-1].text.rstrip().endswith((".", "?", "!"))}
            for unit in units]


def _interval_iou(first: dict, second: dict) -> float:
    intersection = max(0.0, min(first["end"], second["end"]) - max(first["start"], second["start"]))
    union = max(first["end"], second["end"]) - min(first["start"], second["start"])
    return intersection / union if union else 0.0


def generate_candidates(duration: float, motion: list[float], audio: list[float], cuts: list[float], words: list[Word],
                        min_length: int = 20, max_length: int = 55,
                        cancel: threading.Event | None = None) -> dict:
    """Propose scene, sustained-activity, and dialogue intervals independently of scoring."""
    minimum, maximum = max(1.0, float(min_length)), max(float(min_length), float(max_length))
    result = {"candidates": [], "rejections": [], "activity_regions": [], "speech_regions": [],
              "short_source_policy": "none"}
    if duration <= 0:
        result["short_source_policy"] = "skip_invalid_duration"
        return result
    if duration < minimum:
        result["short_source_policy"] = "skip_below_minimum"
        result["rejections"].append({"reason": "source shorter than configured minimum", "duration": duration})
        return result
    words = _valid_words(words, duration)

    proposals: dict[tuple[int, int], dict] = {}
    def add(start: float, end: float, source: str, scene_edges: tuple[bool, bool] = (False, False)):
        start, end = max(0.0, float(start)), min(duration, float(end))
        if end <= start or end - start < minimum - 0.01 or end - start > maximum + 0.01:
            result["rejections"].append({"start": round(start, 3), "end": round(end, 3), "source": source,
                                         "reason": "outside configured duration"})
            return
        key = (round(start * 4), round(end * 4))
        candidate = proposals.setdefault(key, {"start": start, "end": end, "original_start": start,
                                                "original_end": end, "sources": [], "scene_edges": [False, False]})
        if source not in candidate["sources"]:
            candidate["sources"].append(source)
        candidate["scene_edges"] = [a or b for a, b in zip(candidate["scene_edges"], scene_edges)]

    target_durations = sorted({minimum, min(maximum, minimum + 5), min(maximum, 30.0),
                               min(maximum, 40.0), min(maximum, 45.0), maximum})
    # Snap a small set of requested window sizes to scene ends instead of enumerating every cut pair.
    boundaries = [0.0] + sorted({float(c) for c in cuts if 0 < c < duration}) + [float(duration)]
    for left in range(len(boundaries) - 1):
        if cancel and cancel.is_set():
            raise InterruptedError("Processing cancelled")
        for target in target_durations:
            target_time = boundaries[left] + target
            position = bisect_left(boundaries, target_time, left + 1)
            options = [index for index in (position - 1, position) if left < index < len(boundaries)
                       and minimum <= boundaries[index] - boundaries[left] <= maximum]
            if options:
                right = min(options, key=lambda index: abs((boundaries[index] - boundaries[left]) - target))
                add(boundaries[left], boundaries[right], "scene", (left > 0, right < len(boundaries) - 1))

    activity = detect_activity_regions(duration, motion, audio, words)
    result["activity_regions"] = activity
    for region in activity:
        if cancel and cancel.is_set():
            raise InterruptedError("Processing cancelled")
        center = (region["start"] + region["end"]) / 2
        for length in target_durations:
            start = max(0.0, min(duration - length, center - length / 2))
            add(start, start + length, "activity")

    dialogue = extract_speech_regions(words)
    result["speech_regions"] = dialogue
    # Accumulate adjacent speech units into complete, context-preserving dialogue candidates.
    for index, unit in enumerate(dialogue):
        if cancel and cancel.is_set():
            raise InterruptedError("Processing cancelled")
        end_index = index
        while end_index < len(dialogue):
            phrase_end = dialogue[end_index]["end"]
            raw_start = max(0.0, unit["start"] - 2.5)
            raw_end = min(duration, phrase_end + 2.5)
            if raw_end - raw_start > maximum:
                break
            if raw_end - raw_start >= minimum:
                add(raw_start, raw_end, "speech")
                break
            end_index += 1

    result["candidates"] = list(proposals.values())
    result["candidate_count"] = len(result["candidates"])
    return result


def refine_clip_boundaries(candidate: dict, duration: float, words: list[Word], cuts: list[float],
                           min_length: int, max_length: int, context_margin: float = 2.5,
                           speech_regions: list[dict] | None = None) -> dict:
    """Snap near sentence, word, or scene edges without crossing clip-duration limits."""
    start, end = candidate["start"], candidate["end"]
    original = (start, end)
    units = speech_regions if speech_regions is not None else extract_speech_regions(words)
    start_unit = next((u for u in units if u["start"] <= start < u["end"] and start - u["start"] <= context_margin), None)
    end_unit = next((u for u in units if u["start"] < end < u["end"] and u["end"] - end <= context_margin), None)
    if start_unit:
        start = start_unit["start"]
    if end_unit:
        end = end_unit["end"]
    if not start_unit:
        nearest = min((c for c in cuts if abs(c - start) <= 1.25), key=lambda c: abs(c - start), default=None)
        if nearest is not None and not any(w.start < nearest < w.end for w in words):
            start = nearest
    if not end_unit:
        nearest = min((c for c in cuts if abs(c - end) <= 1.25), key=lambda c: abs(c - end), default=None)
        if nearest is not None and not any(w.start < nearest < w.end for w in words):
            end = nearest
    def snap_to_words(left: float, right: float) -> tuple[float, float]:
        for word in words:
            if word.start < left < word.end:
                left = word.start
            if word.start < right < word.end:
                right = word.end
        return left, right
    start, end = snap_to_words(start, end)
    start, end = max(0.0, start), min(duration, end)
    if end - start > max_length:
        end = start + max_length
        for word in words:
            if word.start < end < word.end:
                end = word.start
                break
    boundary_valid = min_length <= end - start <= max_length and end > start
    if not boundary_valid:
        start, end = snap_to_words(*original)
        start, end = max(0.0, start), min(duration, end)
        boundary_valid = min_length <= end - start <= max_length and end > start
    updated = dict(candidate)
    updated.update(start=round(start, 3), end=round(end, 3), duration=round(end - start, 3),
                   boundary_refinement={"original_start": round(original[0], 3), "original_end": round(original[1], 3),
                                        "start_changed": round(start, 3) != round(original[0], 3),
                                        "end_changed": round(end, 3) != round(original[1], 3),
                                        "context_margin_seconds": context_margin},
                   boundary_valid=boundary_valid)
    aligned_start = any(abs(start - unit["start"]) < 0.01 for unit in units) or any(abs(start - c) < 0.01 for c in cuts)
    aligned_end = any(abs(end - unit["end"]) < 0.01 for unit in units) or any(abs(end - c) < 0.01 for c in cuts)
    updated["boundary_confidence"] = round((0.5 + 0.25 * aligned_start + 0.25 * aligned_end), 2)
    return updated


def _candidate_features(candidate: dict, visual: list[float], sound: list[float],
                        words: list[Word], cuts: list[float], units: list[dict]) -> dict:
    start, end = candidate["start"], candidate["end"]
    bins = len(visual)
    left, right = max(0, int(start)), min(bins, math.ceil(end))
    count = max(1, right - left)
    m, a = visual[left:right], sound[left:right]
    average_motion = sum(m) / count if m else 0.0
    average_audio = sum(a) / count if a else 0.0
    speech_count = sum(start <= word.start < end for word in words)
    speech_density = min(1.0, speech_count / max(1.0, (end - start) * 0.7))
    speech_seconds = {int(word.start) for word in words if start <= word.start < end}
    complete_units = [u for u in units if u["start"] < end and u["end"] > start]
    complete_ratio = (sum(u["start"] >= start and u["end"] <= end for u in complete_units) / len(complete_units)) if complete_units else 0.0
    internal = [c for c in cuts if start < c < end]
    edge_quality = min(1.0, sum(min(abs(start-c), abs(end-c)) <= 1.5 for c in cuts) / 2.0)
    if "scene" in candidate["sources"]:
        edge_quality = max(edge_quality, 0.55)
    motion_consistency = sum(value >= 0.3 for value in m) / count if m else 0.0
    audio_variation = min(1.0, (max(a) - min(a)) if len(a) > 1 else 0.0)
    still_silence = sum(visual[i] < 0.12 and sound[i] < 0.12 and i not in speech_seconds
                        for i in range(left, min(right, len(visual)))) / count
    static_interval = sum(m[index] < 0.12 and index not in speech_seconds for index in range(len(m))) / count if m else 1.0
    return {"average_motion": round(average_motion, 3), "peak_motion": round(max(m, default=0.0), 3),
            "motion_consistency": round(motion_consistency, 3), "average_audio_energy": round(average_audio, 3),
            "audio_energy_variation": round(audio_variation, 3), "speech_density": round(speech_density, 3),
            "dialogue_completeness": round(complete_ratio, 3), "scene_transition_quality": round(edge_quality, 3),
            "excessive_silence": round(still_silence, 3), "static_interval": round(static_interval, 3),
            "scene_changes": len(internal)}


def rank_candidates(candidates: list[dict], duration: float, motion: list[float], audio: list[float],
                    cuts: list[float], words: list[Word], min_length: int, max_length: int,
                    context_margin: float = 2.5, weights: dict | None = None,
                    cancel: threading.Event | None = None) -> list[dict]:
    ranked = []
    weights = weights or CANDIDATE_SCORE_WEIGHTS
    words = _valid_words(words, duration)
    bins = max(1, math.ceil(duration))
    visual = _robust_normalize((motion + [0.0] * bins)[:bins])
    sound = _robust_normalize((audio + [0.0] * bins)[:bins])
    units = extract_speech_regions(words)
    for candidate in candidates:
        if cancel and cancel.is_set():
            raise InterruptedError("Processing cancelled")
        refined = refine_clip_boundaries(candidate, duration, words, cuts, min_length, max_length,
                                         context_margin=context_margin, speech_regions=units)
        features = _candidate_features(refined, visual, sound, words, cuts, units)
        positive = sum(weights[name] * (refined["boundary_confidence"] if name == "boundary_confidence" else features[name])
                       for name in weights)
        score = max(0.0, min(1.0, positive - sum(CANDIDATE_SCORE_PENALTIES[name] * features[name]
                                                 for name in CANDIDATE_SCORE_PENALTIES)))
        ranked.append({**refined, "feature_scores": features, "score": round(score, 4)})
    return ranked


def deduplicate_candidates(candidates: list[dict], iou_threshold: float = 0.35,
                           cancel: threading.Event | None = None) -> tuple[list[dict], list[dict]]:
    kept, rejected = [], []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        if cancel and cancel.is_set():
            raise InterruptedError("Processing cancelled")
        duplicate = next((prior for prior in kept if _interval_iou(candidate, prior) > iou_threshold), None)
        if duplicate:
            rejected.append({"start": candidate["start"], "end": candidate["end"], "reason": "overlap duplicate",
                             "duplicate_of": [duplicate["start"], duplicate["end"]], "iou": round(_interval_iou(candidate, duplicate), 3)})
        else:
            kept.append(candidate)
    return kept, rejected


SEMANTIC_CATEGORY_DIVERSITY_WEIGHT = 0.05


def select_diverse_candidates(candidates: list[dict], count: int, duration: float,
                              minimum_distance: float = 5.0, minimum_score: float = 0.075,
                              cancel: threading.Event | None = None,
                              category_weight: float = SEMANTIC_CATEGORY_DIVERSITY_WEIGHT) -> tuple[list[dict], list[dict]]:
    if not math.isfinite(category_weight) or category_weight < 0:
        raise ValueError("category_weight must be a finite nonnegative value")
    selected, rejected = [], []
    remaining = [c for c in candidates if c.get("boundary_valid", True) and c["score"] >= minimum_score]
    categories = {c.get("semantic_category") for c in remaining
                  if c.get("semantic_category") not in (None, "UNCERTAIN")}
    use_category_diversity = len(categories) > 1 and category_weight > 0
    for candidate in candidates:
        candidate.pop("selection_diversity", None)
    rejected.extend({"start": c["start"], "end": c["end"], "reason": "invalid refined boundary"}
                    for c in candidates if not c.get("boundary_valid", True))
    rejected.extend({"start": c["start"], "end": c["end"], "reason": "below minimum quality score", "score": c["score"]}
                    for c in candidates if c.get("boundary_valid", True) and c["score"] < minimum_score)
    while remaining and len(selected) < count:
        if cancel and cancel.is_set():
            raise InterruptedError("Processing cancelled")
        def diversity_score(item):
            center = (item["start"] + item["end"]) / 2
            distance = min((abs(center - (clip["start"] + clip["end"]) / 2) for clip in selected), default=duration)
            temporal = 0.7 + 0.3 * min(1.0, distance / max(1.0, duration / 4))
            category = item.get("semantic_category")
            category_bonus = category_weight if use_category_diversity and category not in (None, "UNCERTAIN") and all(
                clip.get("semantic_category") != category for clip in selected) else 0.0
            item["_selection_diversity"] = {"temporal_factor": round(temporal, 4),
                                            "category_bonus": round(category_bonus, 4),
                                            "category_weight": category_weight if use_category_diversity else 0.0}
            return item["score"] * temporal + category_bonus
        remaining.sort(key=diversity_score, reverse=True)
        candidate = remaining.pop(0)
        conflict = next((prior for prior in selected if _interval_iou(candidate, prior) > 0.35 or
                         (candidate["start"] >= prior["end"] and candidate["start"] - prior["end"] < minimum_distance) or
                         (prior["start"] >= candidate["end"] and prior["start"] - candidate["end"] < minimum_distance)), None)
        if conflict:
            candidate.pop("_selection_diversity", None)
            rejected.append({"start": candidate["start"], "end": candidate["end"], "reason": "duplicate or too close",
                             "duplicate_of": [conflict["start"], conflict["end"]]})
        else:
            candidate["selection_diversity"] = candidate.pop("_selection_diversity")
            selected.append(candidate)
        for item in remaining:
            item.pop("_selection_diversity", None)
    for candidate in remaining:
        rejected.append({"start": candidate["start"], "end": candidate["end"], "reason": "clip count reached"})
    for candidate in selected:
        features = candidate["feature_scores"]
        signals = [(features["average_motion"], "high visual motion with sustained activity"),
                   (features["average_audio_energy"], "strong audio event with scene continuity"
                    if features.get("scene_transition_quality", 0.0) >= 0.5 else "sustained audio energy"),
                   (features["speech_density"] * features["dialogue_completeness"], "dialogue segment with complete speech units")]
        reason_score, reason = max(signals, key=lambda item: item[0])
        candidate["reason"] = reason if reason_score >= 0.15 else "combined activity signals"
        candidate["selected"] = True
    return sorted(selected, key=lambda item: item["start"]), rejected


def select_clips(duration: float, motion: list[float], audio: list[float], cuts: list[float], words: list[Word],
                 count: int = 5, min_length: int = 20, max_length: int = 55,
                 minimum_distance: float = 5.0, context_margin: float = 2.5,
                 scoring_weights: dict | None = None, cancel: threading.Event | None = None) -> dict:
    generated = generate_candidates(duration, motion, audio, cuts, words, min_length, max_length, cancel)
    candidates = rank_candidates(generated["candidates"], duration, motion, audio, cuts, words,
                                 min_length, max_length, context_margin, scoring_weights, cancel)
    unique, duplicate_rejections = deduplicate_candidates(candidates, cancel=cancel)
    selected, selection_rejections = select_diverse_candidates(unique, count, duration, minimum_distance, cancel=cancel)
    selected_ids = {id(candidate) for candidate in selected}
    for candidate in candidates:
        candidate["selected"] = id(candidate) in selected_ids
    generated["candidate_count"] = len(generated["candidates"])
    generated["candidates"] = candidates
    generated["selected"] = selected
    generated["rejections"].extend(duplicate_rejections)
    generated["rejections"].extend(selection_rejections)
    generated["rejected_count"] = len(generated["rejections"])
    return generated


def rank_clips(duration: float, motion: list[float], audio: list[float], cuts: list[float], words: list[Word],
               count: int = 5, min_length: int = 20, max_length: int = 55,
               minimum_distance: float = 5.0, context_margin: float = 2.5,
               scoring_weights: dict | None = None) -> list[dict]:
    """Compatibility wrapper: return selected variable-duration clips only."""
    return select_clips(duration, motion, audio, cuts, words, count, min_length, max_length,
                        minimum_distance, context_margin, scoring_weights)["selected"]


def _ass_text(words: list[Word], start: float, end: float, style: str | None = None,
              placement: str | None = None, placements: list[dict] | None = None) -> str:
    style = style or os.getenv("CLIPPER_CAPTION_STYLE", "bold_pop")
    placement = placement or os.getenv("CLIPPER_CAPTION_PLACEMENT", "auto")
    if style not in PRESETS:
        style = "bold_pop"
    if placement not in {"auto", "lower", "middle", "upper"}:
        placement = "lower"
    if placement == "auto":
        placement = "lower"
    events = []
    for line in group_words(words, start, end, style):
        karaoke = style != "cinematic_minimal"
        words_text = [f"{{\\kf{max(1, round((w.end - w.start) * 100))}}}{_ass_escape(w.text)}" if karaoke
                      else _ass_escape(w.text) for w in line]
        text = " ".join(words_text[:4]) + ("\\N" + " ".join(words_text[4:]) if len(words_text) > 4 else "")
        midpoint = (line[0].start + line[-1].end) / 2
        anchor = next((item["anchor"] for item in placements or [] if item["start"] <= midpoint < item["end"]), None)
        override = anchor_override(anchor) if anchor else ""
        events.append(f"Dialogue: 0,{_ass_time(max(0, line[0].start-start))},{_ass_time(min(end-start, line[-1].end-start))},Default,,0,0,0,,{override}{text.strip()}")
    return "\n".join(["[Script Info]", "ScriptType: v4.00+", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "PlayResX: 1080", "PlayResY: 1920", "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding", ass_style(style, placement), "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text", *events])


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ").replace("\r", " ")


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(seconds * 100))
    hours, centiseconds = divmod(centiseconds, 360000)
    minutes, centiseconds = divmod(centiseconds, 6000)
    secs, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centiseconds:02}"


def _srt(words: list[Word], start: float, end: float, style: str | None = None) -> str:
    style = style or os.getenv("CLIPPER_CAPTION_STYLE", "bold_pop")
    if style not in PRESETS:
        style = "bold_pop"
    chunks = group_words(words, start, end, style)
    def stamp(s: float) -> str:
        ms = max(0, int(s * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    entries = []
    for i, chunk in enumerate(chunks, 1):
        text = [w.text.replace("\n", " ").replace("\r", " ").strip() for w in chunk]
        caption = " ".join(text[:4])
        if len(text) > 4:
            caption += "\n" + " ".join(text[4:])
        entries.append(f"{i}\n{stamp(max(0, chunk[0].start-start))} --> {stamp(min(end-start, chunk[-1].end-start))}\n{caption}")
    return "\n\n".join(entries)


def _escape_filter(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def validate_srt(path: Path, duration: float, allow_empty: bool = True) -> int:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        if allow_empty:
            return 0
        raise ValueError("Subtitle file is empty")
    pattern = re.compile(r"(?m)^\d+\s*\n(\d{2,}:\d{2}:\d{2},\d{3}) --> (\d{2,}:\d{2}:\d{2},\d{3})\s*\n(.+?)(?=\n\s*\n|\Z)", re.S)
    cues = list(pattern.finditer(text))
    if not cues or len(cues) != len(re.findall(r"(?m)^\d+\s*$", text)):
        raise ValueError("Subtitle file contains invalid SRT cues")

    def seconds(value: str) -> float:
        hms, millis = value.split(",")
        hours, minutes, secs = map(int, hms.split(":"))
        return hours * 3600 + minutes * 60 + secs + int(millis) / 1000

    previous_end = 0.0
    for cue in cues:
        start, end = map(seconds, cue.group(1, 2))
        if start < previous_end or end <= start or end > duration + 0.25:
            raise ValueError("Subtitle timestamps are invalid or outside the clip")
        previous_end = end
    return len(cues)


def _probe_output(path: Path) -> dict:
    result = _run(["ffprobe", "-v", "error", "-show_entries",
                   "stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,start_time,duration:format=duration,format_name",
                   "-of", "json", str(path)])
    return json.loads(result.stdout)


def validate_output(path: Path, expected_duration: float, has_audio: bool, srt_path: Path,
                    deep_verify: bool = True, cancel: threading.Event | None = None) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Rendered output is missing or empty")
    data = _probe_output(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0))
    if not video or (video.get("width"), video.get("height")) != (1080, 1920):
        raise RuntimeError("Output resolution/aspect ratio is not 1080x1920 (9:16)")
    if video.get("codec_name") != "h264":
        raise RuntimeError("Output video codec is not H.264")
    if video.get("pix_fmt") != "yuv420p":
        raise RuntimeError(f"Output pixel format is {video.get('pix_fmt')}, expected yuv420p")
    fps = video.get("avg_frame_rate") or video.get("r_frame_rate")
    try:
        num, den = map(int, fps.split("/"))
        actual_fps = num / den
    except (AttributeError, ValueError, ZeroDivisionError):
        actual_fps = 0
    if abs(actual_fps - 30) > 0.01:
        raise RuntimeError(f"Output frame rate is {actual_fps:g}, expected 30 FPS")
    if "mp4" not in fmt.get("format_name", ""):
        raise RuntimeError("Output container is not MP4")
    if duration <= 0 or abs(duration - expected_duration) > 0.25:
        raise RuntimeError(f"Output duration {duration:.3f}s does not match requested {expected_duration:.3f}s")
    if has_audio != (audio is not None):
        raise RuntimeError("Output audio presence does not match the source")
    if audio:
        if audio.get("codec_name") != "aac":
            raise RuntimeError("Output audio codec is not AAC")
        start_delta = abs(float(audio.get("start_time", 0) or 0) - float(video.get("start_time", 0) or 0))
        audio_duration = float(audio.get("duration", duration) or duration)
        video_duration = float(video.get("duration", duration) or duration)
        if start_delta > 0.1 or abs(audio_duration - video_duration) > 0.25:
            raise RuntimeError(f"Audio/video timing differs (start {start_delta:.3f}s, duration {abs(audio_duration-video_duration):.3f}s)")
    cues = validate_srt(srt_path, duration)
    if deep_verify:
        _run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], cancel)
    return {"container": "mp4", "video_codec": "h264", "audio_codec": "aac" if audio else None,
            "width": 1080, "height": 1920, "fps": actual_fps, "pixel_format": video.get("pix_fmt"),
            "duration": duration, "subtitle_cues": cues, "audio_video_sync_checked": bool(audio)}


def select_encoder(requested: str = "auto") -> tuple[str, str]:
    if requested not in {"auto", "libx264", "h264_nvenc"}:
        raise ValueError(f"Unsupported encoder setting: {requested}")
    if requested in {"auto", "h264_nvenc"}:
        usable, message = verify_encoder("h264_nvenc")
        if usable:
            return "h264_nvenc", "NVENC self-test passed"
        fallback = f"NVENC unavailable ({message or 'self-test failed'}); using libx264"
    else:
        fallback = "libx264 requested"
    usable, message = verify_encoder("libx264")
    if not usable:
        raise RuntimeError(f"No verified H.264 encoder is available: {message}")
    return "libx264", fallback


def _render_filter(info: VideoInfo, clip: dict, ass_path: Path, reframe_plan: dict | None = None) -> str:
    shots = (reframe_plan or {}).get("shots", [])
    if not shots:
        return (f"[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,boxblur=30:10[bg2];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];"
                f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,subtitles='{_escape_filter(ass_path)}',fps=30,format=yuv420p[v]")
    pieces = []
    labels = []
    clip_start = float(clip["start"])
    for index, shot in enumerate(shots):
        shot_start = max(0.0, float(shot["start"]) - clip_start)
        shot_end = min(float(clip["end"] - clip_start), float(shot["end"]) - clip_start)
        if shot_end <= shot_start:
            continue
        label = f"shot{index}"
        crop = shot.get("crop") if shot.get("composition") in {"subject_crop", "saliency_crop"} else None
        if crop and crop["width"] / crop["height"] > 0:
            expression = crop_x_expression(shot.get("focus_path", []), crop["width"], info.width,
                                           shot_end - shot_start).replace(",", r"\,")
            pieces.append(f"[0:v]trim=start={shot_start:.3f}:end={shot_end:.3f},setpts=PTS-STARTPTS,"
                          f"crop={crop['width']}:{crop['height']}:'{expression}':{crop['y']},"
                          f"scale=1080:1920,setsar=1[{label}]")
        else:
            pieces.append(f"[0:v]trim=start={shot_start:.3f}:end={shot_end:.3f},setpts=PTS-STARTPTS,split=2[bg{index}][fg{index}];"
                          f"[bg{index}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:10[bg{index}x];"
                          f"[fg{index}]scale=1080:1920:force_original_aspect_ratio=decrease[fg{index}x];"
                          f"[bg{index}x][fg{index}x]overlay=(W-w)/2:(H-h)/2,setsar=1[{label}]")
        labels.append(f"[{label}]")
    if not labels:
        raise ValueError("Reframing plan contains no renderable shot intervals")
    if len(labels) == 1:
        base = labels[0]
    else:
        base = "[joined]"
        pieces.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0{base}")
    pieces.append(f"{base}subtitles='{_escape_filter(ass_path)}',fps=30,format=yuv420p[v]")
    return ";".join(pieces)


def render(path: Path, output: Path, info: VideoInfo, clip: dict, words: list[Word], has_audio: bool,
           cancel: threading.Event, encoder: str = "auto", reframe_plan: dict | None = None,
           caption_shots: list[dict] | None = None) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    srt_path = output.with_suffix(".srt")
    temp_video = output.with_name(output.stem + ".partial" + output.suffix)
    temp_srt = temp_video.with_suffix(".srt")
    ass_path = temp_video.with_suffix(".ass")
    duration = clip["end"] - clip["start"]
    selected_encoder, encoder_note = select_encoder(encoder)
    temp_srt.write_text(_srt(words, clip["start"], clip["end"]), encoding="utf-8")
    placement = os.getenv("CLIPPER_CAPTION_PLACEMENT", "auto")
    ass_path.write_text(_ass_text(words, clip["start"], clip["end"], placement=placement,
                                  placements=caption_shots), encoding="utf-8-sig")
    try:
        filt = _render_filter(info, clip, ass_path, reframe_plan)
        cmd = ["ffmpeg", "-y", "-ss", f"{clip['start']:.3f}", "-i", str(path), "-t", f"{duration:.3f}", "-filter_complex", filt, "-map", "[v]"]
        if has_audio:
            cmd += ["-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        cmd += ["-c:v", selected_encoder]
        cmd += ["-preset", "p4", "-cq", "22", "-b:v", "0"] if selected_encoder == "h264_nvenc" else ["-preset", "veryfast", "-crf", "20"]
        cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", str(temp_video)]
        encode_started = time.perf_counter()
        _run(cmd, cancel)
        encode_seconds = time.perf_counter() - encode_started
        validation_started = time.perf_counter()
        checked = validate_output(temp_video, duration, has_audio, temp_srt, cancel=cancel)
        validation_seconds = time.perf_counter() - validation_started
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        os.replace(temp_srt, srt_path)
        os.replace(temp_video, output)
        return {**checked, "encoder": selected_encoder, "encoder_note": encoder_note,
                "composition_modes": [shot.get("composition", "full_frame_contain") for shot in (reframe_plan or {}).get("shots", [])],
                "reframe_plan": reframe_plan,
                "encode_seconds": round(encode_seconds, 3), "validation_seconds": round(validation_seconds, 3)}
    finally:
        temp_video.unlink(missing_ok=True)
        temp_srt.unlink(missing_ok=True)
        ass_path.unlink(missing_ok=True)


def process_video(path: Path, output_root: Path, model_size: str, clip_count: int, min_length: int, max_length: int,
                  cancel: threading.Event, progress: Callable[[str], None], encoder: str = "auto",
                  model_manager: ModelManager | None = None, semantic_enabled: bool = False,
                  vision_manager: VisionModelManager | None = None, analysis_only: bool = False,
                  release_vision_on_exit: bool = True,
                  clip_validated: Callable[[dict, Path, Path, dict], None] | None = None,
                  should_continue: Callable[[], bool] | None = None,
                  transcription_enabled: bool = True, semantic_only: bool = False) -> int:
    total_started = time.perf_counter()
    stage_timings = {}
    stage_started = time.perf_counter()
    info = probe(path)
    stage_timings["metadata_probe_seconds"] = round(time.perf_counter() - stage_started, 3)
    suffix = hashlib.sha1(str(path.resolve()).casefold().encode()).hexdigest()[:8]
    folder = output_root / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', path.stem)}_{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "analysis.json"
    cache = folder / "transcription.json"
    analysis_cache_path = folder / "analysis_cache.json"
    progress(f"Analyzing {path.name}: {info.width}×{info.height}, {info.duration:.0f}s")
    source_stat = path.stat()
    settings = {"model": model_size, "clip_count": clip_count, "min_length": min_length, "max_length": max_length,
                "encoder": encoder, "candidate_engine": "scene-aware-variable-v1",
                "semantic_enabled": semantic_enabled, "transcription_enabled": transcription_enabled,
                "editorial_engine": "editorial-v1", "local_editorial_model": os.getenv("CLIPPER_LOCAL_LLM_MODEL", ""),
                "local_editorial_endpoint": os.getenv("CLIPPER_LOCAL_LLM_ENDPOINT", ""),
                "reframing_engine": PLAN_VERSION, "storyboard_engine": STORYBOARD_VERSION,
                "content_type": os.getenv("CLIPPER_CONTENT_TYPE", "Auto"),
                "highlight_preference": os.getenv("CLIPPER_HIGHLIGHT_PREFERENCE", "Auto"),
                "clip_length_profile": os.getenv("CLIPPER_CLIP_LENGTH_PROFILE", "Auto"),
                "caption_style": os.getenv("CLIPPER_CAPTION_STYLE", "bold_pop"),
                "caption_placement": os.getenv("CLIPPER_CAPTION_PLACEMENT", "auto")}
    existing_manifest = {}
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_manifest = {}
        if not isinstance(existing_manifest, dict):
            existing_manifest = {}
        existing_clips = existing_manifest.get("clips", [])
        files_valid = isinstance(existing_clips, list) and bool(existing_clips) and all(
            isinstance(c, dict) and (candidate := _manifest_clip_path(folder, c.get("filename"))) is not None and
            candidate.is_file() and candidate.with_suffix(".srt").is_file() for c in existing_clips)
        source_valid = existing_manifest.get("source_size") == source_stat.st_size and existing_manifest.get("source_mtime_ns") == source_stat.st_mtime_ns
        selection_report = existing_manifest.get("candidate_selection")
        complete_empty = isinstance(selection_report, dict) and selection_report.get("selected_count") == 0
        resumable_partial = existing_manifest.get("queue_stage") == "READY_TO_RENDER"
        if (files_valid or complete_empty) and not resumable_partial and source_valid and existing_manifest.get("settings") == settings:
            progress(f"Already completed; skipping {path.name}")
            return 0
    if info.duration < min_length:
        manifest = {"source": path.name, "source_path": str(path), "source_size": source_stat.st_size,
                    "source_mtime_ns": source_stat.st_mtime_ns, "settings": settings, "source_duration": info.duration,
                    "metadata": asdict(info), "clips": [], "output_resolution": "1080x1920", "queue_stage": "SKIPPED",
                    "candidate_selection": {"candidate_count": 0, "rejected_count": 1, "selected_count": 0,
                                            "short_source_policy": "skip_below_minimum",
                                            "short_source_reason": f"Source duration {info.duration:.3f}s is below the configured minimum of {min_length}s.",
                                            "candidates": [], "rejections": [{"reason": "source shorter than configured minimum"}]}}
        _write_manifest(manifest_path, manifest)
        progress(f"Skipped {path.name}: {info.duration:.1f}s is shorter than the {min_length}s minimum")
        return 0
    stage_started = time.perf_counter()
    fingerprint = _analysis_fingerprint(path, source_stat)
    cached_analysis = _read_analysis_cache(analysis_cache_path, fingerprint)
    stage_timings["cache_check_seconds"] = round(time.perf_counter() - stage_started, 3)
    if cached_analysis:
        motion = cached_analysis["motion"]
        energy = cached_analysis["audio_energy"]
        cuts = cached_analysis["scene_cuts"]
        scene_status = cached_analysis["scene_detection_status"]
        analysis_cache_status = "reused"
        stage_timings["motion_frames_decoded"] = cached_analysis.get("motion_frames_decoded")
    else:
        stage_started = time.perf_counter()
        try:
            energy = _audio_energy(path, info.duration, cancel) if info.has_audio else [0.0] * max(1, math.ceil(info.duration))
            audio_analysis_valid = True
        except InterruptedError:
            raise
        except Exception as exc:
            progress(f"Audio analysis skipped for {path.name}: {exc}")
            energy = [0.0] * max(1, math.ceil(info.duration))
            audio_analysis_valid = False
        stage_timings["audio_analysis_seconds"] = round(time.perf_counter() - stage_started, 3)
        stage_started = time.perf_counter()
        motion_stats = {}
        motion = _motion(path, info.duration, cancel, motion_stats)
        stage_timings["motion_analysis_seconds"] = round(time.perf_counter() - stage_started, 3)
        stage_timings["motion_frames_decoded"] = motion_stats.get("frames_decoded")
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        stage_started = time.perf_counter()
        cuts, scene_status = _scene_cuts(path)
        stage_timings["scene_analysis_seconds"] = round(time.perf_counter() - stage_started, 3)
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        analysis_cache_status = "created"
        if audio_analysis_valid and "failed" not in scene_status and "unavailable" not in scene_status:
            _write_analysis_cache(analysis_cache_path, {"schema": 2, "fingerprint": fingerprint, "motion": motion,
                                                        "motion_frames_decoded": motion_stats.get("frames_decoded"),
                                                        "audio_energy": energy, "scene_cuts": cuts,
                                                        "scene_detection_status": scene_status})
        else:
            analysis_cache_status = "not cached because an analysis stage reported a failure"
    progress(f"Analysis signals · {analysis_cache_status}")
    progress(f"Scene detection: {scene_status}")
    progress(f"Transcribing {path.name}" if info.has_audio else f"No audio track in {path.name}")
    stage_started = time.perf_counter()
    transcript = transcribe(path, info.has_audio, model_size, cache, cancel, progress, model_manager, info.duration,
                            allow_inference=transcription_enabled)
    stage_timings["transcription_seconds"] = round(time.perf_counter() - stage_started, 3)
    if transcript.outcome == "CANCELLED":
        raise InterruptedError(transcript.message or "Processing cancelled")
    words = transcript.words
    if transcript.status == "failed":
        progress(f"Transcription failed ({transcript.device}/{transcript.precision}): {transcript.message}")
    elif transcript.status == "unavailable":
        progress(f"Transcription unavailable: {transcript.message}")
    else:
        progress(f"Transcription {transcript.status} · {transcript.device}/{transcript.precision} · {len(words)} words")
    stage_started = time.perf_counter()
    selection = select_clips(info.duration, motion, energy, cuts, words, clip_count, min_length, max_length, cancel=cancel)
    stage_timings["candidate_selection_seconds"] = round(time.perf_counter() - stage_started, 3)
    editorial_summary = {"status": "unavailable", "provider": None, "prompt_version": "editorial-v1",
                         "quality_floor": QUALITY_FLOOR, "assessed": 0,
                         "reason": "Local narrative endpoint not configured"}
    try:
        endpoint = local_endpoint()
    except ValueError as exc:
        endpoint = None
        editorial_summary["reason"] = str(exc)
    if endpoint and words:
        provider_model = os.getenv("CLIPPER_LOCAL_LLM_MODEL", "").strip()
        if not provider_model:
            editorial_summary["status"] = "unavailable"
            editorial_summary["reason"] = "CLIPPER_LOCAL_LLM_MODEL is not configured"
        else:
            editorial_summary.update(status="local_model", provider="OpenAI-compatible loopback",
                                     model=provider_model, endpoint=endpoint)
            ranked = sorted(selection["candidates"], key=lambda item: item.get("score", 0), reverse=True)[:12]
            cache_path = folder / "editorial_cache.json"
            try:
                editorial_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                editorial_cache = {"version": "editorial-v1", "entries": {}}
            if editorial_cache.get("version") != "editorial-v1" or not isinstance(editorial_cache.get("entries"), dict):
                editorial_cache = {"version": "editorial-v1", "entries": {}}
            for candidate in ranked:
                if cancel.is_set():
                    raise InterruptedError("Processing cancelled")
                transcript_key = " ".join(word.text for word in words if word.end > candidate["start"] and word.start < candidate["end"])
                key = hashlib.sha256(json.dumps([fingerprint, endpoint, provider_model, candidate["start"], candidate["end"],
                                                  hashlib.sha256(transcript_key.encode()).hexdigest(),
                                                  editorial_summary["prompt_version"]]).encode()).hexdigest()
                try:
                    assessment = editorial_cache["entries"].get(key)
                    if assessment is None:
                        progress(f"Local editorial assessment · {candidate['start']:.1f}–{candidate['end']:.1f}s")
                        assessment = assess_candidate(endpoint, provider_model, words, candidate["start"],
                                                      candidate["end"], transcript.language)
                        editorial_cache["entries"][key] = assessment
                        _write_manifest(cache_path, editorial_cache)
                    apply_assessment(candidate, assessment)
                    candidate["editorial_category"] = assessment["category"]
                    candidate["score"] = round(.4 * candidate["score"] + .6 * assessment["editorial_score"], 4)
                    editorial_summary["assessed"] += 1
                except Exception as exc:
                    editorial_summary.update(status="partial_fallback", reason=f"{type(exc).__name__}: {exc}")
                    progress(f"Narrative AI unavailable for candidate; retaining activity score: {exc}")
            if editorial_summary["assessed"]:
                eligible = [item for item in selection["candidates"] if not item.get("editorial_assessed") or item.get("editorial_selected")]
                unique, rejected_editorial = deduplicate_candidates(eligible, cancel=cancel)
                selected, rejected_diversity = select_diverse_candidates(unique, clip_count, info.duration, cancel=cancel)
                for item in selection["candidates"]:
                    item["selected"] = item in selected
                selection["selected"] = selected
                selection["selected_count"] = len(selected)
                selection["rejections"].extend({"start": item.get("start"), "end": item.get("end"),
                                                "reason": "editorial quality floor"} for item in selection["candidates"]
                                               if item.get("editorial_assessed") and not item.get("editorial_selected"))
                selection["rejections"].extend(rejected_editorial)
                selection["rejections"].extend(rejected_diversity)
                selection["rejected_count"] = len(selection["rejections"])
    semantic_summary = {"enabled": semantic_enabled,
                        "status": "deferred" if analysis_only and semantic_enabled else "disabled",
                        "model": None, "analyzed": 0}
    if semantic_enabled:
        vision = vision_manager or VisionModelManager()
        semantic_cache_path = folder / "semantic_cache.json"
        semantic_cache = read_semantic_cache(semantic_cache_path)
        ranked = [candidate for candidate in selection["candidates"] if candidate.get("boundary_valid", True) and candidate.get("score", 0) >= 0.075]
        ranked = sorted(ranked, key=lambda item: item["score"], reverse=True)[:min(12, max(8, clip_count * 2))]
        semantic_summary.update(status="heuristic_fallback", model="HuggingFaceTB/SmolVLM2-500M-Video-Instruct",
                                category_diversity_weight=SEMANTIC_CATEGORY_DIVERSITY_WEIGHT,
                                model_lifecycle=("Whisper released before vision; vision reused through the current microbatch"
                                                 if not release_vision_on_exit else
                                                 "Whisper released before vision; vision released after this source"))
        missing_ranked = []
        for candidate in ranked:
            transcript_text = " ".join(word.text for word in words if candidate["start"] <= word.start < candidate["end"])
            transcript_for_model = transcript_text if transcript.language == "en" else ""
            key = semantic_cache_key(path, candidate, transcript_for_model, transcript.language)
            if semantic_cache["entries"].get(key) is None:
                missing_ranked.append((candidate, transcript_for_model, key))
        semantic_started = time.perf_counter()
        try:
            if analysis_only and not semantic_only:
                pass
            else:
                if missing_ranked:
                    # Never keep the Whisper model resident while loading a vision model on the same GPU.
                    (model_manager or _default_model_manager(model_size)).release_whisper()
                    vision_state = vision.status()
                    if vision.model is None or not isinstance(vision_state, dict):
                        vision_started = time.perf_counter()
                        semantic_summary["device"] = vision.initialize().get("device")
                        semantic_summary["vision_initialization_seconds"] = round(time.perf_counter() - vision_started, 3)
                    else:
                        semantic_summary["device"] = vision_state.get("device")
                        semantic_summary["vision_reused"] = True
                else:
                    semantic_summary["device"] = "cache"
                for index, candidate in enumerate(ranked, 1):
                    if cancel.is_set():
                        raise InterruptedError("Processing cancelled")
                    transcript_text = " ".join(word.text for word in words if candidate["start"] <= word.start < candidate["end"])
                    transcript_for_model = transcript_text if transcript.language == "en" else ""
                    key = semantic_cache_key(path, candidate, transcript_for_model, transcript.language)
                    result = semantic_cache["entries"].get(key)
                    if result is None:
                        progress(f"Semantic analysis · candidate {index}/{len(ranked)}")
                        interval_peaks = []
                        for signal in (motion, energy):
                            lo = max(0, int(candidate["start"]))
                            hi = min(len(signal), math.ceil(candidate["end"]))
                            if hi > lo:
                                interval_peaks.extend(sorted(range(lo, hi), key=lambda i: signal[i], reverse=True)[:2])
                        event_times = [float(value) for value in cuts if candidate["start"] <= value < candidate["end"]]
                        event_times.extend(float(value) + .5 for value in interval_peaks)
                        frames, temp_dir = extract_candidate_frames(path, candidate["start"], candidate["end"],
                                                                    cancel, event_times, 12)
                        try:
                            candidate["sampled_frame_timestamps"] = [frame["timestamp"] for frame in frames]
                            semantic_summary["frames_sampled"] = semantic_summary.get("frames_sampled", 0) + len(frames)
                            if len(frames) < 2:
                                raise ValueError("Fewer than two valid distinct frames were extracted")
                            inference_started = time.perf_counter()
                            result = vision.analyze(frames, transcript_for_model, transcript.language)
                            semantic_summary["inference_seconds"] = round(semantic_summary.get("inference_seconds", 0) + time.perf_counter() - inference_started, 3)
                        finally:
                            import shutil
                            shutil.rmtree(temp_dir, ignore_errors=True)
                        semantic_cache["entries"][key] = result
                        write_semantic_cache(semantic_cache_path, semantic_cache)
                    candidate["heuristic_score"] = candidate["score"]
                    candidate["semantic_score"] = result["score"]
                    candidate["semantic_category"] = result["category"]
                    candidate["semantic_reason"] = result["reason"]
                    candidate["semantic_evidence"] = result["evidence"]
                    candidate["score"] = round(0.65 * candidate["heuristic_score"] + 0.35 * result["score"], 4)
                    semantic_summary["analyzed"] += 1
                    progress(f"Semantic result · {result['category']} · model {result['score']:.2f} · combined {candidate['score']:.2f}")
                if semantic_summary["analyzed"]:
                    unique, _ = deduplicate_candidates(selection["candidates"])
                    semantic_selected, _ = select_diverse_candidates(
                        unique, clip_count, info.duration, category_weight=SEMANTIC_CATEGORY_DIVERSITY_WEIGHT)
                    for candidate in selection["candidates"]:
                        candidate["selected"] = candidate in semantic_selected
                    selection["selected"] = semantic_selected
                    selection["selected_count"] = len(semantic_selected)
                    selection["category_diversity_weight"] = SEMANTIC_CATEGORY_DIVERSITY_WEIGHT
                    semantic_summary["status"] = "completed"
        except InterruptedError:
            raise
        except Exception as exc:
            for candidate in selection["candidates"]:
                if "heuristic_score" in candidate:
                    candidate["score"] = candidate.pop("heuristic_score")
                    for field in ("semantic_score", "semantic_category", "semantic_reason", "semantic_evidence"):
                        candidate.pop(field, None)
            semantic_summary.update(status="heuristic_fallback", reason=f"{type(exc).__name__}: {exc}")
            progress(f"Semantic analysis unavailable; using heuristic ranking: {exc}")
        finally:
            if release_vision_on_exit:
                vision.release()
        stage_timings["semantic_analysis_seconds"] = round(time.perf_counter() - semantic_started, 3)
    clips = selection["selected"]
    for candidate_id, candidate in enumerate(selection["candidates"], 1):
        candidate["candidate_id"] = f"candidate_{candidate_id:03}"
    storyboard_summary = {"status": "unavailable", "path": None, "candidate_count": 0}
    try:
        storyboard_path = write_candidate_storyboard(path, folder / "candidate_storyboard", selection, words, cuts)
        if storyboard_path:
            storyboard_summary = {"status": "created", "path": str(storyboard_path.relative_to(folder)),
                                  "candidate_count": min(8, len(selection["candidates"]))}
    except InterruptedError:
        raise
    except Exception as exc:
        storyboard_summary["reason"] = f"{type(exc).__name__}: {exc}"
        progress(f"Candidate storyboard unavailable: {exc}")
    durations = ", ".join(f"{clip['duration']:.0f}s" for clip in clips) or "none"
    progress(f"Candidates · {selection['candidate_count']} generated, {len(clips)} selected · {durations}")
    candidate_report = {"candidate_count": selection["candidate_count"], "rejected_count": selection["rejected_count"],
                        "selected_count": len(clips), "short_source_policy": selection["short_source_policy"],
                        "category_diversity_weight": selection.get("category_diversity_weight", 0),
                        "activity_regions": selection["activity_regions"], "speech_regions": selection["speech_regions"],
                        "candidates": selection["candidates"], "rejections": selection["rejections"]}
    existing_by_interval = ({(round(item.get("start", -1), 3), round(item.get("end", -1), 3)): item
                             for item in existing_manifest.get("clips", []) if isinstance(item, dict)}
                            if existing_manifest.get("settings") == settings else {})
    report_clips = []
    review_artifacts = {"clip_review": "clip_review.md", "contact_sheet": None}
    processing_phase = "semantic" if semantic_only else "analysis" if analysis_only else "render"
    def persist(queue_stage: str):
        stage_timings["episode_total_seconds"] = round(time.perf_counter() - total_started, 3)
        stage_history = existing_manifest.get("stage_timings_by_pass", {})
        if not isinstance(stage_history, dict):
            stage_history = {}
        stage_history[processing_phase] = dict(stage_timings)
        manifest = {"source": path.name, "source_path": str(path), "source_size": source_stat.st_size,
                    "source_mtime_ns": source_stat.st_mtime_ns, "settings": settings, "source_duration": info.duration,
                    "metadata": asdict(info), "language": transcript.language, "output_resolution": "1080x1920",
                    "clips": report_clips, "candidate_selection": candidate_report, "editorial_analysis": editorial_summary,
                    "semantic_analysis": semantic_summary, "candidate_storyboard": dict(storyboard_summary),
                    "review_artifacts": dict(review_artifacts),
                    "queue_stage": queue_stage, "stage_timings": dict(stage_timings),
                    "stage_timings_by_pass": stage_history,
                    "analysis_cache_status": analysis_cache_status, "scene_cuts": cuts,
                    "scene_detection_status": scene_status, "transcription_words": len(words),
                    "transcription_available": bool(words), "transcription_status": transcript.status,
                    "transcription_outcome": transcript.outcome, "transcription_model": transcript.model,
                    "transcription_device": transcript.device, "transcription_precision": transcript.precision,
                    "transcription_message": transcript.message,
                    "transcription_initialization_seconds": transcript.initialization_seconds,
                    "transcription_seconds": transcript.transcription_seconds,
                    "transcription_audio_seconds": transcript.audio_seconds, "transcription_rtf": transcript.real_time_factor,
                    "transcription_vram_before": transcript.vram_before, "transcription_vram_after": transcript.vram_after,
                    "transcription_peak_vram": transcript.peak_vram}
        _write_manifest(manifest_path, manifest)
        review = [f"# Clip review · {path.name}", "", f"Source duration: {info.duration:.3f}s",
                  f"Narrative analysis: {editorial_summary['status']}", ""]
        if (folder / "clip_contact_sheet.png").is_file():
            review.append("Contact sheet: [clip_contact_sheet.png](clip_contact_sheet.png)")
            review.append("")
        for index, item in enumerate(report_clips, 1):
            editorial = item.get("editorial", {})
            review.extend([f"## Clip {index:03} · {item.get('category', 'unclassified')}", "",
                           f"- Source time: {item.get('start', 0):.3f}–{item.get('end', 0):.3f}s",
                           f"- Duration: {item.get('duration', 0):.3f}s",
                           f"- Status: {item.get('validation', item.get('status', 'pending'))}",
                           f"- Editorial score: {editorial.get('editorial_score', 'not assessed')}",
                           f"- Reason: {item.get('reason', 'combined activity signals')}",
                           f"- File: {item.get('filename', 'not rendered')}", ""])
        (folder / "clip_review.md").write_text("\n".join(review), encoding="utf-8")
    if analysis_only:
        persist("READY_TO_RENDER" if semantic_only and semantic_summary.get("status") in {"completed", "heuristic_fallback"} else "ANALYZED")
        return 0

    render_started = time.perf_counter()
    for index, clip in enumerate(clips, 1):
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        if should_continue and not should_continue():
            break
        filename = f"clip_{index:03}.mp4"
        previous = existing_by_interval.get((round(clip["start"], 3), round(clip["end"], 3)))
        if previous:
            previous_mp4 = _manifest_clip_path(folder, previous.get("filename"))
            if previous_mp4 is None:
                previous_mp4 = folder / filename
                previous = None
        if previous:
            previous_filename = previous_mp4.name
            previous_srt = previous_mp4.with_suffix(".srt")
            try:
                checked = validate_output(previous_mp4, clip["end"] - clip["start"], info.has_audio, previous_srt, cancel=cancel)
                report_clips.append({**previous, **clip, "filename": previous_filename,
                                     "duration": round(clip["end"] - clip["start"], 3),
                                     "category": clip.get("editorial_category", clip.get("semantic_category", "mixed activity")),
                                     "status": "completed", "validation": "VALIDATED",
                                     "render": previous.get("render", checked)})
                progress(f"Reusing validated output {previous_filename} for {path.name}")
                persist("RENDERING")
                continue
            except Exception:
                pass
        if should_continue and not should_continue():
            break
        progress(f"Rendering clip {index}/{len(clips)} from {path.name}")
        clip_started = time.perf_counter()
        try:
            reframe_plan = build_reframe_plan(path, clip["start"], clip["end"], cuts, cancel)
        except InterruptedError:
            raise
        except Exception as exc:
            progress(f"Smart framing fallback · {type(exc).__name__}: {exc}")
            from reframing import shot_intervals
            reframe_plan = {"version": PLAN_VERSION, "status": "fallback", "source_width": info.width,
                            "source_height": info.height,
                            "shots": [{"shot_id": shot_id, "start": shot_start, "end": shot_end,
                                       "composition": "full_frame_contain", "crop": None, "focus_path": [],
                                       "obstructions": [], "evidence": str(exc)}
                                      for shot_id, (shot_start, shot_end) in enumerate(
                                          shot_intervals(clip["start"], clip["end"], cuts))]}
        debug_image = write_reframe_debug(path, reframe_plan, folder / "reframe_debug" / f"clip_{index:03}.png")
        reframe_plan["debug_image"] = str(debug_image.relative_to(folder)) if debug_image else None
        caption_plan = shot_placements(reframe_plan.get("shots", []), os.getenv("CLIPPER_CAPTION_PLACEMENT", "auto"))
        for shot, placement in zip(reframe_plan.get("shots", []), caption_plan):
            shot["caption_placement"] = placement
        render_info = render(path, folder / filename, info, clip, words, info.has_audio, cancel, encoder,
                             reframe_plan, caption_plan)
        stage_timings[f"clip_{index:03}_render_call_seconds"] = round(time.perf_counter() - clip_started, 3)
        stage_timings[f"clip_{index:03}_encoding_seconds"] = render_info.get("encode_seconds", 0)
        stage_timings[f"clip_{index:03}_validation_seconds"] = render_info.get("validation_seconds", 0)
        progress(f"Validated clip_{index:03}.mp4 · {render_info['encoder']} · {render_info['encoder_note']}")
        report_clip = {"filename": filename, **clip, "duration": round(clip["end"] - clip["start"], 3),
                       "category": clip.get("editorial_category", clip.get("semantic_category", "mixed activity")), "status": "completed",
                       "validation": "VALIDATED", "reframing": reframe_plan,
                       "caption_placements": caption_plan, "caption_style": settings["caption_style"],
                       "caption_placement": settings["caption_placement"], "render": render_info}
        report_clips.append(report_clip)
        persist("RENDERING")
        if clip_validated:
            clip_validated(report_clip, folder / filename, (folder / filename).with_suffix(".srt"), render_info)
    stage_timings["rendering_seconds"] = round(time.perf_counter() - render_started, 3)
    complete = len(report_clips) >= len(clips)
    if complete and report_clips:
        contact_sheet = _write_contact_sheet(folder, report_clips)
        if contact_sheet:
            review_artifacts["contact_sheet"] = contact_sheet.name
            progress(f"Review contact sheet · {contact_sheet.name}")
    persist("COMPLETED" if complete else "READY_TO_RENDER")
    return len(report_clips)
