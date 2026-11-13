"""Human review data and metrics for candidate highlights; never used by selection."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

SCHEMA = "clipper-anime-evaluation-v1"
CATEGORIES = {"Action", "Comedy", "Emotional", "Drama", "Suspense", "Memorable dialogue",
              "Reveal", "Cliffhanger", "Other", "Unclassified"}
RATINGS = {"hook", "context", "coherence", "impact", "payoff", "audio_clarity",
           "visual_clarity", "caption_accuracy", "overall"}
REJECTION_REASONS = {"Studio logo or title", "Opening sequence", "Ending credits", "Ordinary dialogue",
                     "Cut-off dialogue", "No context", "No payoff", "Low-interest scene",
                     "Duplicate candidate", "Caption error", "Unclear/uncertain"}


def source_fingerprint(source: str, size: int, mtime_ns: int) -> str:
    return hashlib.sha256(f"{Path(source).resolve()}\0{size}\0{mtime_ns}".encode("utf-8")).hexdigest()


def validate_dataset(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"Expected {SCHEMA}")
    if not isinstance(data.get("candidates"), list) or not isinstance(data.get("annotations"), list):
        raise ValueError("Dataset candidates and annotations must be arrays")
    if not isinstance(data.get("source"), dict) or not isinstance(data["source"].get("fingerprint"), str) or len(data["source"]["fingerprint"]) != 64:
        raise ValueError("Dataset needs a 64-character source fingerprint")
    if data.get("candidate_count") != len(data["candidates"]):
        raise ValueError("candidate_count does not match candidates")
    if any(not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str) for row in data["candidates"]):
        raise ValueError("Candidates need string IDs")
    ids = {row["candidate_id"] for row in data["candidates"]}
    if len(ids) != len(data["candidates"]) or None in ids:
        raise ValueError("Candidate IDs must be present and unique")
    for row in data["candidates"]:
        start, end = row.get("start"), row.get("end")
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or
                not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end) or end <= start):
            raise ValueError(f"Candidate {row.get('candidate_id')} has invalid bounds")
    seen = set()
    for row in data["annotations"]:
        if not isinstance(row, dict) or row.get("candidate_id") not in ids or row["candidate_id"] in seen:
            raise ValueError("Annotations must reference one unique candidate")
        seen.add(row["candidate_id"])
        candidate = next(candidate for candidate in data["candidates"] if candidate["candidate_id"] == row["candidate_id"])
        if row.get("source_fingerprint") != data["source"]["fingerprint"] or row.get("source_interval") != [candidate["start"], candidate["end"]]:
            raise ValueError("Annotation source fingerprint or timestamp interval does not match its candidate")
        if row.get("decision") not in {"keep", "reject", "maybe"}:
            raise ValueError("Annotation decision must be keep, reject, or maybe")
        if row.get("category") not in CATEGORIES:
            raise ValueError("Annotation category is invalid")
        if not isinstance(row.get("ratings", {}), dict):
            raise ValueError("Ratings must be an object")
        for key, value in row.get("ratings", {}).items():
            if key not in RATINGS or isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                raise ValueError(f"Invalid rating: {key}")
        reasons = row.get("rejection_reasons", [])
        if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons) or not set(reasons) <= REJECTION_REASONS:
            raise ValueError("Invalid rejection reason")
        for bound in ("corrected_start", "corrected_end"):
            value = row.get(bound)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"{bound} must be finite or null")
        if ((row.get("corrected_start") is None) != (row.get("corrected_end") is None) or
                row.get("corrected_start") is not None and row["corrected_end"] <= row["corrected_start"]):
            raise ValueError("Corrected interval must have both bounds and positive duration")
        if row.get("notes") is not None and not isinstance(row["notes"], str):
            raise ValueError("Notes must be text")
        if not isinstance(row.get("annotated_at"), str) or not row["annotated_at"]:
            raise ValueError("Each annotation needs an annotation timestamp")
    return data


def save_dataset(path: Path, data: dict) -> None:
    validate_dataset(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def load_dataset(path: Path) -> dict:
    return validate_dataset(json.loads(path.read_text(encoding="utf-8")))


def _signal(candidate: dict, key: str) -> float:
    return float(candidate.get("review_feature_scores", candidate.get("feature_scores", {})).get(key, 0) or 0)


def _duplicate_similarity(left: dict, right: dict) -> float:
    """Score temporal duplicates; nearby but distinct events stay separate."""
    ls, le = float(left["start"]), float(left["end"])
    rs, re = float(right["start"]), float(right["end"])
    overlap = max(0.0, min(le, re) - max(ls, rs))
    shorter = min(le - ls, re - rs)
    if shorter <= 0:
        return 0.0
    iou = overlap / (max(le, re) - min(ls, rs))
    coverage = overlap / shorter
    boundary_distance = min(abs(ls - rs), abs(le - re))
    # shortcut: same broad scene is insufficient; only high IoU or high containment plus a close boundary clusters, revisit after human review.
    duplicate = iou >= .60 or (coverage >= .80 and boundary_distance <= max(3.0, .10 * shorter))
    return min(1.0, iou + coverage / 100) if duplicate else 0.0


def _representative_key(candidate: dict) -> tuple:
    features = candidate.get("feature_scores", {})
    return (-bool(candidate.get("selected")), -bool(candidate.get("boundary_valid", True)),
            -float(features.get("dialogue_completeness", 0) or 0),
            -float(candidate.get("boundary_confidence", 0) or 0),
            -float(candidate.get("score", 0) or 0), float(candidate["start"]),
            str(candidate.get("candidate_id", "")))


def cluster_review_candidates(candidates: list[dict]) -> list[dict]:
    """Deterministic temporal NMS retaining each representative's proposal provenance."""
    ranked = sorted((c for c in candidates if c.get("boundary_valid", True) and
                     float(c.get("end", 0)) > float(c.get("start", 0))), key=_representative_key)
    clusters: list[list[dict]] = []
    representatives: list[dict] = []
    for candidate in ranked:
        similar = [(score, i) for i, rep in enumerate(representatives)
                   if (score := _duplicate_similarity(candidate, rep)) > 0]
        if similar:
            _, index = max(similar, key=lambda pair: (pair[0], -pair[1]))
            clusters[index].append(candidate)
        else:
            representatives.append(candidate)
            clusters.append([candidate])
    output = []
    # Stable cluster IDs follow the representative's timestamp, independent of input order.
    groups = sorted(zip(representatives, clusters), key=lambda pair: (float(pair[0]["start"]), str(pair[0].get("candidate_id", ""))))
    for number, (representative, members) in enumerate(groups, 1):
        item = dict(representative)
        members = sorted(members, key=lambda c: (float(c["start"]), float(c["end"]), str(c.get("candidate_id", ""))))
        item["review_cluster_id"] = f"cluster_{number:03}"
        item["review_cluster_size"] = len(members)
        item["review_start"] = min(float(m["start"]) for m in members)
        item["review_end"] = max(float(m["end"]) for m in members)
        item["review_cluster_members"] = [{"candidate_id": m.get("candidate_id"), "start": float(m["start"]),
            "end": float(m["end"]), "sources": m.get("sources", []), "score": float(m.get("score", 0) or 0),
            "selected": bool(m.get("selected")), "boundary_valid": bool(m.get("boundary_valid", True))}
            for m in members]
        item["review_sources"] = sorted({source for member in members for source in member.get("sources", [])})
        keys = {key for member in members for key in member.get("feature_scores", {})}
        item["review_feature_scores"] = {key: max(float(m.get("feature_scores", {}).get(key, 0) or 0)
                                                       for m in members) for key in keys}
        output.append(item)
    return output


def _sample_review_clusters(clusters: list[dict], duration: float, count: int) -> list[dict]:
    if count < 1 or duration <= 0:
        raise ValueError("count and duration must be positive")
    regions = [[] for _ in range(6)]
    for candidate in clusters:
        midpoint = (float(candidate["start"]) + float(candidate["end"])) / 2
        regions[min(5, int(midpoint / duration * 6))].append(candidate)
    chosen, seen = [], set()
    groups = (
        lambda c: bool(c.get("selected")),
        lambda c: "speech" in c.get("review_sources", c.get("sources", [])) or _signal(c, "speech_density") >= .5,
        lambda c: _signal(c, "average_motion") >= .45 or _signal(c, "peak_motion") >= .8,
        lambda c: _signal(c, "average_audio_energy") >= .7,
        lambda c: c.get("review_start", float(c["start"])) < 120 or c.get("review_end", float(c["end"])) > duration - 120,
        lambda c: True,
    )
    while len(chosen) < count:
        progressed = False
        for group in groups:
            for region in regions:
                pool = [c for c in region if c.get("candidate_id") not in seen and group(c)]
                if pool:
                    if group is groups[-1]:
                        pool.sort(key=lambda c: (float(c.get("score", 0)), float(c["start"])))
                    else:
                        pool.sort(key=lambda c: (-float(c.get("score", 0)), float(c["start"])))
                    chosen.append(pool[0])
                    seen.add(pool[0].get("candidate_id"))
                    progressed = True
                    if len(chosen) >= count:
                        break
            if len(chosen) >= count:
                break
        if not progressed:
            break
    return chosen


def review_candidates(candidates: list[dict], duration: float, count: int = 36) -> list[dict]:
    """Deduplicate, then sample deterministically across six episode regions."""
    return _sample_review_clusters(cluster_review_candidates(candidates), duration, count)


def build_dataset(manifest: dict, words: list[dict], count: int = 36) -> dict:
    source = manifest.get("source_path") or manifest.get("source")
    size, mtime = int(manifest.get("source_size", 0)), int(manifest.get("source_mtime_ns", 0))
    duration = float(manifest.get("source_duration") or manifest.get("metadata", {}).get("duration", 0))
    selection = manifest.get("candidate_selection", {})
    raw_candidates = selection.get("candidates", [])
    clusters = cluster_review_candidates(raw_candidates)
    valid_candidate_count = sum(1 for c in raw_candidates if c.get("boundary_valid", True) and float(c.get("end", 0)) > float(c.get("start", 0)))
    sampled = _sample_review_clusters(clusters, duration, count)
    streams = manifest.get("metadata", {}).get("streams", [])
    audio_index = manifest.get("selected_audio_stream_index")
    audio_stream = next((stream for stream in streams if stream.get("type") == "audio" and
                         stream.get("index") == audio_index), None) if isinstance(audio_index, int) and not isinstance(audio_index, bool) else None
    audio_index = audio_stream["index"] if audio_stream else None
    audio_language = audio_stream.get("language") if audio_stream else None
    rejected = selection.get("rejections", [])
    candidates = []
    for number, item in enumerate(sampled, 1):
        start, end = float(item["start"]), float(item["end"])
        match = next((r for r in rejected if abs(float(r.get("start", -9)) - start) < .1 and
                      abs(float(r.get("end", -9)) - end) < .1), None)
        features = item.get("review_feature_scores", item.get("feature_scores", {}))
        evidence = {key: features.get(key) for key in ("average_motion", "peak_motion", "average_audio_energy",
                    "speech_density", "dialogue_completeness", "scene_changes", "opening_hook_quality") if key in features}
        excerpt = " ".join(str(w.get("word", w.get("text", ""))).strip()
                           for w in words if float(w.get("end", 0)) > start and float(w.get("start", 0)) < end).strip()
        predicted = item.get("editorial_category") or item.get("semantic_category")
        category_map = {"ACTION_COMBAT": "Action", "COMEDY_REACTION": "Comedy", "EMOTIONAL": "Emotional",
                        "SUSPENSE_TENSION": "Suspense", "DIALOGUE_CONFRONTATION": "Drama",
                        "TRANSFORMATION_REVEAL": "Reveal"}
        category = category_map.get(predicted, predicted if predicted in CATEGORIES else "Unclassified")
        source_kinds = item.get("review_sources", item.get("sources", []))
        if item.get("selected"):
            reason = item.get("reason") or "Selected by existing Task 06C heuristic scoring"
        elif match:
            reason = match.get("reason", "Rejected by existing Task 06C heuristics")
        else:
            reason = "Candidate from existing Task 06C analysis; not among final selected clips"
        candidates.append({"candidate_id": f"review_{number:03}", "source_candidate_id": item.get("candidate_id"),
                           "start": start, "end": end, "duration": round(end-start, 3), "predicted_category": category,
                           "score": float(item.get("score", 0)), "selected": bool(item.get("selected")),
                           "boundary_valid": True,
                           "sources": source_kinds, "reason": reason, "transcript_excerpt": excerpt,
                           "evidence": evidence, "reviewer_label": None,
                           "review_cluster_id": item["review_cluster_id"],
                           "review_cluster_size": item["review_cluster_size"],
                           "alternate_candidates": [member for member in item["review_cluster_members"]
                                                     if member["candidate_id"] != item.get("candidate_id")],
                           "preview_audio_available": audio_index is not None,
                           "audio_stream_index": audio_index, "audio_language": audio_language})
    return {"schema": SCHEMA, "schema_version": 1,
            "annotation_policy": "Human judgement only. Candidate predictions are separate; no labels are prefilled.",
            "source": {"filename": Path(source).name if source else "", "path": source,
                       "duration": duration, "fingerprint": source_fingerprint(source or "", size, mtime),
                       "size": size, "mtime_ns": mtime, "selected_audio_stream_index": audio_index,
                       "audio_language": audio_language, "preview_audio_available": audio_index is not None},
            "review_sampling": {"raw_candidate_count": len(raw_candidates), "valid_cluster_count": len(clusters),
                                "valid_candidate_count": valid_candidate_count,
                                "invalid_proposals_excluded": len(raw_candidates) - valid_candidate_count,
                                "duplicate_proposals_removed": valid_candidate_count - len(clusters),
                                "displayed_cluster_count": len(candidates)},
            "candidate_count": len(candidates), "candidates": candidates, "annotations": []}


def interval_iou(a: dict, b: dict) -> float:
    intersection = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = max(a["end"], b["end"]) - min(a["start"], b["start"])
    return intersection / union if union > 0 else 0.0


def _acceptance(annotations: list[dict], key: str) -> float | None:
    values = [row["ratings"][key] for row in annotations if row.get("decision") == "keep" and key in row.get("ratings", {})]
    return sum(value >= 4 for value in values) / len(values) if values else None


def evaluate_dataset(data: dict, minimum_labels: int = 5) -> dict:
    validate_dataset(data)
    labels = data["annotations"]
    if len(labels) < minimum_labels:
        return {"status": "INSUFFICIENT_DATA", "human_label_count": len(labels),
                "required_label_count": minimum_labels, "metrics": None}
    by_id = {row["candidate_id"]: row for row in data["candidates"]}
    reviewed = [(by_id[row["candidate_id"]], row) for row in labels]
    top = sorted((pair for pair in reviewed if pair[0].get("score") is not None),
                 key=lambda pair: float(pair[0]["score"]), reverse=True)[:5]
    selected_reviewed = [(c, a) for c, a in reviewed if c.get("selected")]
    selected = [c for c in data["candidates"] if c.get("selected")]
    selected_overlaps = [interval_iou(a, b) for i, a in enumerate(selected) for b in selected[i+1:]]
    category = [(c, a) for c, a in reviewed if c.get("predicted_category") != "Unclassified" and a.get("category")]
    boundaries = [(c, a) for c, a in reviewed if a.get("corrected_start") is not None]
    duration = max(float(data["source"].get("duration", 0)), 1e-9)
    selected_regions = {min(5, int(((c["start"] + c["end"]) / 2) / duration * 6)) for c in selected}
    metrics = {
        "precision_at_k_reviewed_candidates": sum(a["decision"] == "keep" for _, a in top) / len(top) if top else None,
        "keep_rate_selected_candidates": sum(a["decision"] == "keep" for _, a in selected_reviewed) / len(selected_reviewed) if selected_reviewed else None,
        "category_agreement": sum(c["predicted_category"] == a["category"] for c, a in category) / len(category) if category else None,
        "temporal_iou_mean": sum(interval_iou(c, {"start": a["corrected_start"], "end": a["corrected_end"]}) for c, a in boundaries) / len(boundaries) if boundaries else None,
        "boundary_error_seconds_mean": sum(abs(c["start"]-a["corrected_start"])+abs(c["end"]-a["corrected_end"]) for c, a in boundaries) / (2*len(boundaries)) if boundaries else None,
        "selected_duplicate_overlap_rate": sum(value >= .5 for value in selected_overlaps) / len(selected_overlaps) if selected_overlaps else 0.0,
        "selected_temporal_region_coverage": len(selected_regions) / 6,
        "hook_acceptance": _acceptance(labels, "hook"), "context_acceptance": _acceptance(labels, "context"),
        "payoff_acceptance": _acceptance(labels, "payoff"), "caption_acceptance": _acceptance(labels, "caption_accuracy")}
    return {"status": "EVALUATED", "human_label_count": len(labels), "metrics": metrics}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Calculate metrics from human-reviewed anime highlight data")
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate_dataset(load_dataset(args.dataset)), ensure_ascii=False, indent=2))
