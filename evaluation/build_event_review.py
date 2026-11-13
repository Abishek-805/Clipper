"""Build a local review board comparing event-first proposals with existing selected clips."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from evaluation.anime_review import SCHEMA, load_dataset, save_dataset, source_fingerprint, validate_dataset
from evaluation.build_review_board import preview_command, validate_preview
from evaluation.event_engine import assess_candidate_interval, build_event_proposals


def _overlap_ratio(a: dict, b: dict) -> float:
    overlap = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    shorter = min(a["end"] - a["start"], b["end"] - b["start"])
    return overlap / shorter if shorter > 0 else 0.0


def _select_events(events: list[dict], duration: float, count: int, legacy: list[dict]) -> tuple[list[dict], int]:
    regions = [[] for _ in range(6)]
    for event in events:
        middle = (event["start"] + event["end"]) / 2
        regions[min(5, int(middle / duration * 6))].append(event)
    for region in regions:
        region.sort(key=lambda e: (-e["signal_score"], e["start"], e["event_id"]))
    selected, skipped, skipped_ids = [], 0, set()
    legacy_intervals = [{"start": c["start"], "end": c["end"]} for c in legacy]
    for region_index in range(6):
        # A dialogue proposal and an activity-only proposal can both be useful review controls.
        region = regions[region_index]
        for kind in ("dialogue", "activity"):
            for event in region:
                is_activity = event["structure"] == "unclassified_visual_activity_span"
                if is_activity != (kind == "activity") or event in selected:
                    continue
                if any(_overlap_ratio(event, old) >= .8 for old in legacy_intervals):
                    if event["event_id"] not in skipped_ids:
                        skipped += 1
                        skipped_ids.add(event["event_id"])
                    continue
                selected.append(event)
                break
    pool = sorted((e for region in regions for e in region if e not in selected and e["event_id"] not in skipped_ids),
                  key=lambda e: (-e["signal_score"], e["start"], e["event_id"]))
    for event in pool:
        if len(selected) >= count:
            break
        if any(_overlap_ratio(event, old) >= .8 for old in legacy_intervals + selected):
            if event["event_id"] not in skipped_ids:
                skipped += 1
                skipped_ids.add(event["event_id"])
            continue
        selected.append(event)
    return sorted(selected, key=lambda e: (e["start"], e["end"])), skipped


def _summary(event: dict, baseline: bool = False) -> str:
    if baseline:
        speech = event["speech_evidence"]
        return ("Existing production selection · activity/transcript evidence only\n"
                f"Hook/frame context: not assessed · transcript words: {speech['word_count']}\n"
                f"Speech coverage: {speech['speech_coverage']:.1%} · largest speech gap: "
                f"{speech['largest_uncovered_gap_seconds']:.2f}s\n"
                f"Warnings: {', '.join(event['warnings']) or 'none from available signals'}\n"
                "Editorial score: not assessed; no semantic provider ran.")
    evidence = event["signal_evidence"]
    return (f"Event-first proposal · observed structure: {event['structure']}\n"
            f"Provisional transcript-only logline: {event['context_evidence'] or 'no dialogue evidence'}\n"
            f"Hook evidence: {event['hook_evidence'] or 'not available'}\n"
            f"Context evidence: {event['context_evidence'] or 'not available'}\n"
            f"Escalation evidence: {event['escalation_evidence'] or 'no explicit cue detected'}\n"
            f"Payoff/reaction evidence: {event['payoff_evidence'] or 'no explicit cue detected'}\n"
            f"Evidence: {evidence['word_count']} words · {evidence['speech_coverage']:.1%} speech coverage · "
            f"largest gap {evidence['largest_uncovered_gap_seconds']:.2f}s · motion mean {evidence.get('motion_mean')} · "
            f"audio mean {evidence.get('audio_energy_mean')} · {evidence.get('scene_changes')} scene cuts\n"
            f"Warnings: {', '.join(event['negative_flags']) or 'none from available signals'}\n"
            "Category: unknown · semantic interpretation: unavailable · editorial quality: not assessed.")


def build_comparison(analysis: dict, words: list[dict], cache: dict, events: list[dict]) -> dict:
    duration = float(analysis.get("source_duration") or analysis.get("metadata", {}).get("duration", 0))
    rows = []
    for number, clip in enumerate(analysis.get("clips", []), 1):
        assessment = assess_candidate_interval(clip["start"], clip["end"], duration, words, cache)
        match = max(events, key=lambda e: _overlap_ratio(e, clip), default=None)
        matching_iou = 0.0
        if match:
            intersection = max(0.0, min(match["end"], clip["end"]) - max(match["start"], clip["start"]))
            union = max(match["end"], clip["end"]) - min(match["start"], clip["start"])
            matching_iou = intersection / union if union else 0.0
        rows.append({"baseline_id": f"baseline_{number:03}", "source_interval": [clip["start"], clip["end"]],
                     "duration": clip.get("duration"), "old_category": clip.get("category"),
                     "old_signal_score": clip.get("score"), "old_reason": clip.get("reason"),
                     "old_feature_scores": clip.get("feature_scores", {}),
                     "evidence_audit": assessment,
                     "nearest_event_id": match["event_id"] if match else None,
                     "nearest_event_iou": round(matching_iou, 4),
                     "interpretation": "Temporal match is not an editorial acceptance; human review is still required."})
    return {"schema": "clipper-event-first-comparison-v1", "source": analysis.get("source"),
            "production_algorithm_changed": False, "baseline_clip_count": len(rows),
            "event_proposal_count": len(events), "baseline": rows}


def _candidate_row(event: dict, words: list[dict], baseline: bool = False) -> dict:
    if baseline:
        source_clip, assessment = event["clip"], event["assessment"]
        start, end = float(source_clip["start"]), float(source_clip["end"])
        transcript = " ".join(str(w.get("text", w.get("word", ""))).strip() for w in words
                              if start <= float(w.get("start", -1)) < end).strip()
        return {"candidate_id": event["baseline_id"], "source_candidate_id": source_clip.get("candidate_id"),
                "start": start, "end": end, "duration": end-start, "predicted_category": "Unclassified",
                "score": float(source_clip.get("score", 0)), "score_label": "old activity-signal score (not editorial)",
                "selected": True, "baseline": True, "reviewer_label": None, "boundary_valid": bool(source_clip.get("boundary_valid", True)),
                "sources": source_clip.get("sources", []), "reason": source_clip.get("reason", "Existing production clip"),
                "transcript_excerpt": transcript, "evidence": {"old_feature_scores": source_clip.get("feature_scores", {}),
                "speech_coverage": assessment["speech_evidence"]}, "event_summary": _summary(assessment, True),
                "review_cluster_id": event["baseline_id"], "review_cluster_size": 1, "alternate_candidates": [],
                "event_assessment": assessment}
    return {"candidate_id": event["event_id"], "source_candidate_id": event["event_id"],
            "start": event["start"], "end": event["end"], "duration": event["duration"],
            "predicted_category": "Unclassified", "score": event["signal_score"],
            "score_label": "event signal-evidence score (not editorial)", "selected": False, "baseline": False,
            "reviewer_label": None, "boundary_valid": True, "sources": ["event_first", event["structure"]],
            "reason": event["structure"], "transcript_excerpt": event["transcript_excerpt"],
            "evidence": event["signal_evidence"], "event_summary": _summary(event),
            "review_cluster_id": event["event_id"], "review_cluster_size": 1, "alternate_candidates": [],
            "event": event}


def _preserve_existing_annotations(output: Path, dataset: dict) -> None:
    path = output / "anime_highlight_dataset.json"
    if not path.is_file():
        return
    existing = load_dataset(path)
    if existing["source"]["fingerprint"] != dataset["source"]["fingerprint"]:
        raise ValueError(f"Refusing to overwrite annotations for a different source: {path}")
    candidates = {row["candidate_id"]: [row["start"], row["end"]] for row in dataset["candidates"]}
    if any(row["candidate_id"] not in candidates or
           row["source_interval"] != candidates[row["candidate_id"]] for row in existing["annotations"]):
        raise ValueError(f"Refusing to drop annotations whose candidates changed: {path}")
    dataset["annotations"] = existing["annotations"]


def write_event_review_board(analysis_path: Path, output: Path, review_limit: int = 36) -> dict:
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    transcript_path = analysis_path.with_name("transcription.json")
    cache_path = analysis_path.with_name("analysis_cache.json")
    if not transcript_path.is_file() or not cache_path.is_file():
        raise FileNotFoundError("Task 06E requires the existing cached transcription and analysis cache")
    transcript, cache = json.loads(transcript_path.read_text(encoding="utf-8")), json.loads(cache_path.read_text(encoding="utf-8"))
    words = transcript.get("words", [])
    source = Path(analysis.get("source_path") or "")
    if not source.is_file():
        raise FileNotFoundError(f"Episode source is unavailable: {source}")
    event_data = build_event_proposals(analysis, words, cache)
    comparison = build_comparison(analysis, words, cache, event_data["events"])
    duration = event_data["source_duration"]
    legacy = []
    for number, clip in enumerate(analysis.get("clips", []), 1):
        legacy.append({"baseline_id": f"baseline_{number:03}", "clip": clip,
                       "assessment": assess_candidate_interval(clip["start"], clip["end"], duration, words, cache)})
    selected, duplicates = _select_events(event_data["events"], duration, max(0, review_limit-len(legacy)), analysis.get("clips", []))
    candidates = [_candidate_row(item, words, True) for item in legacy]
    candidates.extend(_candidate_row(event, words) for event in selected)
    streams = analysis.get("metadata", {}).get("streams", [])
    audio_index = analysis.get("selected_audio_stream_index")
    audio = next((s for s in streams if s.get("type") == "audio" and s.get("index") == audio_index), None)
    stat = source.stat()
    dataset = {"schema": SCHEMA, "schema_version": 1,
               "annotation_policy": "Human decisions only. Automated event structure, evidence, and warnings are predictions; no annotations are prefilled.",
               "source": {"filename": source.name, "path": str(source), "duration": duration,
                          "fingerprint": source_fingerprint(str(source), stat.st_size, stat.st_mtime_ns),
                          "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                          "selected_audio_stream_index": audio_index if audio else None,
                          "audio_language": audio.get("language") if audio else None,
                          "preview_audio_available": audio is not None},
               "review_sampling": {"raw_candidate_count": len(legacy)+len(event_data["events"]),
                                   "valid_cluster_count": len(legacy)+len(event_data["events"])-duplicates,
                                   "valid_candidate_count": len(legacy)+len(event_data["events"])-duplicates,
                                   "invalid_proposals_excluded": 0, "duplicate_proposals_removed": duplicates,
                                   "displayed_cluster_count": len(candidates)},
               "candidate_count": len(candidates), "candidates": candidates, "annotations": []}
    output.mkdir(parents=True, exist_ok=True)
    _preserve_existing_annotations(output, dataset)
    validate_dataset(dataset)
    (output / "event_timeline.json").write_text(json.dumps({"schema": event_data["schema"],
        "source": event_data["source"], "source_duration": duration, "provenance": event_data["provenance"],
        "timeline": event_data["timeline"], "beats": event_data["beats"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "event_proposals.json").write_text(json.dumps({"schema": event_data["schema"],
        "event_count": event_data["event_count"], "editorial_assessment_count": 0,
        "events_ranked_by_signal_evidence": event_data["events"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "old_vs_event_comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required to extract low-resolution review frames") from exc
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not open the source episode")
    try:
        for item in candidates:
            folder = output / "candidates" / item["candidate_id"]
            folder.mkdir(parents=True, exist_ok=True)
            start, end = item["start"], item["end"]
            sample_times = sorted({min(max(start + .05, start), end - .03), (start+end)/2,
                                   min(max(start, end-.75), end-.03)})
            frame_files = []
            for frame_number, timestamp in enumerate(sample_times, 1):
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp*1000)
                ok, frame = capture.read()
                if ok:
                    height, width = frame.shape[:2]
                    frame = cv2.resize(frame, (min(480, width), max(1, round(height*min(480, width)/width))))
                    frame_path = folder / f"frame_{frame_number}.jpg"
                    if cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 78]):
                        frame_files.append(frame_path.relative_to(output).as_posix())
            item["frames"] = frame_files
            item["frame_timestamps"] = sample_times
            preview_start = min(max(start, (start+end-8)/2), end-.1)
            preview_duration = min(8.0, end-preview_start)
            preview = folder / "preview.mp4"
            command = preview_command(source, preview, preview_start, preview_duration,
                                      dataset["source"]["selected_audio_stream_index"], dataset["source"]["audio_language"])
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
                preview_info = validate_preview(preview, dataset["source"]["selected_audio_stream_index"],
                                                dataset["source"]["audio_language"])
            except (subprocess.CalledProcessError, ValueError):
                if dataset["source"]["selected_audio_stream_index"] is None:
                    raise
                command = preview_command(source, preview, preview_start, preview_duration, None, None)
                subprocess.run(command, check=True, capture_output=True, text=True)
                preview_info = validate_preview(preview, None, None)
            item.update({"preview": preview.relative_to(output).as_posix(),
                         "preview_duration": preview_info["duration"],
                         "preview_audio_available": preview_info["audio_available"],
                         "audio_stream_index": dataset["source"]["selected_audio_stream_index"] if preview_info["audio_available"] else None,
                         "audio_language": preview_info["audio_language"]})
    finally:
        capture.release()
    dataset["source"]["preview_audio_available"] = any(c["preview_audio_available"] for c in candidates)
    save_dataset(output / "anime_highlight_dataset.json", dataset)
    template = Path(__file__).with_name("review_board.html").read_text(encoding="utf-8")
    embedded = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    (output / "index.html").write_text(template.replace("__DATA__", embedded), encoding="utf-8")
    return {"board": output / "index.html", "dataset": output / "anime_highlight_dataset.json",
            "timeline": output / "event_timeline.json", "proposals": output / "event_proposals.json",
            "comparison": output / "old_vs_event_comparison.json", "event_count": len(event_data["events"]),
            "review_count": len(candidates), "baseline_count": len(legacy), "duplicates_skipped": duplicates}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--review-limit", type=int, default=36)
    args = parser.parse_args()
    output = args.output or args.analysis_json.parent / "candidate_review_06e"
    print(json.dumps({k: str(v) if isinstance(v, Path) else v
                      for k, v in write_event_review_board(args.analysis_json, output, args.review_limit).items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
