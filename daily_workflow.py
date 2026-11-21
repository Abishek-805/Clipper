"""Small local delivery and manual-interval operations shared by the browser."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
from pathlib import Path

import clipper
from model_manager import ModelManager


def whisper_transcript(source, info, cache_root, audio_index, caption_mode, allow_inference, cancel, progress=None):
    key = hashlib.sha256(str(source.resolve()).casefold().encode()).hexdigest()[:20]
    manager = ModelManager("small", local_files_only=True)
    try:
        return clipper.transcribe(source, info.has_audio, "small", cache_root / key / "transcription.json", cancel,
            model_manager=manager, audio_index=audio_index, allow_inference=allow_inference, progress=progress,
            task="translate" if caption_mode == "Whisper translation to English" else "transcribe")
    finally:
        manager.release_whisper()


def transcript_rows(words):
    rows, current = [], []
    for word in clipper._valid_words(words):
        if current and (word.start - current[-1].end > 1.15 or len(current) >= 30 or
                        current[-1].text.rstrip().endswith((".", "?", "!"))):
            rows.append(current)
            current = []
        current.append(word)
    if current:
        rows.append(current)
    return [{"start": group[0].start, "end": group[-1].end,
             "text": clipper.join_word_tokens([word.text for word in group])} for group in rows]


def interval(start, end, duration):
    start, end = float(start), float(end)
    if not all(math.isfinite(x) for x in (start, end)) or not 0 <= start < end <= duration:
        raise ValueError("Choose ordered times inside the source")
    if end - start > 180:
        raise ValueError("The maximum interval is 180 seconds")
    return start, end


def manual_clip(source: Path, folder: Path, cache_root: Path, audio_index, start, end,
                caption_mode: str, allow_transcription: bool, cancel: threading.Event,
                preset="fast", overlays=None, source_profile="general"):
    from source_profiles import validate_profile, FULL_FRAME
    validate_profile(source_profile)
    info = clipper.probe(source)
    start, end = interval(start, end, info.duration)
    words = []
    transcription = None
    release_structure = None
    if caption_mode == "Release subtitles":
        key = hashlib.sha256(str(source.resolve()).casefold().encode()).hexdigest()[:20]
        release_structure = clipper.prepare_structure(source, cache_root / key, source_profile=source_profile)
        selected = clipper.release_cues(release_structure)
        words = [clipper.Word(**c) for c in selected["cues"]]
    elif caption_mode != "None":
        transcription = whisper_transcript(source, info, cache_root, audio_index, caption_mode, allow_transcription, cancel)
        if transcription.status not in {"transcribed", "no_speech", "no_audio_track"}:
            raise ValueError("Matching captions are unavailable. Enable explicit transcription or choose None. " + transcription.message)
        words = transcription.words
    if cancel.is_set():
        raise InterruptedError("Cancelled")
    folder.mkdir(parents=True, exist_ok=False)
    if release_structure is not None:
        transcription = clipper.release_subtitle_transcription(release_structure, folder)
        clipper._write_manifest(folder / "structure.json", release_structure)
    stat = source.stat()
    record = {"filename": "clip_001.mp4", "start": start, "end": end, "duration": end-start,
              "selection": "MANUAL", "overlays": overlays or {}}
    manifest = {"source": source.name, "source_profile": source_profile, "source_path": str(source.resolve()), "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns, "settings": {"audio_stream_index": audio_index,
        "caption_mode": caption_mode, "encoder": "auto", "source_profile": source_profile}, "caption_source": caption_mode,
        "clips": [], "selection_status": "PROVISIONAL"}
    clipper._write_manifest(folder / "analysis.json", manifest)
    clipper._write_manifest(folder / "pending-edits.json", {"start": start, "end": end, "overlays": overlays})
    try:
        plan = clipper.build_reframe_plan(source, start, end, [], cancel, preserve_source=source_profile in FULL_FRAME)
    except InterruptedError:
        raise
    except Exception as exc:
        plan = {"status": "fallback", "shots": [], "reason": f"Smart framing unavailable: {exc}"}
    record["reframing"] = plan
    result = clipper.render(source, folder / record["filename"], info, record, words, info.has_audio, cancel,
        reframe_plan=plan,
        audio_index=audio_index, captions_required=bool(words), render_preset=preset, overlays=overlays,
        caption_cues=clipper.clipped_cues(selected["cues"], start, end) if release_structure is not None else None)
    record.update(render=result, validation="VALIDATED")
    manifest["clips"] = [record]
    if transcription:
        cached = transcription.message == "transcription cache hit"
        manifest.update(transcription_device=transcription.device, transcription_message=transcription.message,
                        transcription_compute_type=transcription.precision,
                        transcription_executed=transcription.device in {"cpu", "cuda"} and not cached,
                        transcription_fallback_reason=transcription.message if transcription.outcome == "CPU_FALLBACK" and not cached else None,
                        cache={"transcription": "reused" if cached else "not reused"})
    clipper._write_manifest(folder / "analysis.json", manifest)
    return manifest


def restore_version(folder: Path, filename: str, version: int):
    manifest_path = folder / "analysis.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clip = next((x for x in manifest.get("clips", []) if x.get("filename") == filename), None)
    target = clipper._manifest_clip_path(folder, filename)
    if not clip or target is None or not isinstance(version, int) or version < 1:
        raise ValueError("Invalid saved version")
    snapshot = target.with_name(f"{target.stem}.v{version}.json")
    if not snapshot.is_file():
        raise ValueError("This older version has no edit metadata; its media remains available in the job folder")
    prior = json.loads(snapshot.read_text(encoding="utf-8"))
    prior_clip = next(x for x in prior["clips"] if x["filename"] == filename)
    for suffix in (".mp4", ".srt"):
        if not target.with_name(f"{target.stem}.v{version}{suffix}").is_file():
            raise ValueError("Saved version is incomplete")
    versions = [int(match.group(1)) for path in folder.glob(f"{target.stem}.v*.mp4")
                if (match := re.fullmatch(re.escape(target.stem) + r"\.v(\d+)\.mp4", path.name))]
    next_version = max(versions, default=0) + 1
    for suffix in (".mp4", ".srt"):
        current = target.with_suffix(suffix)
        shutil.copy2(current, target.with_name(f"{target.stem}.v{next_version}{suffix}"))
    clipper._write_manifest(target.with_name(f"{target.stem}.v{next_version}.json"), manifest)
    try:
        for suffix in (".mp4", ".srt"):
            shutil.copy2(target.with_name(f"{target.stem}.v{version}{suffix}"), target.with_suffix(suffix))
        clip.clear()
        clip.update(prior_clip)
        clipper._write_manifest(manifest_path, manifest)
    except Exception:
        for suffix in (".mp4", ".srt"):
            shutil.copy2(target.with_name(f"{target.stem}.v{next_version}{suffix}"), target.with_suffix(suffix))
        raise
    return {"restored_version": version, "backup_version": next_version}


def byte_range(header: str | None, size: int):
    if not header:
        return 0, size-1, 200
    if not header.startswith("bytes=") or "," in header or size <= 0:
        raise ValueError("Unsupported byte range")
    left, right = header[6:].split("-", 1)
    if not left:
        length = int(right)
        if length <= 0:
            raise ValueError("Invalid suffix range")
        return max(0, size-length), size-1, 206
    start, end = int(left), min(int(right) if right else size-1, size-1)
    if start < 0 or start > end or start >= size:
        raise ValueError("Unsatisfiable range")
    return start, end, 206


def export_delivery(folder: Path, filename: str, output_root: Path):
    manifest = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    clip = next((x for x in manifest.get("clips", []) if x.get("filename") == filename), None)
    source = clipper._manifest_clip_path(folder, filename)
    if not clip or source is None or not source.is_file():
        raise ValueError("Choose an existing clip in the job")
    render = clip.get("render") or {}
    expected_audio = render["audio_codec"] is not None if "audio_codec" in render else clipper.probe(source).has_audio
    episode = re.sub(r'[^\w .-]', '_', Path(manifest.get("source", "episode")).stem)[:80].strip(' .') or "episode"
    destination = output_root / "Exports" / episode / folder.name
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / filename
    revision = 1
    suffixes = (".srt", ".json", ".mp4")  # Publish the playable file last.
    while True:
        staging = destination / f".{target.name}.exporting"
        try:
            staging.mkdir()
        except FileExistsError:
            pass
        else:
            if not any(os.path.lexists(target.with_suffix(ext)) for ext in suffixes):
                break
            staging.rmdir()
        target = destination / f"{source.stem}_export_{revision:03}.mp4"
        revision += 1
    staged = staging / target.name
    published = []
    try:
        for suffix in (".mp4", ".srt"):
            shutil.copy2(source.with_suffix(suffix), staged.with_suffix(suffix))
        clipper.validate_output(staged, float(clip["end"]) - float(clip["start"]), expected_audio,
                                staged.with_suffix(".srt"), captions_required=bool(render.get("subtitle_cues")))
        clipper._write_manifest(staged.with_suffix(".json"), {"source": manifest.get("source"), "job_id": folder.name,
            "clip": clip, "status": "Technically validated; rights not verified"})
        for suffix in suffixes:
            final = target.with_suffix(suffix)
            staged.with_suffix(suffix).rename(final)
            published.append(final)
    except Exception:
        for final in published:
            final.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging)
    return {"folder": str(destination.resolve()), "filename": target.name}
