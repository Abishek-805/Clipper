"""Build the Task 06H evidence graph and review board exclusively from cached artifacts."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import html
import json
import os
import time
from collections import Counter
from pathlib import Path

from evaluation.anime_review import evaluate_dataset, load_dataset, save_dataset
from evaluation.story_graph import build_story_graph, make_review_dataset, save_story_graph


def peak_working_set_bytes() -> int | None:
    if os.name != "nt":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PeakWorkingSetSize)


def build(episode_root: Path, output: Path | None = None) -> dict:
    started = time.perf_counter()
    f_root, g_root = episode_root / "candidate_review_06f", episode_root / "candidate_review_06g"
    brain_path = f_root / "episode_brain.json"
    memory_path = g_root / "episode_vlm_memory.json"
    old_data_path = f_root / "anime_highlight_dataset.json"
    brain = json.loads(brain_path.read_text(encoding="utf-8"))
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    source_fp = brain.get("source", {}).get("fingerprint")
    if source_fp != memory.get("source_fingerprint"):
        raise ValueError("Task 06F and 06G caches do not share a source fingerprint")
    window_files = sorted(g_root.glob("window_*.json"))
    windows = [json.loads(path.read_text(encoding="utf-8")) for path in window_files]
    if not memory.get("coverage_complete") or len(windows) != memory.get("analyzed_window_count"):
        raise ValueError("Cached 06G window coverage is incomplete; refusing a partial episode graph")
    for window in windows:
        if window.get("source_fingerprint") != source_fp:
            raise ValueError(f"Window {window.get('window_id')} belongs to a different source")
        if len(window.get("frames", [])) != len(window.get("timestamps", [])):
            raise ValueError(f"Window {window.get('window_id')} has mismatched cached frame timestamps")
        missing = [frame for frame in window["frames"] if not Path(frame).is_file()]
        if missing:
            raise FileNotFoundError(f"Cached evidence frame is missing: {missing[0]}")

    target = output or episode_root / "candidate_review_06h"
    existing_dataset = target / "anime_highlight_dataset.json"
    task06f_data = load_dataset(old_data_path)
    review_source = load_dataset(existing_dataset) if existing_dataset.is_file() else task06f_data
    graph = build_story_graph(brain, memory, windows, task06f_data["candidates"])
    target.mkdir(parents=True, exist_ok=True)
    save_story_graph(target / "story_graph.json", graph)
    dataset = make_review_dataset(review_source, graph, windows)

    source_file = Path(brain["source"]["path"])
    if not source_file.is_file():
        raise FileNotFoundError("Original source media is unavailable for review playback")
    source_url = source_file.resolve().as_uri()
    cached_frames = sorted((float(row["frame_timestamp"]), Path(row["frame_path"]).name)
                           for row in graph["evidence"] if row.get("evidence_type") == "frame")
    for candidate in dataset["candidates"]:
        candidate["source_playback_url"] = f"{source_url}#t={float(candidate['start']):.3f}"
        times = [float(candidate["start"]), (float(candidate["start"])+float(candidate["end"]))/2,
                 max(float(candidate["start"]), float(candidate["end"])-.001)]
        selected = [min(cached_frames, key=lambda pair: abs(pair[0]-timestamp)) for timestamp in times]
        candidate["frames"] = [f"../candidate_review_06f/frames/{name}" for _, name in selected]
        candidate["frame_timestamps"] = [timestamp for timestamp, _ in selected]
        if candidate.get("preview"):
            preview = target / candidate["preview"]
            if not preview.is_file() and (f_root / candidate["preview"]).is_file():
                candidate["preview"] = f"../candidate_review_06f/{candidate['preview']}"
            elif not preview.is_file() and not (f_root / candidate["preview"]).is_file():
                candidate["preview"] = ""

    save_dataset(target / "anime_highlight_dataset.json", dataset)
    template = Path(__file__).with_name("review_board.html").read_text(encoding="utf-8")
    embedded = template.replace("__DATA__", json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/"))
    summary = graph["summary"]
    evidence_summary = (f"<h2>Task 06H evidence graph</h2><p>{summary['node_count']} nodes · "
        f"{summary['edge_count']} evidence-backed relationships · {summary['claim_count']} unverified claims · "
        f"{summary['event_count']} event proposals · {summary['evidence_count']} evidence records. "
        "No editorial recommendations were issued; model confidence is not calibrated.</p>"
        "<p>Cards open the original local source at the recorded start time. Use its player to review the full interval. "
        "Cached Qwen descriptions remain hypotheses; exact dialogue comes from the Task 06F Whisper cache.</p>")
    relevant = [node for node in graph["nodes"] if node.get("node_type") in
                {"dialogue_turn", "phrase", "visual_observation", "event"}]
    rows = []
    for node in sorted(relevant, key=lambda item: (item.get("start", 0), item["node_type"], item["node_id"])):
        label = node.get("exact_text") or node.get("exact_quote") or node.get("description") or node.get("event_type", "")
        state = node.get("status", node.get("visual_status", node.get("speaker_status", "unverified")))
        rows.append("<tr><td>" + f"{float(node.get('start', 0)):.2f}–{float(node.get('end', 0)):.2f}s" +
            "</td><td>" + html.escape(node["node_type"]) + "</td><td>" + html.escape(str(label)) +
            "</td><td>" + html.escape(str(state)) + "</td><td><code>" + html.escape(node["node_id"]) + "</code></td></tr>")
    relation_rows = []
    node_map = {node["node_id"]: node for node in graph["nodes"]}
    for edge in graph["edges"]:
        if edge["relationship"] not in {"possible_response_to", "possible_callback_to", "before", "same_sequence_candidate"}:
            continue
        left, right = node_map[edge["source"]], node_map[edge["target"]]
        relation_rows.append("<tr><td>" + html.escape(str(left.get("identity", edge["source"]))) +
            "</td><td>" + html.escape(edge["relationship"]) + "</td><td>" +
            html.escape(str(right.get("identity", edge["target"]))) + "</td><td>" +
            html.escape(edge["status"]) + "</td><td>" + str(len(edge["evidence_ids"])) + "</td></tr>")
    graph_view = ("<details><summary>Chronological episode graph: dialogue, phrases, visual notes and events</summary>"
        "<p>Chronology is source grounded. Every listed interpretation remains unverified until reviewed.</p>"
        "<div style='max-height:55vh;overflow:auto'><table><thead><tr><th>Source time</th><th>Record</th>"
        "<th>Verbatim transcript or cached observation</th><th>Status</th><th>ID</th></tr></thead><tbody>" +
        "".join(rows) + "</tbody></table></div></details>"
        "<details><summary>Tentative relationships and temporal links</summary><div style='max-height:35vh;overflow:auto'>"
        "<table><thead><tr><th>From</th><th>Relationship</th><th>To</th><th>Status</th><th>Evidence records</th>"
        "</tr></thead><tbody>" + "".join(relation_rows) + "</tbody></table></div></details>")
    embedded = embedded.replace("<main id=\"cards\"></main>", f"{evidence_summary}{graph_view}<main id=\"cards\"></main>")
    (target / "index.html").write_text(embedded, encoding="utf-8")

    eval_metrics = evaluate_dataset(dataset)
    category_counts = Counter(str(row.get("result", {}).get("category", "unknown")).casefold() for row in windows)
    cached_usage = [row.get("result", {}).get("usage", {}) for row in windows]
    relation_counts = Counter(edge["relationship"] for edge in graph["edges"])
    counts = {"graph": summary, "review_candidates": len(dataset["candidates"]),
        "human_annotations": len(dataset["annotations"]), "human_metrics": eval_metrics,
        "old_task06f_candidates": len(task06f_data["candidates"]),
        "task06f_production_selections": sum(bool(row.get("selected")) for row in task06f_data["candidates"]),
        "task06g_cached_windows_reused": len(windows), "task06g_cached_frames_reused": sum(len(w["frames"]) for w in windows),
        "task06g_cached_categories": dict(category_counts),
        "task06g_cached_action_lists": sum(bool(row.get("result", {}).get("actions")) for row in windows),
        "task06g_cached_prompt_tokens": sum(int(row.get("prompt_tokens", 0)) for row in cached_usage),
        "task06g_cached_completion_tokens": sum(int(row.get("completion_tokens", 0)) for row in cached_usage),
        "graph_relationship_types": dict(relation_counts),
        "model_inference_count": 0, "cache_reuse_rate": 1.0, "rendered_clips": 0,
        "editorial_recommendations": [], "editorial_status": "NOT_ASSESSED_NO_HUMAN_GROUND_TRUTH",
        "graph_build_seconds": round(time.perf_counter() - started, 3),
        "builder_process_peak_working_set_bytes": peak_working_set_bytes(),
        "source_fingerprint": source_fp, "source_duration": brain["source_duration"]}
    (target / "build_metrics.json").write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_root", type=Path, help="Folder containing candidate_review_06f and candidate_review_06g")
    parser.add_argument("--output", type=Path, help="Defaults to <episode_root>/candidate_review_06h")
    args = parser.parse_args()
    print(json.dumps(build(args.episode_root, args.output), ensure_ascii=False, indent=2))
