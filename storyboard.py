"""Compact chronological evidence sheets for selected and rejected moments."""
from __future__ import annotations

import json
from pathlib import Path

STORYBOARD_VERSION = "candidate-storyboard-v1"


def write_candidate_storyboard(source: Path, directory: Path, selection: dict, words: list,
                               cuts: list[float], max_candidates: int = 8,
                               max_frames: int = 5) -> Path | None:
    import cv2
    if max_candidates < 1 or not 1 <= max_frames <= 12:
        raise ValueError("Storyboard limits must be positive with at most twelve frames per candidate")
    candidates = selection.get("candidates", [])
    selected = [item for item in candidates if item.get("selected")]
    remaining = sorted((item for item in candidates if not item.get("selected")),
                       key=lambda item: item.get("score", 0), reverse=True)
    chosen = (selected + remaining)[:max_candidates]
    if not chosen:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return None
    records = []
    try:
        for number, candidate in enumerate(chosen, 1):
            start, end = float(candidate["start"]), float(candidate["end"])
            times = [start + .05, (start + end) / 2, max(start, end - .05)]
            times.extend(cut for cut in cuts if start < cut < end)
            times = sorted(set(round(value, 3) for value in times))
            if len(times) > max_frames:
                times = [times[round(i * (len(times) - 1) / (max_frames - 1))] for i in range(max_frames)]
            frames = []
            candidate_dir = directory / f"candidate_{number:03}"
            candidate_dir.mkdir(exist_ok=True)
            for frame_number, timestamp in enumerate(times, 1):
                cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = cap.read()
                if not ok:
                    continue
                height, width = frame.shape[:2]
                resized = cv2.resize(frame, (min(320, width), max(1, round(height * min(320, width) / width))))
                image_path = candidate_dir / f"frame_{frame_number:02}.jpg"
                if cv2.imwrite(str(image_path), resized, [cv2.IMWRITE_JPEG_QUALITY, 82]):
                    frames.append({"timestamp": timestamp, "path": str(image_path.relative_to(directory))})
            excerpt = " ".join(word.text for word in words if word.end > start and word.start < end)
            rejections = selection.get("rejections", [])
            rejection = next((row.get("reason") for row in rejections
                              if isinstance(row.get("start"), (int, float)) and isinstance(row.get("end"), (int, float))
                              and abs(row["start"] - start) < .1 and abs(row["end"] - end) < .1), None)
            records.append({"candidate_id": f"candidate_{number:03}", "start": start, "end": end,
                            "source_timestamps": [start, end], "frames": frames, "scene_cuts": [cut for cut in cuts if start < cut < end],
                            "transcript_excerpt": excerpt, "selected": bool(candidate.get("selected")),
                            "selection_reason": candidate.get("reason") if candidate.get("selected") else rejection,
                            "score": candidate.get("score"), "category": candidate.get("editorial_category", candidate.get("semantic_category")),
                            "editorial": candidate.get("editorial"), "visual_evidence": {
                                "status": "inference_result_recorded" if candidate.get("semantic_score") is not None else "not_assessed",
                                "sampled_timestamps": candidate.get("sampled_frame_timestamps", []),
                                "evidence": candidate.get("semantic_evidence", [])},
                            "uncertainty": candidate.get("editorial", {}).get("confidence")})
    finally:
        cap.release()
    index = {"version": STORYBOARD_VERSION, "source": source.name, "max_candidates": max_candidates,
             "max_frames_per_candidate": max_frames, "candidates": records}
    output = directory / "index.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output)
    return output
