"""Local video analysis and Shorts rendering pipeline."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from diagnostics import ffmpeg_path, ffprobe_path, verify_encoder

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


@dataclass
class TranscriptionResult:
    language: str | None
    words: list[Word]
    status: str
    device: str
    precision: str
    message: str = ""


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


def _motion(path: Path, duration: float, cancel: threading.Event) -> list[float]:
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


def transcribe(path: Path, has_audio: bool, model_size: str, cache: Path, cancel: threading.Event,
               progress: Callable[[str], None] | None = None) -> TranscriptionResult:
    if not has_audio:
        return TranscriptionResult(None, [], "no_audio_track", "none", "none")
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            stat = path.stat()
            if data.get("source_size") == stat.st_size and data.get("source_mtime_ns") == stat.st_mtime_ns:
                words = [Word(**w) for w in data.get("words", [])]
                status = "transcribed" if words else "no_speech"
                return TranscriptionResult(data.get("language"), words, status, "cache", "cache")
        except (OSError, ValueError, TypeError, KeyError):
            pass
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        return TranscriptionResult(None, [], "unavailable", "none", "none", str(exc))
    try:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda = False
    device, precision = ("cuda", "int8_float16") if cuda else ("cpu", "int8")
    fallback_message = ""
    try:
        try:
            model = WhisperModel(model_size, device=device, compute_type=precision)
        except Exception as exc:
            if not cuda:
                raise
            fallback_message = f"CUDA {precision} unavailable ({type(exc).__name__}: {exc}); retrying CPU int8"
            if progress:
                progress(fallback_message)
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            device, precision = "cpu", "int8"
        segments, info = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
        words = []
        for segment in segments:
            if cancel.is_set():
                raise InterruptedError("Processing cancelled")
            for word in segment.words or []:
                if word.start is not None and word.end is not None:
                    words.append(Word(float(word.start), float(word.end), word.word))
    except InterruptedError:
        raise
    except Exception as exc:
        return TranscriptionResult(None, [], "failed", device, precision, f"{type(exc).__name__}: {exc}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    stat = path.stat()
    status = "transcribed" if words else "no_speech"
    cache.write_text(json.dumps({"language": info.language, "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns, "device": device, "precision": precision, "words": [asdict(w) for w in words]}, ensure_ascii=False), encoding="utf-8")
    return TranscriptionResult(info.language, words, status, device, precision, fallback_message)


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [0.0] * len(values)
    return [(v - low) / (high - low) for v in values]


def rank_clips(duration: float, motion: list[float], audio: list[float], cuts: list[float], words: list[Word], count: int = 5, min_length: int = 20, max_length: int = 55) -> list[dict]:
    min_length = max(1, int(min_length))
    max_length = max(min_length, int(max_length))
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
        original_start, original_end = c["start"], c["end"]
        nearby = [w for w in words if c["start"] <= w.start < c["end"]]
        if nearby:
            c["start"] = max(0, max((w.end for w in words if c["start"] - 1.5 <= w.end <= c["start"]), default=c["start"]))
            c["end"] = min(duration, min((w.start for w in words if c["end"] <= w.start <= c["end"] + 1.5), default=c["end"]))
        if not 0 <= c["start"] < c["end"] <= duration or c["end"] - c["start"] > max_length:
            c["start"], c["end"] = original_start, original_end
        reasons = [name.replace("_", " ") for name, val in c["signals"].items() if val >= 0.35]
        c["reason"] = "Strong " + (", ".join(reasons) if reasons else "combined audio and visual activity")
    return selected


def _ass_text(words: list[Word], start: float, end: float) -> str:
    selected = [w for w in words if w.end > start and w.start < end]
    events = []
    for offset in range(0, len(selected), 7):
        line = selected[offset:offset + 7]
        words_text = [f"{{\\kf{max(1, round((w.end - w.start) * 100))}}}{_ass_escape(w.text)}" for w in line]
        text = "".join(words_text[:4]) + ("\\N" + "".join(words_text[4:]) if len(words_text) > 4 else "")
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


def render(path: Path, output: Path, info: VideoInfo, clip: dict, words: list[Word], has_audio: bool,
           cancel: threading.Event, encoder: str = "auto") -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    srt_path = output.with_suffix(".srt")
    temp_video = output.with_name(output.stem + ".partial" + output.suffix)
    temp_srt = temp_video.with_suffix(".srt")
    ass_path = temp_video.with_suffix(".ass")
    duration = clip["end"] - clip["start"]
    selected_encoder, encoder_note = select_encoder(encoder)
    temp_srt.write_text(_srt(words, clip["start"], clip["end"]), encoding="utf-8")
    ass_path.write_text(_ass_text(words, clip["start"], clip["end"]), encoding="utf-8-sig")
    try:
        # Keep the full source frame visible over a blurred fill.
        filt = f"[0:v]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:10[bg2];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];[bg2][fg2]overlay=(W-w)/2:(H-h)/2,subtitles='{_escape_filter(ass_path)}',fps=30,format=yuv420p[v]"
        cmd = ["ffmpeg", "-y", "-ss", f"{clip['start']:.3f}", "-i", str(path), "-t", f"{duration:.3f}", "-filter_complex", filt, "-map", "[v]"]
        if has_audio:
            cmd += ["-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        cmd += ["-c:v", selected_encoder]
        cmd += ["-preset", "p4", "-cq", "22", "-b:v", "0"] if selected_encoder == "h264_nvenc" else ["-preset", "veryfast", "-crf", "20"]
        cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", str(temp_video)]
        _run(cmd, cancel)
        checked = validate_output(temp_video, duration, has_audio, temp_srt, cancel=cancel)
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        os.replace(temp_srt, srt_path)
        os.replace(temp_video, output)
        return {**checked, "encoder": selected_encoder, "encoder_note": encoder_note}
    finally:
        temp_video.unlink(missing_ok=True)
        temp_srt.unlink(missing_ok=True)
        ass_path.unlink(missing_ok=True)


def process_video(path: Path, output_root: Path, model_size: str, clip_count: int, min_length: int, max_length: int,
                  cancel: threading.Event, progress: Callable[[str], None], encoder: str = "auto") -> int:
    info = probe(path)
    suffix = hashlib.sha1(str(path.resolve()).casefold().encode()).hexdigest()[:8]
    folder = output_root / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', path.stem)}_{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "analysis.json"
    cache = folder / "transcription.json"
    progress(f"Analyzing {path.name}: {info.width}×{info.height}, {info.duration:.0f}s")
    source_stat = path.stat()
    settings = {"model": model_size, "clip_count": clip_count, "min_length": min_length, "max_length": max_length, "encoder": encoder}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files_valid = bool(manifest.get("clips")) and all((folder / c.get("filename", "")).is_file() for c in manifest["clips"])
        source_valid = manifest.get("source_size") == source_stat.st_size and manifest.get("source_mtime_ns") == source_stat.st_mtime_ns
        if files_valid and source_valid and manifest.get("settings") == settings and len(manifest["clips"]) == clip_count:
            progress(f"Already completed; skipping {path.name}")
            return 0
    try:
        energy = _audio_energy(path, info.duration, cancel) if info.has_audio else [0.0] * max(1, math.ceil(info.duration))
    except InterruptedError:
        raise
    except Exception as exc:
        progress(f"Audio analysis skipped for {path.name}: {exc}")
        energy = [0.0] * max(1, math.ceil(info.duration))
    motion = _motion(path, info.duration, cancel)
    cuts, scene_status = _scene_cuts(path)
    progress(f"Scene detection: {scene_status}")
    progress(f"Transcribing {path.name}" if info.has_audio else f"No audio track in {path.name}")
    transcript = transcribe(path, info.has_audio, model_size, cache, cancel, progress)
    words = transcript.words
    if transcript.status == "failed":
        progress(f"Transcription failed ({transcript.device}/{transcript.precision}): {transcript.message}")
    elif transcript.status == "unavailable":
        progress(f"Transcription unavailable: {transcript.message}")
    else:
        progress(f"Transcription {transcript.status} · {transcript.device}/{transcript.precision} · {len(words)} words")
    clips = rank_clips(info.duration, motion, energy, cuts, words, clip_count, min_length, max_length)
    report_clips = []
    for i, clip in enumerate(clips, 1):
        if cancel.is_set():
            raise InterruptedError("Processing cancelled")
        filename = f"clip_{i:03}.mp4"
        progress(f"Rendering {filename} from {path.name}")
        render_info = render(path, folder / filename, info, clip, words, info.has_audio, cancel, encoder)
        progress(f"Encoded with {render_info['encoder']} · {render_info['encoder_note']}")
        report_clips.append({"filename": filename, **clip, "duration": round(clip["end"] - clip["start"], 3), "category": "mixed activity", "status": "completed", "render": render_info})
    manifest = {"source": path.name, "source_path": str(path), "source_size": source_stat.st_size, "source_mtime_ns": source_stat.st_mtime_ns, "settings": settings, "source_duration": info.duration, "metadata": asdict(info), "language": transcript.language, "output_resolution": "1080x1920", "clips": report_clips, "scene_cuts": cuts, "scene_detection_status": scene_status, "transcription_words": len(words), "transcription_available": bool(words), "transcription_status": transcript.status, "transcription_device": transcript.device, "transcription_precision": transcript.precision, "transcription_message": transcript.message}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(report_clips)
