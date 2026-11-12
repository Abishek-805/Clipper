"""Deterministic synthetic quality benchmark for the heuristic candidate selector."""
from __future__ import annotations

import json
import time
from pathlib import Path

from clipper import Word, _interval_iou, select_clips


def build_benchmark() -> dict:
    duration = 240
    motion, audio = [0.0] * duration, [0.0] * duration
    motion[18:45] = [8.0] * 27
    audio[70:96] = [6.0] * 26
    motion[170:198] = [7.0] * 28
    words = [Word(float(t), float(t) + 0.35, "spoken") for t in range(120, 150)]
    words[-1] = Word(149.0, 149.5, "complete.")
    expected = [
        {"label": "motion-heavy", "start": 18.0, "end": 45.0},
        {"label": "audio-heavy", "start": 70.0, "end": 96.0},
        {"label": "dialogue-heavy", "start": 120.0, "end": 149.5},
        {"label": "silent-visual", "start": 170.0, "end": 198.0},
    ]
    cuts = [18, 45, 70, 96, 120, 150, 170, 198]
    started = time.perf_counter()
    selection = select_clips(duration, motion, audio, cuts, words, count=8, min_length=20, max_length=55)
    elapsed = time.perf_counter() - started
    candidates = selection["candidates"]
    selected = selection["selected"]
    candidate_recall = sum(any(_interval_iou(candidate, event) >= 0.30 for candidate in candidates)
                           for event in expected) / len(expected)
    matched = [max((_interval_iou(clip, event) for event in expected), default=0.0) for clip in selected]
    precision = sum(iou >= 0.30 for iou in matched) / len(selected) if selected else 0.0
    pair_count = len(selected) * (len(selected) - 1) / 2
    duplicates = sum(_interval_iou(selected[i], selected[j]) > 0.35
                     for i in range(len(selected)) for j in range(i + 1, len(selected)))
    invalid = sum(not (0 <= clip["start"] < clip["end"] <= duration and
                       20 <= clip["end"] - clip["start"] <= 55 and
                       not any(word.start < clip["start"] < word.end or word.start < clip["end"] < word.end for word in words))
                 for clip in selected)
    return {
        "benchmark": "synthetic-candidate-selection-v1",
        "scope": "deterministic signal fixture only; not a real-world entertainment-quality claim",
        "expected_intervals": expected,
        "candidate_count": selection["candidate_count"],
        "rejected_count": selection["rejected_count"],
        "selected_count": len(selected),
        "selection_runtime_seconds": round(elapsed, 6),
        "candidate_scoring_per_second": round(selection["candidate_count"] / elapsed, 1) if elapsed else None,
        "selected_intervals": [{"start": c["start"], "end": c["end"], "duration": c["duration"],
                                "score": c["score"], "reason": c["reason"]} for c in selected],
        "metrics": {
            "candidate_recall": round(candidate_recall, 4),
            "selected_interval_precision": round(precision, 4),
            "mean_best_temporal_iou": round(sum(matched) / len(matched), 4) if matched else 0.0,
            "selected_duplicate_rate": round(duplicates / pair_count, 4) if pair_count else 0.0,
            "average_clip_duration_seconds": round(sum(c["duration"] for c in selected) / len(selected), 3) if selected else 0.0,
            "invalid_boundary_count": invalid,
        },
        "inactive_interval_seconds": [210, 240],
    }


def write_report(result: dict, json_path: Path, markdown_path: Path):
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    metrics = result["metrics"]
    rows = [
        "# Task 03 synthetic candidate benchmark", "",
        f"Scope: {result['scope']}", "",
        f"Candidates generated: **{result['candidate_count']}** · rejected: **{result['rejected_count']}** · selected: **{result['selected_count']}**", "",
        f"Synthetic selection runtime: **{result['selection_runtime_seconds']:.6f}s** ({result['candidate_scoring_per_second']:.1f} proposals/s)", "",
        "| Metric | Result |", "| --- | ---: |",
        f"| Candidate recall | {metrics['candidate_recall']:.4f} |",
        f"| Selected interval precision | {metrics['selected_interval_precision']:.4f} |",
        f"| Mean best temporal IoU | {metrics['mean_best_temporal_iou']:.4f} |",
        f"| Selected duplicate rate | {metrics['selected_duplicate_rate']:.4f} |",
        f"| Average clip duration | {metrics['average_clip_duration_seconds']:.3f}s |",
        f"| Invalid boundaries | {metrics['invalid_boundary_count']} |",
        "", "Selected intervals:", "",
    ]
    rows.extend(f"- {c['start']:.1f}–{c['end']:.1f}s ({c['duration']:.1f}s; score {c['score']:.3f}) — {c['reason']}"
                for c in result["selected_intervals"])
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    output = build_benchmark()
    write_report(output, Path("reports/task-03-benchmark.json"), Path("reports/task-03-benchmark.md"))
    print(json.dumps(output["metrics"], indent=2))
