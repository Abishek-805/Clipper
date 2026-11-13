"""Build an offline episode memory and human-review board from cached analysis."""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
import unicodedata
from collections import Counter
from pathlib import Path

from diagnostics import ffmpeg_path
from evaluation.anime_review import SCHEMA, save_dataset, source_fingerprint, validate_dataset
from evaluation.build_event_review import _preserve_existing_annotations
from evaluation.build_review_board import preview_command, validate_preview
from evaluation.episode_brain import build_episode_brain


def _timestamp(value: str) -> float:
    hours, minutes, seconds = value.strip().split(":")
    return int(hours)*3600 + int(minutes)*60 + float(seconds.replace(",", "."))


def _parse_srt(text: str) -> list[dict]:
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.splitlines()
        timing = next((line for line in lines if "-->" in line), None)
        if not timing:
            continue
        left, right = [part.strip().split()[0] for part in timing.split("-->")]
        body = " ".join(lines[lines.index(timing)+1:])
        body = re.sub(r"\{[^}]*\}|<[^>]*>|\\[Nn]", " ", body)
        body = re.sub(r"\\[a-zA-Z]+(?:\([^)]*\))?", "", body).replace("\\h", " ")
        cues.append({"start": _timestamp(left), "end": _timestamp(right), "text": re.sub(r"\s+", " ", body).strip()})
    return cues


def extract_subtitle_tracks(source: Path, streams: list[dict], duration: float,
                            whisper_words: list[dict]) -> list[dict]:
    """Retain each embedded subtitle cue as evidence and measure basic reliability signals."""
    output = []
    whisper_tokens = {token.casefold() for word in whisper_words
                      for token in re.findall(r"[\w']+", str(word.get("text", "")), re.UNICODE)}
    for stream in streams:
        if stream.get("type") != "subtitle":
            continue
        result = subprocess.run([ffmpeg_path(), "-v", "error", "-i", str(source), "-map", f"0:{stream['index']}",
                                 "-c:s", "srt", "-f", "srt", "-"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=True)
        cues = _parse_srt(result.stdout)
        valid = [c for c in cues if 0 <= c["start"] < c["end"] <= duration + .5 and c["text"]]
        normalized = [re.sub(r"\W+", "", c["text"].casefold(), flags=re.UNICODE) for c in valid]
        counts = Counter(normalized)
        duplicate_count = sum(count-1 for count in counts.values() if count > 1)
        intervals, cursor, covered = sorted((c["start"], c["end"]) for c in valid), 0.0, 0.0
        for start, end in intervals:
            covered += max(0.0, end-max(cursor, start))
            cursor = max(cursor, end)
        chars = "".join(c["text"] for c in valid)
        letters = [c for c in chars if c.isalpha()]
        latin = sum(unicodedata.name(c, "").startswith("LATIN") for c in letters)
        kana = sum("HIRAGANA" in unicodedata.name(c, "") or "KATAKANA" in unicodedata.name(c, "") for c in letters)
        tokens = {token.casefold() for c in valid for token in re.findall(r"[\w']+", c["text"], re.UNICODE)}
        overlap = len(tokens & whisper_tokens)/len(tokens | whisper_tokens) if tokens | whisper_tokens else None
        reasons = []
        duplicate_ratio = duplicate_count/max(1, len(valid))
        coverage_ratio = covered/max(.001, duration)
        if duplicate_ratio > .15:
            reasons.append("many repeated cue texts")
        if coverage_ratio < .35:
            reasons.append("sparse time coverage")
        if letters and stream.get("language") == "jpn" and latin/len(letters) > .8 and kana/len(letters) < .05:
            reasons.append("declared Japanese language conflicts with predominantly Latin-script text")
        if letters and stream.get("language") == "eng" and kana/len(letters) > .25:
            reasons.append("declared English language conflicts with substantial Japanese script")
        output.append({"stream_index": stream["index"], "codec": stream.get("codec"),
            "declared_language": stream.get("language"), "title": stream.get("title"),
            "default": bool(stream.get("default")), "cue_count": len(cues), "usable_cue_count": len(valid),
            "invalid_or_empty_cue_count": len(cues)-len(valid), "duplicate_text_cue_count": duplicate_count,
            "duplicate_text_ratio": round(duplicate_ratio, 4), "coverage_seconds": round(covered, 3),
            "coverage_ratio": round(coverage_ratio, 4), "mean_cue_characters": round(sum(len(c["text"]) for c in valid)/max(1,len(valid)), 2),
            "latin_letter_ratio": round(latin/max(1,len(letters)), 4), "kana_letter_ratio": round(kana/max(1,len(letters)), 4),
            "whisper_token_set_jaccard": round(overlap, 4) if overlap is not None else None,
            "reasons_for_review": reasons, "use_as_primary_transcript": False,
            "assessment": "linguistic evidence only; cue timing/coverage are not word alignment",
            "cues": cues})
    return output


def _peak_working_set_mb() -> float | None:
    try:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return round(counters.PeakWorkingSetSize/1024/1024, 2)
    except (AttributeError, OSError):
        pass
    return None


def sample_episode_frames(source: Path, output: Path, duration: float, scene_cuts: list[float]) -> dict:
    """One sequential decode pass; keeps 3-second samples plus scene-transition evidence frames."""
    import cv2
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "_coarse_frame_manifest.json"
    stat = source.stat()
    fingerprint = source_fingerprint(str(source), stat.st_size, stat.st_mtime_ns)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_fingerprint") == fingerprint and all((output / row["path"]).is_file() for row in manifest.get("frames", [])):
            return {"frames": manifest["frames"], "elapsed_seconds": 0.0,
                    "original_sample_seconds": manifest.get("elapsed_seconds"),
                    "decoded_frames": manifest.get("decoded_frames", 0),
                    "cache_hit": True, "frame_pass_peak_working_set_mb": manifest.get("peak_working_set_mb"),
                    "peak_working_set_mb": _peak_working_set_mb()}
    targets = sorted(set([round(t, 3) for t in (i*3.0 for i in range(math.ceil(duration/3)))] +
                         [round(float(t), 3) for t in scene_cuts if 0 <= float(t) < duration]))
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not decode source for coarse frame sampling: {source}")
    start_time, frame_index, target_index, frames = time.perf_counter(), 0, 0, []
    try:
        while target_index < len(targets):
            ok, frame = capture.read()
            if not ok:
                break
            timestamp = capture.get(cv2.CAP_PROP_POS_MSEC)/1000
            frame_index += 1
            while target_index < len(targets) and timestamp >= targets[target_index] - .02:
                height, width = frame.shape[:2]
                scaled = cv2.resize(frame, (min(320, width), max(1, round(height*min(320,width)/width))))
                path = output / f"frame_{target_index+1:05}.jpg"
                if not cv2.imwrite(str(path), scaled, [cv2.IMWRITE_JPEG_QUALITY, 74]):
                    raise OSError(f"Could not write coarse evidence frame: {path}")
                frames.append({"timestamp": round(timestamp, 3), "requested_timestamp": targets[target_index],
                               "path": path.name})
                target_index += 1
    finally:
        capture.release()
    elapsed = round(time.perf_counter()-start_time, 3)
    peak_working_set = _peak_working_set_mb()
    manifest = {"source_fingerprint": fingerprint, "sample_interval_seconds": 3,
                "requested_samples": len(targets), "decoded_frames": frame_index,
                "frames": frames, "elapsed_seconds": elapsed,
                "peak_working_set_mb": peak_working_set}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"frames": frames, "elapsed_seconds": elapsed, "cache_hit": False,
            "original_sample_seconds": elapsed, "decoded_frames": frame_index,
            "frame_pass_peak_working_set_mb": peak_working_set, "peak_working_set_mb": peak_working_set}


def _nearest_frames(frames: list[dict], start: float, end: float) -> tuple[list[str], list[float]]:
    requested = [start, (start+end)/2, end]
    chosen = []
    for timestamp in requested:
        if not frames:
            break
        row = min(frames, key=lambda f: abs(f["timestamp"]-timestamp))
        if row not in chosen:
            chosen.append(row)
    return [r["path"] for r in chosen], [r["timestamp"] for r in chosen]


def _candidate_from_moment(moment: dict, frames: list[dict]) -> dict:
    frame_paths, frame_times = _nearest_frames(frames, moment["start"], moment["end"])
    return {"candidate_id": moment["event_id"], "start": moment["start"], "end": moment["end"],
        "duration": moment["duration"], "predicted_category": "Unclassified",
        "score": float(moment.get("signal_evidence_score", moment.get("signal_score", 0))),
        "signal_evidence_rank": moment.get("signal_evidence_rank"), "global_editorial_rank": None,
        "score_label": "signal evidence priority only · not editorial rank", "selected": False,
        "baseline": False, "reviewer_label": None, "boundary_valid": True,
        "sources": [moment["provenance"] if "provenance" in moment else "cached_motion_signal"],
        "reason": moment["event_type"], "transcript_excerpt": moment.get("exact_quote", moment.get("transcript_excerpt", "")),
        "evidence": moment.get("signal_evidence", moment.get("signal_evidence_score")),
        "event_summary": json.dumps({"category": moment.get("category"), "event_type": moment["event_type"],
            "signal_evidence_rank": moment.get("signal_evidence_rank"), "global_editorial_rank": None,
            "context_before": moment.get("context_before"), "context_after": moment.get("context_after"),
            "subtitle_evidence": moment.get("subtitle_evidence", []),
            "boundary": moment["boundary_recommendation"],
            "editorial_assessment": moment["editorial_assessment"]}, ensure_ascii=False, indent=2),
        "review_cluster_id": moment["event_id"], "review_cluster_size": 1, "alternate_candidates": [],
        "frames": frame_paths, "frame_timestamps": frame_times, "preview_audio_available": False,
        "event": moment}


def write_episode_brain(analysis_path: Path, output: Path) -> dict:
    started = time.perf_counter()
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    transcript_path, cache_path = analysis_path.with_name("transcription.json"), analysis_path.with_name("analysis_cache.json")
    if not transcript_path.is_file() or not cache_path.is_file():
        raise FileNotFoundError("Task 06F requires the existing cached transcript and signal analysis")
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    source = Path(analysis.get("source_path") or "")
    if not source.is_file():
        raise FileNotFoundError(f"Episode source is unavailable: {source}")
    output.mkdir(parents=True, exist_ok=True)
    board_output = output
    duration = float(analysis.get("source_duration") or analysis.get("metadata", {}).get("duration", 0))
    streams = analysis.get("metadata", {}).get("streams", [])
    subtitle_started = time.perf_counter()
    subtitles = extract_subtitle_tracks(source, streams, duration, transcript.get("words", []))
    subtitle_elapsed = round(time.perf_counter()-subtitle_started, 3)
    scene_cuts = analysis.get("scene_cuts", cache.get("scene_cuts", []))
    frame_result = sample_episode_frames(source, output / "frames", duration, scene_cuts)
    frame_rows = frame_result["frames"]
    memory = build_episode_brain(analysis, transcript, cache, subtitles,
                                 [row["timestamp"] for row in frame_rows])
    source_stat = source.stat()
    memory["source"] = {"filename": source.name, "path": str(source), "size": source_stat.st_size,
                        "mtime_ns": source_stat.st_mtime_ns,
                        "fingerprint": source_fingerprint(str(source), source_stat.st_size, source_stat.st_mtime_ns)}
    memory["timeline"]["coarse_frame_evidence"] = frame_rows
    memory["performance"] = {"cache_load_seconds": round(time.perf_counter()-started-subtitle_elapsed-frame_result["elapsed_seconds"],3),
        "subtitle_extract_seconds": subtitle_elapsed, "coarse_frame_sample_seconds": frame_result.get("original_sample_seconds"),
        "coarse_frame_current_run_seconds": frame_result["elapsed_seconds"],
        "coarse_frame_cache_hit": frame_result["cache_hit"], "coarse_frame_count": len(frame_rows),
        "decoded_source_frames": frame_result.get("decoded_frames", 0),
        "frame_pass_peak_working_set_mb": frame_result.get("frame_pass_peak_working_set_mb"),
        "current_build_peak_working_set_mb": frame_result.get("peak_working_set_mb"),
        "semantic_model_initialization_seconds": None, "semantic_inference_seconds": None,
        "peak_vram_mb": None, "gpu_inference": "NOT_RUN_NO_LOCAL_LLAMA_CPP_OR_MODEL"}
    output.mkdir(parents=True, exist_ok=True)
    outputs = {"episode_brain.json": memory,
        "episode_timeline.json": memory["timeline"], "scene_memory.json": memory["scene_memory"],
        "entity_memory.json": memory["entity_memory"], "plot_memory.json": memory["plot_memory"],
        "important_phrases.json": memory["important_phrase_index"],
        "visual_event_timeline.json": memory["visual_event_timeline"],
        "action_fight_event_index.json": {"status": "NOT_SEMANTICALLY_CLASSIFIED_NO_VLM",
          "items": memory["visual_event_timeline"],
          "note": "Motion candidates remain unknown; none are asserted to be fights or action."},
        "moment_discovery.json": memory["moments"], "global_ranked_moments.json": memory["ranking"],
        "old_vs_new_comparison.json": memory["baseline_comparison"],
        "natural_boundaries.json": [dict(event_id=m["event_id"], **m["boundary_recommendation"]) for m in memory["moments"]],
        "subtitle_quality_report.json": subtitles, "performance.json": memory["performance"]}
    for filename, content in outputs.items():
        (output / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

    analysis_clips = analysis.get("clips", [])
    candidates = []
    for i, clip in enumerate(analysis_clips, 1):
        frame_paths, frame_times = _nearest_frames(frame_rows, clip["start"], clip["end"])
        candidates.append({"candidate_id": f"baseline_{i:03}", "start": clip["start"], "end": clip["end"],
            "duration": clip["end"]-clip["start"], "predicted_category": "Unclassified", "score": clip.get("score", 0),
            "score_label": "old production signal score · not editorial", "selected": True, "baseline": True,
            "reviewer_label": None, "boundary_valid": clip.get("boundary_valid", True),
            "sources": clip.get("sources", []), "reason": clip.get("reason", "Existing production baseline"),
            "transcript_excerpt": " ".join(str(w.get("text", "")).strip() for w in transcript.get("words", [])
                                             if clip["start"] <= float(w["start"]) < clip["end"]),
            "evidence": {"old_feature_scores": clip.get("feature_scores", {})},
            "event_summary": "Existing production clip shown for comparison. Editorial status: not assessed.",
            "review_cluster_id": f"baseline_{i:03}", "review_cluster_size": 1, "alternate_candidates": [],
            "frames": frame_paths, "frame_timestamps": frame_times, "preview_audio_available": False})
    candidates.extend(_candidate_from_moment(moment, frame_rows) for moment in memory["moments"])
    dataset = {"schema": SCHEMA, "schema_version": 1,
        "annotation_policy": "Human decisions only. Predictions and activity evidence are not human labels.",
        "source": {"filename": source.name, "path": str(source), "duration": duration,
          "fingerprint": memory["source"]["fingerprint"], "size": source_stat.st_size, "mtime_ns": source_stat.st_mtime_ns,
          "selected_audio_stream_index": analysis.get("selected_audio_stream_index"),
          "audio_language": next((s.get("language") for s in streams if s.get("index") == analysis.get("selected_audio_stream_index")), None),
          "preview_audio_available": False},
        "episode_overview": memory["episode_overview"],
        "timeline_scenes": [{"scene_id": s["scene_id"], "start": s["start"], "end": s["end"]} for s in memory["scene_memory"]],
        "ranking_status": memory["ranking"],
        "review_sampling": {"raw_candidate_count": len(candidates), "valid_cluster_count": len(candidates),
          "valid_candidate_count": len(candidates), "invalid_proposals_excluded": 0,
          "duplicate_proposals_removed": 0, "displayed_cluster_count": len(candidates)},
        "candidate_count": len(candidates), "candidates": candidates, "annotations": []}
    _preserve_existing_annotations(board_output, dataset)
    validate_dataset(dataset)
    # Candidate previews are short navigation aids; the cards retain the full natural event interval.
    audio_index = dataset["source"]["selected_audio_stream_index"]
    for item in dataset["candidates"]:
        folder = board_output / "candidates" / item["candidate_id"]
        folder.mkdir(parents=True, exist_ok=True)
        for relative in item.get("frames", []):
            target = folder / Path(relative).name
            if not target.exists():
                import shutil
                shutil.copy2(output / "frames" / Path(relative).name, target)
        if item["end"]-item["start"] <= 0:
            continue
        preview_start = max(item["start"], (item["start"]+item["end"]-8)/2)
        preview_start = min(preview_start, item["end"]-.1)
        preview = folder / "preview.mp4"
        command = preview_command(source, preview, preview_start, min(8.0, item["end"]-preview_start), audio_index,
                                  dataset["source"]["audio_language"])
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            preview_info = validate_preview(preview, audio_index, dataset["source"]["audio_language"])
        except (subprocess.CalledProcessError, ValueError):
            command = preview_command(source, preview, preview_start, min(8.0, item["end"]-preview_start), None, None)
            subprocess.run(command, check=True, capture_output=True, text=True)
            preview_info = validate_preview(preview, None, None)
        item["preview"] = preview.relative_to(board_output).as_posix()
        item["preview_audio_available"] = preview_info["audio_available"]
        item["preview_note"] = "8-second excerpt for navigation; candidate interval above is the complete proposed moment."
        item["frames"] = [f"candidates/{item['candidate_id']}/{Path(relative).name}" for relative in item.get("frames", [])]
    validate_dataset(dataset)
    save_dataset(board_output / "anime_highlight_dataset.json", dataset)
    html = Path(__file__).with_name("review_board.html").read_text(encoding="utf-8")
    html = html.replace("Clipper candidate review", "Clipper Episode Brain · whole-episode review")
    html = html.replace("Clipper · anime candidate review", "Clipper · Episode Brain review")
    overview = "<section style='max-width:1200px;margin:20px auto;padding:16px;background:#1f2937;border-radius:12px'>" \
      "<h2>Episode map</h2><p>Complete cached episode timeline. Segment widths represent source time; green marks are discovered moments.</p>" \
      "<div id='episode-timeline' style='position:relative;height:28px;background:#030712;margin:16px 0'></div>" \
      "<p id='signal-leaders'></p>" \
      "<p>Editorial ranking: not run. Order is signal evidence only. Human review is required; no number of clips is forced.</p></section>"
    html = html.replace('<main id="cards">', overview+'<main id="cards">')
    timeline_js = "const lane=document.querySelector('#episode-timeline');for(const s of dataset.timeline_scenes){const x=document.createElement('i');x.title=`${s.scene_id} ${s.start.toFixed(1)}–${s.end.toFixed(1)}s`;x.style=`position:absolute;left:${100*s.start/dataset.source.duration}%;width:${Math.max(.04,100*(s.end-s.start)/dataset.source.duration)}%;height:100%;background:#374151`;lane.append(x)}for(const c of dataset.candidates){if(c.baseline)continue;const x=document.createElement('i');x.title=`${c.candidate_id} ${c.start.toFixed(1)}–${c.end.toFixed(1)}s`;x.style=`position:absolute;left:${100*c.start/dataset.source.duration}%;width:${Math.max(.1,100*(c.end-c.start)/dataset.source.duration)}%;height:100%;background:#34d399;opacity:.85`;lane.append(x)}const leaders=dataset.ranking_status.moments.slice(0,2).map(id=>dataset.candidates.find(c=>c.candidate_id===id)).filter(Boolean);document.querySelector('#signal-leaders').textContent='Highest signal-evidence review priority: '+(leaders.length?leaders.map((c,i)=>`${i+1}. ${c.candidate_id} (${c.start.toFixed(1)}–${c.end.toFixed(1)}s; ${Number(c.score).toFixed(3)})`).join(' · '):'none')+' — signal only, not an editorial recommendation.'"
    html = html.replace("</script></html>", timeline_js+"\n</script></html>")
    embedded = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    (board_output / "index.html").write_text(html.replace("__DATA__", embedded), encoding="utf-8")
    memory["performance"]["total_build_seconds"] = round(time.perf_counter()-started, 3)
    (output / "performance.json").write_text(json.dumps(memory["performance"], indent=2), encoding="utf-8")
    (output / "episode_brain.json").write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": output, "board": board_output / "index.html", "candidates": len(candidates),
            "moments": len(memory["moments"]), "scenes": len(memory["scene_memory"]),
            "phrases": len(memory["important_phrase_index"]), "frames": len(frame_rows),
            "annotations": len(dataset["annotations"]), "performance": memory["performance"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.analysis_json.parent / "candidate_review_06f"
    print(json.dumps({k: str(v) if isinstance(v, Path) else v
                      for k, v in write_episode_brain(args.analysis_json, output).items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
