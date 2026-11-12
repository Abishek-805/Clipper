"""Human-labelled Clipper evaluation; empty or small sets never report accuracy."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

SCHEMA = "clipper-golden-v1"
BOOL_FIELDS = ("keep", "self_contained", "payoff_lands", "framing_acceptable", "captions_acceptable")


def validate_labels(data: dict) -> list[dict]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("labels"), list):
        raise ValueError(f"Expected {SCHEMA} with a labels array")
    for index, row in enumerate(data["labels"]):
        if not isinstance(row, dict) or not isinstance(row.get("source_id"), str) or not row["source_id"].strip():
            raise ValueError(f"Label {index} has no source_id")
        start, end = row.get("start"), row.get("end")
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or
                not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end) or end <= start):
            raise ValueError(f"Label {index} has invalid time bounds")
        if not isinstance(row.get("keep"), bool):
            raise ValueError(f"Label {index} must include a human keep decision")
        for field in BOOL_FIELDS[1:]:
            if row.get(field) is not None and not isinstance(row[field], bool):
                raise ValueError(f"Label {index} field {field} must be boolean or null")
        if row.get("category") is not None and not isinstance(row["category"], str):
            raise ValueError(f"Label {index} category must be text or null")
    return data["labels"]


def interval_iou(left: dict, right: dict) -> float:
    intersection = max(0.0, min(left["end"], right["end"]) - max(left["start"], right["start"]))
    union = max(left["end"], right["end"]) - min(left["start"], right["start"])
    return intersection / union if union > 0 else 0.0


def evaluate(labels: list[dict], predictions: list[dict], minimum_labels: int = 5) -> dict:
    if len(labels) < minimum_labels:
        return {"status": "NOT ENOUGH DATA", "human_label_count": len(labels),
                "required_label_count": minimum_labels, "metrics": None}
    matched, human_kept_hits = [], set()
    selected_predictions = {index for index, prediction in enumerate(predictions) if prediction.get("selected")}
    possible_matches = []
    for pi, prediction in enumerate(predictions):
        candidates = [(interval_iou(prediction, label), li) for li, label in enumerate(labels)
                      if prediction.get("source_id") == label.get("source_id")]
        if candidates:
            score, li = max(candidates)
            if score >= .1:
                possible_matches.append((score, pi, li))
    prediction_labels = {}
    matched_labels = set()
    for score, pi, li in sorted(possible_matches, reverse=True):
        if pi in prediction_labels or li in matched_labels:
            continue
        prediction_labels[pi] = (li, score)
        matched_labels.add(li)
        matched.append((predictions[pi], labels[li], score))
        if pi in selected_predictions and labels[li]["keep"]:
            human_kept_hits.add(li)
    tp = sum(1 for pi in selected_predictions if pi in prediction_labels and labels[prediction_labels[pi][0]]["keep"])
    precision = tp / len(selected_predictions) if selected_predictions else None
    recall = len(human_kept_hits) / sum(row["keep"] for row in labels) if any(row["keep"] for row in labels) else None
    categorized = [(prediction, label, iou) for prediction, label, iou in matched
                   if prediction.get("editorial_category") and label.get("category")]
    visual = [(prediction, label) for prediction, label, _ in matched if prediction.get("selected")]
    def acceptance(field):
        values = [label[field] for _, label in visual if label.get(field) is not None]
        return sum(values) / len(values) if values else None
    matched_kept = [(prediction, label, iou) for prediction, label, iou in matched if label["keep"]]
    return {"status": "EVALUATED", "human_label_count": len(labels), "prediction_count": len(predictions),
            "matched_count": len(matched), "metrics": {
                "temporal_iou_mean": sum(iou for _, _, iou in matched) / len(matched) if matched else None,
                "keep_precision": precision, "keep_recall": recall,
                "category_agreement": sum(pred.get("editorial_category") == label.get("category")
                                           for pred, label, _ in categorized) / len(categorized) if categorized else None,
                "boundary_error_seconds_mean": (sum(abs(pred["start"] - label["start"]) +
                                                     abs(pred["end"] - label["end"]) for pred, label, _ in matched_kept) /
                                                 (2 * len(matched_kept)) if matched_kept else None),
                "framing_acceptance": acceptance("framing_acceptable"),
                "caption_acceptance": acceptance("captions_acceptable")}}


def predictions_from_manifest(manifest: dict) -> list[dict]:
    source = manifest.get("source") or manifest.get("source_path")
    candidates = manifest.get("candidate_selection", {}).get("candidates", [])
    return [{"source_id": source, "start": row["start"], "end": row["end"],
             "selected": bool(row.get("selected")), "editorial_category": row.get("editorial_category"),
             "score": row.get("score")} for row in candidates if "start" in row and "end" in row]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Clipper analysis.json files against human labels")
    parser.add_argument("labels", type=Path)
    parser.add_argument("manifests", nargs="+", type=Path)
    args = parser.parse_args()
    labels = validate_labels(json.loads(args.labels.read_text(encoding="utf-8")))
    predictions = []
    for path in args.manifests:
        predictions.extend(predictions_from_manifest(json.loads(path.read_text(encoding="utf-8"))))
    result = evaluate(labels, predictions)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "EVALUATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
