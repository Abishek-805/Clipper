"""Resumable, local-only Qwen analysis of cached chronological episode frames."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import time
from pathlib import Path

from evaluation.llama_cpp_vlm import LlamaCppVisualEventAnalyzer

MODEL_REVISION = "Qwen/Qwen3-VL-2B-Instruct-GGUF@52d6c8ffea26cc873ac5ad116f8631268d7eb503"
RUNTIME_VERSION = "llama.cpp b12284 / 9e9619d"
PROMPT_VERSION = "episode-window-v1"
SCHEMA_VERSION = "clipper-episode-vlm-window-v1"
CONTEXT_VERSION = "episode-context-bounded-interval-v3"


def build_windows(evidence: list[dict], frame_root: Path, words: list[dict], size: int = 8) -> list[dict]:
    ordered = sorted(evidence, key=lambda item: float(item["timestamp"]))
    windows = []
    for offset in range(0, len(ordered), size):
        frames = ordered[offset:offset + size]
        start, end = float(frames[0]["timestamp"]), float(frames[-1]["timestamp"])
        excerpt = " ".join(str(word["text"]) for word in words
                           if float(word["end"]) >= start and float(word["start"]) <= end)
        windows.append({"start": start, "end": end,
                        "timestamps": [float(frame["timestamp"]) for frame in frames],
                        "frames": [str(frame_root / frame["path"]) for frame in frames],
                        "transcript_excerpt": excerpt})
    return windows


def cache_key(source_fingerprint: str, window: dict) -> str:
    identity = {"source_fingerprint": source_fingerprint, "model_revision": MODEL_REVISION,
                "runtime_version": RUNTIME_VERSION, "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION, "timestamps": window["timestamps"],
                "transcript_fingerprint": hashlib.sha256(window["transcript_excerpt"].encode()).hexdigest()}
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _overlap(left: dict, right: dict) -> float:
    common = max(0.0, min(left["end"], right["end"]) - max(left["start"], right["start"]))
    union = max(left["end"], right["end"]) - min(left["start"], right["start"])
    return common / union if union else 0.0


def write_review_board(output: Path, records: list[dict], baseline: list[dict], production: list[dict],
                       chapters: list[dict]) -> None:
    cards = []
    for item in records:
        result = item["result"]
        nearest_signal = max(baseline, key=lambda candidate: _overlap(item, candidate), default=None)
        nearest_clip = max(production, key=lambda candidate: _overlap(item, candidate), default=None)
        pics = "".join(f'<a href="../candidate_review_06f/frames/{html.escape(Path(frame).name)}">'
                       f'<img loading="lazy" src="../candidate_review_06f/frames/{html.escape(Path(frame).name)}"></a>'
                       for frame in item["frames"])
        cards.append(f"<article><h2>{item['window_id']} · {item['start']:.2f}–{item['end']:.2f}s</h2>"
                     f"<p><b>Model category (unreviewed):</b> {html.escape(result['category'])} · "
                     f"confidence {result['confidence']:.2f}</p><p>{html.escape(result['description'])}</p>"
                     f"<p><b>Actions:</b> {html.escape(', '.join(result['actions']))}</p>"
                     f"<p><b>Transcript, cached Whisper:</b> {html.escape(item['transcript_excerpt'])}</p>"
                     f"<p><b>Uncertainty:</b> {html.escape(result['uncertainty'])}</p>"
                     f"<p><b>Nearest 06F proposal:</b> {html.escape(str(nearest_signal.get('event_id') if nearest_signal else 'none'))}; "
                     f"<b>nearest selected clip:</b> {html.escape(str(nearest_clip.get('candidate_id') if nearest_clip else 'none'))}</p>"
                     f"<p><b>Evidence:</b> {html.escape(str(item['timestamps']))}</p><div class='frames'>{pics}</div></article>")
    baseline_rows = "".join(f"<li>{html.escape(str(event.get('event_id')))} "
                             f"{event.get('start', 0):.2f}–{event.get('end', 0):.2f}s — "
                             f"{html.escape(str(event.get('event_type', event.get('category', 'unknown'))))}</li>"
                             for event in baseline)
    document = ("<!doctype html><meta charset='utf-8'><title>Task 06G episode review</title>"
                "<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;"
                "background:#11151d;color:#e9edf4}article{padding:1rem;margin:1rem 0;background:#1c2431;"
                "border-radius:12px}.frames{display:flex;gap:.5rem;overflow:auto}.frames img{height:130px}"
                "small{color:#9bb}</style><h1>Task 06G · chronological model review</h1>"
                "<p>All model claims are unreviewed. Transcript text is cached Whisper output;"
                " it is not a visual claim. Heuristic proposals below are comparison only.</p>"
                f"<p>Analyzed {len(records)} windows, {sum(len(x['frames']) for x in records)} cached frames.</p>"
                f"<p><a href='phase_b_dialogue_context.json'>Open dialogue and visual-context experiment</a></p>"
                f"<h2>Chronological episode memory</h2><ol>"
                + "".join(f"<li>{chapter['chapter_id']} · {chapter['start']:.2f}–{chapter['end']:.2f}s — "
                         f"{html.escape(chapter['result']['scene_summary'])}; uncertainty: "
                         f"{html.escape('; '.join(chapter['result']['uncertainties']))}</li>" for chapter in chapters)
                + f"</ol><h2>Task 06F proposal comparison</h2><ol>{baseline_rows}</ol>"
                "<h2>Existing production selections</h2><ol>"
                + "".join(f"<li>{html.escape(str(clip.get('candidate_id')))} "
                         f"{clip.get('start', 0):.2f}–{clip.get('end', 0):.2f}s; "
                         f"score {clip.get('score')}</li>" for clip in production)
                + "</ol>" + "".join(cards))
    (output / "index.html").write_text(document + "</html>", encoding="utf-8")


def analyze_episode(timeline_path: Path, brain_path: Path, analysis_path: Path,
                    output: Path, analyzer=None) -> dict:
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    brain = json.loads(brain_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    source_fp = brain["source"].get("fingerprint") or brain["source"].get("source_fingerprint")
    if not source_fp:
        raise ValueError("Episode source fingerprint is required for safe cache reuse")
    evidence = timeline["coarse_frame_evidence"]
    frame_root = brain_path.parent / "frames"
    windows = build_windows(evidence, frame_root, brain["transcript"]["words"])
    output.mkdir(parents=True, exist_ok=True)
    analyzer = analyzer or LlamaCppVisualEventAnalyzer(timeout=600)
    results = []
    for index, window in enumerate(windows, 1):
        key = cache_key(source_fp, window)
        target = output / f"window_{index:03d}_{key[:12]}.json"
        if target.exists():
            item = json.loads(target.read_text(encoding="utf-8"))
            if item.get("cache_key") == key:
                results.append(item)
                continue
        missing = [path for path in window["frames"] if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError(f"Cached source frame missing: {missing[0]}")
        request = {"frames": window["frames"], "timestamps": window["timestamps"],
                   "transcript_excerpt": window["transcript_excerpt"]}
        try:
            result = analyzer.analyze(request)
        except ValueError as exc:
            if "valid JSON" not in str(exc):
                raise
            request["max_tokens"] = 768
            result = analyzer.analyze(request)
        item = {"cache_key": key, "window_id": f"window_{index:03d}",
                "source_fingerprint": source_fp, "model_revision": MODEL_REVISION,
                "runtime_version": RUNTIME_VERSION, "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION, **window, "result": result}
        target.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(item)
        print(f"{index}/{len(windows)} {window['start']:.2f}-{window['end']:.2f}s "
              f"frames={len(window['frames'])} usage={item['result'].get('usage')}", flush=True)
    baseline = brain.get("moments", [])
    production = analysis.get("clips", [])
    chapters = []
    for index in range(0, len(results), 3):
        group = results[index:index + 3]
        start, end = group[0]["start"], group[-1]["end"]
        chapter = {"start": start, "end": end,
                   "timestamps": [t for item in group for t in item["timestamps"]],
                   "visual_summaries": [{"timestamp": item["start"],
                       "description": item["result"]["description"],
                       "entities": item["result"]["entities"], "actions": item["result"]["actions"],
                       "uncertainty": item["result"]["uncertainty"]} for item in group],
                   "transcript_turns": [{"turn_id": turn["phrase_id"], "start": turn["start"],
                       "end": turn["end"], "text": turn["exact_text"]} for turn in brain["important_phrase_index"]
                       if turn["end"] >= start and turn["start"] <= end]}
        key = cache_key(source_fp, {"timestamps": chapter["timestamps"],
                                    "transcript_excerpt": json.dumps([CONTEXT_VERSION, chapter["visual_summaries"],
                                                                       chapter["transcript_turns"]], ensure_ascii=False)})
        target = output / f"chapter_{index // 3 + 1:02d}_{key[:12]}.json"
        if target.exists():
            item = json.loads(target.read_text(encoding="utf-8"))
            if item.get("cache_key") == key:
                chapters.append(item)
                continue
        t0 = time.perf_counter()
        item = {"cache_key": key, "chapter_id": f"chapter_{index // 3 + 1:02d}", **chapter,
                "inference_seconds": None,
                "result": analyzer.analyze_context(chapter)}
        item["inference_seconds"] = round(time.perf_counter() - t0, 3)
        target.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        chapters.append(item)
        print(f"{item['chapter_id']} {start:.2f}-{end:.2f}s "
              f"usage={item['result'].get('usage')}", flush=True)
    # A final text-only synthesis uses the cached chapter memories, never the selector's proposal list.
    episode_key = cache_key(source_fp, {"timestamps": [c["start"] for c in chapters],
                                       "transcript_excerpt": json.dumps([CONTEXT_VERSION, [c["result"] for c in chapters]], ensure_ascii=False)})
    episode_path = output / f"episode_memory_{episode_key[:12]}.json"
    if episode_path.exists():
        episode_memory = json.loads(episode_path.read_text(encoding="utf-8"))
        if episode_memory.get("cache_key") != episode_key:
            episode_memory = None
    else:
        episode_memory = None
    if episode_memory is None:
        t0 = time.perf_counter()
        request = {"start": 0, "end": float(timeline["duration"]),
                   "timestamps": [c["start"] for c in chapters],
                   "visual_summaries": [{"timestamp": c["start"],
                       "description": c["result"]["scene_summary"],
                       "uncertainty": "Unreviewed chapter model summary."} for c in chapters],
                   "transcript_turns": []}
        episode_memory = {"cache_key": episode_key, "inference_seconds": None,
                          "result": analyzer.analyze_context(request)}
        episode_memory["inference_seconds"] = round(time.perf_counter() - t0, 3)
        episode_path.write_text(json.dumps(episode_memory, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review_board(output, results, baseline, production, chapters)
    summary = {"source_fingerprint": source_fp, "source_duration": timeline["duration"],
               "coverage_complete": len(evidence) == len(timeline["coarse_frame_timestamps"]),
               "cached_frame_count": len(evidence), "analyzed_window_count": len(results),
               "model_revision": MODEL_REVISION, "runtime_version": RUNTIME_VERSION,
               "cache_key_version": SCHEMA_VERSION,
               "task06f_proposal_count": len(baseline), "production_selection_count": len(production),
               "chapter_count": len(chapters), "episode_memory": episode_memory["result"],
               "production_selections": [{"candidate_id": c.get("candidate_id"), "start": c["start"],
                                          "end": c["end"], "score": c.get("score")}
                                         for c in production],
               "windows": [{"window_id": item["window_id"], "start": item["start"], "end": item["end"],
                            "category": item["result"]["category"], "confidence": item["result"]["confidence"],
                            "description": item["result"]["description"],
                            "uncertainty": item["result"]["uncertainty"],
                            "usage": item["result"].get("usage")} for item in results]}
    (output / "episode_vlm_memory.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--brain", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = analyze_episode(args.timeline, args.brain, args.analysis, args.output)
    print(json.dumps({key: value for key, value in summary.items() if key != "windows"}, indent=2))


if __name__ == "__main__":
    main()
