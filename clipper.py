"""Local video analysis and Shorts rendering pipeline."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


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


def scan_videos(folder: Path) -> list[Path]:
    return sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS), key=lambda p: str(p).casefold())


def _run(args: list[str], cancel: threading.Event | None = None, **kwargs) -> subprocess.CompletedProcess:
    if cancel and cancel.is_set():
        raise InterruptedError("Processing cancelled")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
    while proc.poll() is None:
        if cancel and cancel.wait(0.2):
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise InterruptedError("Processing cancelled")
    stdout, stderr = proc.communicate()
    result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    if result.returncode:
        raise RuntimeError(result.stderr[-2500:] or f"Command failed: {args[0]}")
    return result


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
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
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
        if proc.wait() and not sample_offset:
            raise RuntimeError(err[-1000:] or "Could not decode audio")
    finally:
        if proc.poll() is None:
            proc.kill()
    return [math.sqrt(totals[i] / counts[i]) if counts[i] else 0.0 for i in range(bins)]


def _motion(path: Path, duration: float, cancel: threading.Event) -> list[float]:
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return [0.0] * max(1, math.ceil(duration))
    values = [0.0] * max(1, math.ceil(duration))
    previous = None
    second = -1
    try:
        while True:
            if cancel.is_set():
                raise InterruptedError("Processing cancelled")
            ok, frame = cap.read()
            if not ok:
                break
            pos = max(0, int(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000))
            if pos != second:
                gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
                if previous is not None and second < len(values):
                    values[second] = float(cv2.absdiff(previous, gray).mean())
                previous, second = gray, pos
    finally:
        cap.release()
    return values


def _scene_cuts(path: Path) -> list[float]:
    try:
        from scenedetect import ContentDetector, detect
        scenes = detect(str(path), ContentDetector(threshold=27.0), show_progress=False)
        return [float(a.get_seconds()) for a, _ in scenes[1:]]
    except Exception:
        return []


def transcribe(path: Path, has_audio: bool, model_size: str, cache: Path, cancel: threading.Event) -> tuple[str | None, list[Word]]:
    if not has_audio:
        return None, []
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        stat = path.stat()
        if data.get("source_size") == stat.st_size and data.get("source_mtime_ns") == stat.st_mtime_ns:
            return data.get("language"), [Word(**w) for w in data.get("words", [])]
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None, []
    try:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda = False
    try:
        model = WhisperModel(model_size, device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8")
    except Exception:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    words = []
    for segment in segments:
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        for word in segment.words or []:
            if word.start is not None and word.end is not None:
                words.append(Word(float(word.start), float(word.end), word.word))
    cache.parent.mkdir(parents=True, exist_ok=True)
    stat = path.stat()
    cache.write_text(json.dumps({"language": info.language, "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns, "words": [asdict(w) for w in words]}, ensure_ascii=False), encoding="utf-8")
    return info.language, words


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [0.0] * len(values)
    return [(v - low) / (high - low) for v in values]


def rank_clips(duration: float, motion: list[float], audio: list[float], cuts: list[float], words: list[Word], count: int = 5, min_length: int = 20, max_length: int = 55) -> list[dict]:
    length = min(max_length, duration)
    if length < min_length:
        length = duration
    if length <= 0:
        return []
    nm, na = _normalize(motion), _normalize(audio)
    step = 5.0
    candidates = []
    latest = max(0.0, duration - length)
    starts = [i * step for i in range(math.ceil(latest / step) + 1)] or [0.0]
    if starts[-1] < latest:
        starts.append(latest)
    for start in starts:
        end = min(duration, start + length)
        a, b = max(0, int(start)), max(1, math.ceil(end))
        m = sum(nm[a:min(b, len(nm))]) / max(1, min(b, len(nm)) - a)
        e = sum(na[a:min(b, len(na))]) / max(1, min(b, len(na)) - a)
        cut_score = min(1.0, sum(start < c < end for c in cuts) / max(1, length / 12))
        speech = sum(start <= w.start < end for w in words) / max(1, length * 2)
        score = 0.38 * m + 0.30 * e + 0.18 * min(1, speech) + 0.14 * cut_score
        candidates.append({"start": start, "end": end, "score": round(score, 4), "signals": {"motion": round(m, 3), "audio_energy": round(e, 3), "speech_density": round(min(1, speech), 3), "scene_changes": round(cut_score, 3)}})
    selected = []
    for _ in range(min(count, len(candidates))):
        def rank(candidate: dict) -> float:
            center = (candidate["start"] + candidate["end"]) / 2
            nearest = min((abs(center - (s["start"] + s["end"]) / 2) for s in selected), default=duration)
            return candidate["score"] * (0.4 + 0.6 * min(1, nearest / max(1, duration / 4)))
        remaining = [c for c in candidates if c not in selected]
        spaced = [c for c in remaining if all(min(c["end"], s["end"]) - max(c["start"], s["start"]) <= length * 0.5 for s in selected)]
        best = max(spaced or remaining, key=rank)
        selected.append(best)
    selected.sort(key=lambda c: c["start"])
    for c in selected:
        nearby = [w for w in words if c["start"] <= w.start < c["end"]]
        if nearby:
            c["start"] = max(0, max((w.end for w in words if c["start"] - 1.5 <= w.end <= c["start"]), default=c["start"]))
            c["end"] = min(duration, min((w.start for w in words if c["end"] <= w.start <= c["end"] + 1.5), default=c["end"]))
        reasons = [name.replace("_", " ") for name, val in c["signals"].items() if val >= 0.35]
        c["reason"] = "Strong " + (", ".join(reasons) if reasons else "combined audio and visual activity")
    return selected


def _ass_text(words: list[Word], start: float, end: float) -> str:
    selected = [w for w in words if w.end > start and w.start < end]
    events = []
    for offset in range(0, len(selected), 7):
        line = selected[offset:offset + 7]
        text = "".join(f"{{\\kf{max(1, round((w.end - w.start) * 100))}}}{_ass_escape(w.text)}" for w in line)
        events.append(f"Dialogue: 0,{_ass_time(max(0, line[0].start-start))},{_ass_time(min(end-start, line[-1].end-start))},Default,,0,0,0,,{text.strip()}")
    return "\n".join(["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding", "Style: Default,Arial,66,&H00FFFFFF,&H0000D7FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,250,1", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text", *events])


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ").replace("\r", " ")


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(seconds * 100))
    hours, centiseconds = divmod(centiseconds, 360000)
    minutes, centiseconds = divmod(centiseconds, 6000)
    secs, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centiseconds:02}"


def _srt(words: list[Word], start: float, end: float) -> str:
    selected = [w for w in words if w.end > start and w.start < end]
    chunks = [selected[i:i + 7] for i in range(0, len(selected), 7)]
    def stamp(s: float) -> str:
        ms = max(0, int(s * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    return "\n\n".join(f"{i}\n{stamp(max(0,c[0].start-start))} --> {stamp(min(end-start,c[-1].end-start))}\n{' '.join(w.text.strip() for w in c).strip()}" for i, c in enumerate(chunks, 1))


def _escape_filter(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render(path: Path, output: Path, info: VideoInfo, clip: dict, words: list[Word], has_audio: bool, cancel: threading.Event) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    srt_path = output.with_suffix(".srt")
    ass_path = output.with_suffix(".ass")
    srt_path.write_text(_srt(words, clip["start"], clip["end"]), encoding="utf-8")
    ass_path.write_text(_ass_text(words, clip["start"], clip["end"]), encoding="utf-8-sig")
    # Keep the entire source image visible, using a blurred fill behind it.
    filt = f"[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:10[bg2];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];[bg2][fg2]overlay=(W-w)/2:(H-h)/2,subtitles='{_escape_filter(ass_path)}',fps=30,format=yuv420p[v]"
    cmd = ["ffmpeg", "-y", "-ss", f"{clip['start']:.3f}", "-i", str(path), "-t", f"{clip['end']-clip['start']:.3f}", "-filter_complex", filt, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", "-shortest", str(output)]
    _run(cmd, cancel)
    ass_path.unlink(missing_ok=True)
    checked = _run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type,width,height:format=duration,format_name", "-of", "json", str(output)])
    data = json.loads(checked.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    if (video.get("width"), video.get("height"), video.get("codec_name")) != (1080, 1920, "h264"):
        raise RuntimeError("Rendered output did not pass 1080x1920 H.264 validation")
    if has_audio and not any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in data.get("streams", [])):
        raise RuntimeError("Rendered audio did not pass AAC validation")
    if float(data.get("format", {}).get("duration", 0)) <= 0 or "mp4" not in data.get("format", {}).get("format_name", ""):
        raise RuntimeError("Rendered output did not pass MP4 duration validation")


def process_video(path: Path, output_root: Path, model_size: str, clip_count: int, min_length: int, max_length: int,
                  cancel: threading.Event, progress: Callable[[str], None]) -> int:
    info = probe(path)
    suffix = hashlib.sha1(str(path.resolve()).casefold().encode()).hexdigest()[:8]
    folder = output_root / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', path.stem)}_{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "analysis.json"
    cache = folder / "transcription.json"
    progress(f"Analyzing {path.name}: {info.width}×{info.height}, {info.duration:.0f}s")
    source_stat = path.stat()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files_valid = bool(manifest.get("clips")) and all((folder / c.get("filename", "")).is_file() for c in manifest["clips"])
        source_valid = manifest.get("source_size") == source_stat.st_size and manifest.get("source_mtime_ns") == source_stat.st_mtime_ns
        if files_valid and source_valid and len(manifest["clips"]) == clip_count:
            progress(f"Already completed; skipping {path.name}")
            return 0
    energy = _audio_energy(path, info.duration, cancel) if info.has_audio else [0.0] * max(1, math.ceil(info.duration))
    motion = _motion(path, info.duration, cancel)
    cuts = _scene_cuts(path)
    progress(f"Transcribing {path.name}" if info.has_audio else f"No audio track in {path.name}")
    language, words = transcribe(path, info.has_audio, model_size, cache, cancel)
    clips = rank_clips(info.duration, motion, energy, cuts, words, clip_count, min_length, max_length)
    report_clips = []
    for i, clip in enumerate(clips, 1):
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        filename = f"clip_{i:03}.mp4"
        progress(f"Rendering {filename} from {path.name}")
        render(path, folder / filename, info, clip, words, info.has_audio, cancel)
        report_clips.append({"filename": filename, **clip, "duration": round(clip["end"] - clip["start"], 3), "category": "mixed activity", "status": "completed"})
    manifest = {"source": path.name, "source_path": str(path), "source_size": source_stat.st_size, "source_mtime_ns": source_stat.st_mtime_ns, "source_duration": info.duration, "metadata": asdict(info), "language": language, "output_resolution": "1080x1920", "clips": report_clips, "scene_cuts": cuts, "transcription_words": len(words), "transcription_available": bool(words)}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(report_clips)
