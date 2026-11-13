"""Build and validate evidence-first episode story graphs from cached Task 06F/06G data."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from evaluation.anime_review import validate_dataset

SCHEMA = "clipper-evidence-story-graph-v1"
EVIDENCE_TYPES = {"frame", "frame_sequence", "transcript_word", "transcript_turn", "subtitle_cue",
                  "audio_event", "scene_interval", "cached_signal", "model_observation", "human_review"}
CLAIM_STATUSES = {"directly_observed", "transcript_stated", "visually_supported_inference", "weakly_supported",
                  "contradicted", "unverified", "human_confirmed", "human_rejected"}
RELATIONSHIPS = {"occurs_in", "before", "after", "overlaps", "part_of", "visually_supports",
                 "transcript_supports", "audio_supports", "possible_response_to", "possible_cause_of",
                 "possible_callback_to", "same_sequence_candidate", "contradicts", "human_confirmed_relationship"}
_ATTACK = ("attack", "strik", "swing", "slash", "thrust", "punch", "kick", "fire at", "shoot")
_RESPONSE = ("defend", "block", "dodge", "impact", "hit", "collid", "recoil", "fall", "react", "counter")
_INSTRUCTION = ("skill activation", "activate the skill", "technique", "instruction", "training")
_COMBAT = ("fight", "battle", "combat", "attacking", "attack", "defending", "defense")


def cross_modal_flags(transcript: str, result: dict) -> list[str]:
    """Surface narrow dialogue/visual label disagreement without declaring either side false."""
    dialogue = transcript.casefold()
    visual = " ".join([str(result.get("category", "")), str(result.get("description", "")),
                        *[str(x) for x in result.get("actions", [])]]).casefold()
    instructional = any(term in dialogue for term in _INSTRUCTION) or (
        "skill" in dialogue and any(term in dialogue for term in ("activate", "activation", "start your move")))
    combat_label = result.get("category", "").casefold() in {"action", "fight", "battle", "combat"}
    if instructional and (combat_label or any(term in visual for term in _COMBAT)):
        return ["instructional_dialogue_vs_combat_visual_claim_requires_human_review"]
    return []


def _id(kind: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{kind}_{digest}"


def _interval(start: float, end: float, duration: float) -> tuple[float, float]:
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           for x in (start, end, duration)) or not 0 <= start < end <= duration:
        raise ValueError(f"Invalid source interval: {start!r}–{end!r} of {duration!r}")
    return float(start), float(end)


def _overlap(a: dict, b: dict) -> float:
    intersection = max(0.0, min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"])))
    union = max(float(a["end"]), float(b["end"])) - min(float(a["start"]), float(b["start"]))
    return intersection / union if union else 0.0


def _transcript_tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", text.casefold(), flags=re.UNICODE)


def action_progression(actions_by_timestamp: list[tuple[float, list[str], list[str]]]) -> dict:
    """Return a tentative hypothesis from distinct timestamp-local observations only."""
    ordered = sorted(actions_by_timestamp, key=lambda row: row[0])
    attack_at = [t for t, actions, _ in ordered if any(x in " ".join(actions).casefold() for x in _ATTACK)]
    response_at = [t for t, actions, _ in ordered if any(x in " ".join(actions).casefold() for x in _RESPONSE)]
    participants = {name for _, _, entities in ordered for name in entities if name and name != "unknown"}
    supported = (len({row[0] for row in ordered}) >= 2 and len(participants) >= 2 and bool(attack_at) and bool(response_at) and
                 any(abs(a-r) >= .1 for a in attack_at for r in response_at))
    return {"type": "possible_fight" if supported else "unknown_event",
            "status": "unverified" if supported else "unverified",
            "evidence_timestamps": sorted({*attack_at, *response_at}) if supported else [],
            "participant_hypothesis_count": len(participants),
            "reason": ("Multiple sampled times contain attack and response language with at least two unverified entity labels."
                       if supported else "Insufficient temporal and participant evidence; a static pose or single action label is not a fight.")}


def validate_story_graph(graph: dict) -> dict:
    if not isinstance(graph, dict) or graph.get("schema") != SCHEMA:
        raise ValueError(f"Expected {SCHEMA}")
    duration, source_fp = graph.get("source_duration"), graph.get("source_fingerprint")
    if not isinstance(source_fp, str) or len(source_fp) != 64 or any(c not in "0123456789abcdef" for c in source_fp.casefold()):
        raise ValueError("Graph needs a 64-character source fingerprint")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Graph needs a positive finite source duration")
    cache = graph.get("cache_identity", {})
    if (cache.get("source_fingerprint") != source_fp or not isinstance(cache.get("window_ids"), list) or
            not isinstance(cache.get("transcript_fingerprint"), str) or len(cache["transcript_fingerprint"]) != 64):
        raise ValueError("Graph cache identity is incomplete or belongs to a different source")
    evidence, nodes, edges, claims = graph.get("evidence"), graph.get("nodes"), graph.get("edges"), graph.get("claims")
    if not all(isinstance(rows, list) for rows in (evidence, nodes, edges, claims)):
        raise ValueError("Graph evidence, nodes, edges, and claims must be arrays")
    evidence_ids = [row.get("evidence_id") for row in evidence]
    node_ids = [row.get("node_id") for row in nodes]
    claim_ids = [row.get("claim_id") for row in claims]
    edge_ids = [row.get("edge_id") for row in edges]
    if any(not isinstance(x, str) or not x for x in evidence_ids + node_ids + claim_ids):
        raise ValueError("Every evidence, node, and claim requires a string ID")
    if any(not isinstance(x, str) or not x for x in edge_ids):
        raise ValueError("Every relationship requires a string ID")
    if any(len(ids) != len(set(ids)) for ids in (evidence_ids, node_ids, claim_ids, edge_ids)):
        raise ValueError("Evidence, node, claim, and relationship IDs must be unique")
    known_evidence, known_nodes = set(evidence_ids), set(node_ids)
    for item in evidence:
        if item.get("source_fingerprint") != source_fp or item.get("evidence_type") not in EVIDENCE_TYPES:
            raise ValueError("Evidence source or type is invalid")
        start, end = item.get("start"), item.get("end")
        if start is not None or end is not None:
            _interval(start, end, duration)
        if item.get("validation_status") not in {"source_validated", "cached_unverified", "model_output"}:
            raise ValueError("Evidence validation status is invalid")
    evidence_by_id = {row["evidence_id"]: row for row in evidence}
    for node in nodes:
        if node.get("start") is not None or node.get("end") is not None:
            _interval(node.get("start"), node.get("end"), duration)
        if not set(node.get("evidence_ids", [])) <= known_evidence:
            raise ValueError("Node references missing evidence")
        progression = node.get("action_progression", {})
        if node.get("event_type") == "possible_fight" and (
                progression.get("type") != "possible_fight" or progression.get("status") != "unverified" or
                len(set(progression.get("evidence_timestamps", []))) < 2 or
                progression.get("participant_hypothesis_count", 0) < 2):
            raise ValueError("A possible fight needs distinct timestamped progression and multiple unverified participants")
    for claim in claims:
        if (claim.get("status") not in CLAIM_STATUSES or claim.get("source_fingerprint") != source_fp or
                claim.get("subject") not in known_nodes):
            raise ValueError("Claim status or source is invalid")
        _interval(claim.get("start"), claim.get("end"), duration)
        if not claim.get("evidence_ids") or not set(claim["evidence_ids"]) <= known_evidence:
            raise ValueError("Claim must reference existing evidence")
        if claim.get("predicate") == "possible_action" and not any(
                evidence_by_id[eid]["evidence_type"] in {"frame", "frame_sequence"} for eid in claim["evidence_ids"]):
            raise ValueError("An action claim requires visual frame evidence")
        if "model_reported_confidence" in claim and claim["model_reported_confidence"] is not None:
            value = claim["model_reported_confidence"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError("Model-reported confidence must be in [0,1]")
    for edge in edges:
        if edge.get("source") not in known_nodes or edge.get("target") not in known_nodes:
            raise ValueError("Relationship references a missing node")
        if edge.get("relationship") not in RELATIONSHIPS:
            raise ValueError("Relationship type is outside the controlled vocabulary")
        if edge.get("status") not in CLAIM_STATUSES:
            raise ValueError("Relationship status is invalid")
        if not edge.get("evidence_ids") or not set(edge["evidence_ids"]) <= known_evidence:
            raise ValueError("Relationship references missing evidence")
    return graph


def build_story_graph(brain: dict, episode_memory: dict, window_records: list[dict], baseline: list[dict]) -> dict:
    source = brain.get("source", {})
    source_fp = source.get("fingerprint")
    duration = float(brain.get("source_duration", 0))
    if not source_fp or duration <= 0:
        raise ValueError("Task 06F source fingerprint and duration are required")
    if episode_memory.get("source_fingerprint", source_fp) != source_fp or any(
            row.get("source_fingerprint", source_fp) != source_fp for row in window_records):
        raise ValueError("Task 06F, Task 06G, and cached windows must share a source fingerprint")
    edges: list[dict] = []
    claims: list[dict] = []
    evidence_by_id: dict[str, dict] = {}
    nodes_by_id: dict[str, dict] = {}

    def add_evidence(kind: str, identity: str, start: float | None, end: float | None, **details) -> str:
        if start is not None:
            start, end = _interval(start, end, duration)
        eid = _id("evidence", source_fp + "|" + kind + "|" + identity)
        row = {"evidence_id": eid, "source_fingerprint": source_fp, "evidence_type": kind,
               "start": start, "end": end, "original_source_reference": identity,
               "validation_status": "source_validated", **details}
        evidence_by_id[eid] = row
        return eid

    def add_node(kind: str, identity: str, **details) -> str:
        nid = _id(kind, source_fp + "|" + identity)
        if nid not in nodes_by_id:
            nodes_by_id[nid] = {"node_id": nid, "node_type": kind, "identity": identity, **details}
        return nid

    def add_edge(source_id: str, target_id: str, relationship: str, evidence_ids: list[str], status: str = "unverified") -> None:
        identity = f"{source_id}|{target_id}|{relationship}"
        if not any(e["source"] == source_id and e["target"] == target_id and e["relationship"] == relationship for e in edges):
            edges.append({"edge_id": _id("edge", identity), "source": source_id, "target": target_id,
                          "relationship": relationship, "status": status, "evidence_ids": sorted(set(evidence_ids))})

    word_evidence: dict[int, str] = {}
    words = brain.get("transcript", {}).get("words", [])
    for index, word in enumerate(words):
        start, end = _interval(word["start"], word["end"], duration)
        word_evidence[index] = add_evidence("transcript_word", f"word:{index}", start, end,
                                            language=brain.get("transcript", {}).get("language"),
                                            exact_text=str(word.get("text", "")),
                                            transcript_provenance=word.get("provenance", "cached_whisper"))
    turns = brain.get("transcript", {}).get("turns", [])
    turn_nodes: dict[str, str] = {}
    for turn in turns:
        start, end = _interval(turn["start"], turn["end"], duration)
        turn_words = [w for w in words if float(w["end"]) > start and float(w["start"]) < end]
        if _transcript_tokens(" ".join(str(w.get("text", "")) for w in turn_words)) != _transcript_tokens(turn["text"]):
            raise ValueError(f"Cached dialogue turn {turn['beat_id']} does not match its source word timestamps")
        tids = [word_evidence[i] for i, w in enumerate(words) if float(w["end"]) > start and float(w["start"]) < end]
        teid = add_evidence("transcript_turn", turn["beat_id"], start, end,
                            language=brain.get("transcript", {}).get("language"), exact_text=turn["text"],
                            word_evidence_ids=tids, transcript_provenance="cached_whisper_word_timestamps")
        tnode = add_node("dialogue_turn", turn["beat_id"], start=start, end=end, exact_text=turn["text"],
                         speaker="unknown", speaker_status="UNAVAILABLE_NO_DIARIZATION", evidence_ids=[teid])
        turn_nodes[turn["beat_id"]] = tnode

    for phrase in brain.get("important_phrase_index", []):
        start, end = _interval(phrase["start"], phrase["end"], duration)
        turn_id = phrase["phrase_id"]
        exact = phrase["exact_text"]
        phrase_words = [w for w in words if float(w["end"]) > start and float(w["start"]) < end]
        if _transcript_tokens(" ".join(str(w.get("text", "")) for w in phrase_words)) != _transcript_tokens(exact):
            raise ValueError(f"Cached phrase {turn_id} does not match its source word timestamps")
        word_ids = [word_evidence[i] for i, word in enumerate(words)
                    if float(word["end"]) > start and float(word["start"]) < end]
        eid = add_evidence("transcript_turn", f"phrase:{turn_id}", start, end,
                           language=brain.get("transcript", {}).get("language"), exact_text=exact,
                           word_evidence_ids=word_ids, source_scene_id=phrase.get("scene_id"),
                           transcript_provenance=phrase.get("provenance", "cached_whisper_word_timestamps"))
        pid = add_node("phrase", turn_id, start=start, end=end, exact_quote=exact,
                       importance="unknown_unreviewed", speaker="unknown", evidence_ids=[eid])
        if turn_id in turn_nodes:
            add_edge(pid, turn_nodes[turn_id], "part_of", [eid])

    scene_nodes: list[tuple[dict, str]] = []
    for scene in brain.get("scene_memory", []):
        start, end = _interval(scene["start"], scene["end"], duration)
        eid = add_evidence("scene_interval", scene["scene_id"], start, end,
                           source_scene_id=scene["scene_id"], scene_status=scene.get("visual_status"))
        nid = add_node("scene", scene["scene_id"], start=start, end=end,
                       visual_status=scene.get("visual_status", "UNKNOWN"), evidence_ids=[eid])
        scene_nodes.append((scene, nid))
    for turn in turns:
        turn_start = float(turn["start"])
        turn_node = turn_nodes[turn["beat_id"]]
        turn_evidence = nodes_by_id[turn_node]["evidence_ids"]
        for scene, scene_node in scene_nodes:
            if scene["start"] <= turn_start < scene["end"]:
                add_edge(turn_node, scene_node, "occurs_in", turn_evidence)
                break
    for signal in brain.get("timeline", {}).get("signal_bins", []):
        start, end = float(signal["start"]), float(signal["end"])
        eid = add_evidence("cached_signal", f"signal:{start:.3f}", start, end,
                           motion=signal.get("motion"), audio_energy=signal.get("audio_energy"),
                           word_count=signal.get("word_count"), scene_boundary=bool(signal.get("scene_boundary")),
                           interpretation="signal_only_not_semantic_evidence")
        _ = eid

    for track in brain.get("transcript", {}).get("subtitle_tracks", []):
        for index, cue in enumerate(track.get("cues", [])):
            start, end = _interval(cue["start"], cue["end"], duration)
            eid = add_evidence("subtitle_cue", f"subtitle:{track.get('stream_index')}:{index}", start, end,
                               language=track.get("language"), exact_text=cue.get("text", ""),
                               stream_index=track.get("stream_index"), validation_status="cached_unverified")
            _ = eid

    event_nodes: dict[str, dict] = {}
    for moment in brain.get("moments", []):
        start, end = _interval(moment["start"], moment["end"], duration)
        kind = "dialogue_event" if moment.get("exact_quote") else "unknown_event"
        ids = []
        for i, word in enumerate(words):
            if float(word["end"]) > start and float(word["start"]) < end:
                ids.append(word_evidence[i])
        eid = add_evidence("transcript_turn" if moment.get("exact_quote") else "cached_signal",
                           f"task06f-moment:{moment['event_id']}", start, end,
                           exact_text=moment.get("exact_quote"), event_type=moment.get("event_type"),
                           interpretation="Task 06F proposal; not editorially verified")
        ids.append(eid)
        nid = add_node("event", f"task06f:{moment['event_id']}", start=start, end=end,
                       event_type=kind, source_proposal_type=moment.get("event_type"),
                       status="unverified", boundary=moment.get("boundary_recommendation"), evidence_ids=ids)
        event_nodes[f"task06f:{moment['event_id']}"] = {"node_id": nid, "start": start, "end": end, "moment": moment}
        for scene, snode in scene_nodes:
            if _overlap({"start": start, "end": end}, scene) > 0:
                add_edge(nid, snode, "occurs_in", [eid])

    comparison_nodes = []
    for candidate in baseline:
        start, end = _interval(candidate["start"], candidate["end"], duration)
        eid = add_evidence("cached_signal", f"review-candidate:{candidate['candidate_id']}", start, end,
                           source_candidate_id=candidate["candidate_id"],
                           selected_in_task06f=bool(candidate.get("selected")),
                           proposal_sources=candidate.get("sources", []),
                           interpretation="comparison_only_not_editorial_validation")
        nid = add_node("comparison_candidate", candidate["candidate_id"], start=start, end=end,
                       candidate_id=candidate["candidate_id"], selected_in_task06f=bool(candidate.get("selected")),
                       source_status="cached_task06f_candidate", evidence_ids=[eid], editorial_status="NOT_ASSESSED")
        comparison_nodes.append({"node_id": nid, "start": start, "end": end, "evidence_id": eid})

    window_nodes: list[dict] = []
    model_revision = episode_memory.get("model_revision")
    runtime = episode_memory.get("runtime_version")
    for window in sorted(window_records, key=lambda row: (row["start"], row["end"], row["window_id"])):
        start, end = _interval(window["start"], window["end"], duration)
        result = window.get("result", {})
        frame_ids = []
        for timestamp, path in zip(window.get("timestamps", []), window.get("frames", [])):
            timestamp = float(timestamp)
            frame_start, frame_end = max(0.0, timestamp - .0005), min(duration, timestamp + .0005)
            frame_start, frame_end = _interval(frame_start, frame_end, duration)
            frame_ids.append(add_evidence("frame", f"{window['window_id']}:{timestamp:.3f}",
                         frame_start, frame_end, frame_timestamp=timestamp, frame_path=str(path),
                         model_id=model_revision, runtime_version=runtime, validation_status="cached_unverified"))
        model_eid = add_evidence("model_observation", window["window_id"], start, end,
                                 model_id=window.get("model_revision", model_revision),
                                 runtime_version=window.get("runtime_version", runtime),
                                 prompt_version=window.get("prompt_version"), schema_version=window.get("schema_version"),
                                 raw_response=result.get("raw_response"), provider=result.get("provider"),
                                 model_reported_confidence=result.get("confidence"),
                                 validation_status="model_output")
        all_evidence = [*frame_ids, model_eid]
        wid = add_node("visual_observation", window["window_id"], start=start, end=end,
                       description=result.get("description", ""), category_prediction=result.get("category", "unknown"),
                       model_reported_confidence=result.get("confidence"), unverified=True,
                       frame_evidence_ids=frame_ids, model_observation_evidence_id=model_eid,
                       transcript_excerpt=window.get("transcript_excerpt", ""))
        observation_claim = {"claim_id": _id("claim", f"{source_fp}|{window['window_id']}|description"),
            "subject": wid, "predicate": "visual_description", "object": result.get("description", ""),
            "start": start, "end": end, "evidence_ids": all_evidence, "source_fingerprint": source_fp,
            "model_id": window.get("model_revision", model_revision), "runtime_version": runtime,
            "prompt_version": window.get("prompt_version"), "schema_version": window.get("schema_version"),
            "model_reported_confidence": result.get("confidence"), "evidence_completeness": min(1.0, len(frame_ids)/4),
            "evidence_independence": 0, "cross_modal_agreement": "NOT_ASSESSED",
            "contradiction_count": 0, "cross_modal_disagreement_flags": cross_modal_flags(
                window.get("transcript_excerpt", ""), result),
            "status": "unverified", "human_review_status": "UNREVIEWED"}
        claims.append(observation_claim)
        for field, values in (("category", [result.get("category")]), ("possible_action", result.get("actions", [])),
                              ("visual_entity_hypothesis", result.get("entities", []))):
            for index, value in enumerate(values):
                if not isinstance(value, str) or not value.strip():
                    continue
                claim_id = _id("claim", f"{source_fp}|{window['window_id']}|{field}|{index}|{value}")
                subject = wid
                if field == "visual_entity_hypothesis":
                    subject = add_node("entity_hypothesis", f"{window['window_id']}:{index}",
                                       anonymous_id=f"entity_{window['window_id']}_{index+1:02}",
                                       appearance_description=value, candidate_name=None,
                                       identity_status="UNVERIFIED_NO_CROSS_SCENE_MERGE", evidence_ids=all_evidence)
                claims.append({"claim_id": claim_id, "subject": subject, "predicate": field, "object": value,
                    "start": start, "end": end, "evidence_ids": all_evidence, "source_fingerprint": source_fp,
                    "model_id": window.get("model_revision", model_revision), "runtime_version": runtime,
                    "prompt_version": window.get("prompt_version"), "schema_version": window.get("schema_version"),
                    "model_reported_confidence": result.get("confidence"), "evidence_completeness": min(1.0, len(frame_ids)/4),
                    "evidence_independence": 0, "cross_modal_agreement": "NOT_ASSESSED",
                    "contradiction_count": 0, "status": "unverified", "human_review_status": "UNREVIEWED"})
        # Task 06G returns one aggregate action list per window, not frame-local action labels.
        # Repeating that list across frame timestamps would fabricate temporal progression.
        progression = action_progression(result.get("frame_observations", []))
        enode = add_node("event", f"qwen:{window['window_id']}", start=start, end=end,
                         event_type="possible_fight" if progression["type"] == "possible_fight" else "unknown_event",
                         status="unverified", action_progression=progression, visual_observation_id=wid,
                         evidence_ids=all_evidence, editorial_status="NOT_ASSESSED", boundary_status="SOURCE_WINDOW_ONLY")
        add_edge(enode, wid, "part_of", all_evidence)
        for scene, snode in scene_nodes:
            if start < scene["end"] and end > scene["start"]:
                add_edge(enode, snode, "occurs_in", frame_ids[:1] or [model_eid])
        window_nodes.append({"node_id": enode, "observation_id": wid, "start": start, "end": end,
                             "window_id": window["window_id"], "frame_ids": frame_ids, "model_eid": model_eid,
                             "transcript": window.get("transcript_excerpt", ""), "result": result,
                             "progression": progression})

    # Preserve Task 06F dialogue links as unverified hypotheses and their exact transcript evidence.
    for link in brain.get("plot_memory", {}).get("relationships", []):
        left, right = turn_nodes.get(link.get("from_turn_id")), turn_nodes.get(link.get("to_turn_id"))
        if left and right:
            rel = "possible_response_to" if "question" in link.get("type", "") else "before"
            ids = [e["evidence_id"] for e in evidence_by_id.values() if e.get("original_source_reference") in
                   {link.get("from_turn_id"), link.get("to_turn_id")}]
            add_edge(left, right, rel, ids)
    for link in brain.get("plot_memory", {}).get("possible_cross_scene_callbacks", []):
        left, right = turn_nodes.get(link.get("from_turn_id")), turn_nodes.get(link.get("to_turn_id"))
        if left and right:
            ids = [e["evidence_id"] for e in evidence_by_id.values() if e.get("original_source_reference") in
                   {link.get("from_turn_id"), link.get("to_turn_id")}]
            add_edge(left, right, "possible_callback_to", ids)

    for index, left in enumerate(window_nodes[:-1]):
        right = window_nodes[index + 1]
        if right["start"] >= left["start"]:
            add_edge(left["node_id"], right["node_id"], "before", [left["model_eid"], right["model_eid"]])
    for moment in event_nodes.values():
        for window in window_nodes:
            if _overlap(moment, window) >= .1:
                add_edge(moment["node_id"], window["node_id"], "same_sequence_candidate",
                         [window["model_eid"], *window["frame_ids"][:1]])
    for candidate in comparison_nodes:
        for window in window_nodes:
            if _overlap(candidate, window) > 0:
                add_edge(candidate["node_id"], window["node_id"], "same_sequence_candidate",
                         [candidate["evidence_id"], window["model_eid"]])

    transcript_fingerprint = hashlib.sha256(json.dumps(words, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    graph = {"schema": SCHEMA, "schema_version": 1, "source_fingerprint": source_fp,
             "source_duration": duration, "model": {"id": model_revision, "runtime": runtime,
             "inference_used_in_this_build": False}, "cache_identity": {"source_fingerprint": source_fp,
             "model_id": model_revision, "runtime_version": runtime, "schema_version": 1,
             "window_ids": [window["window_id"] for window in sorted(window_records, key=lambda row: row["window_id"])],
             "transcript_fingerprint": transcript_fingerprint,
             "frame_timestamps": sorted({float(t) for row in window_records for t in row.get("timestamps", [])})},
             "evidence": list(evidence_by_id.values()),
             "nodes": list(nodes_by_id.values()), "claims": claims, "edges": edges,
             "summary": {"evidence_count": len(evidence_by_id), "node_count": len(nodes_by_id),
                         "claim_count": len(claims), "edge_count": len(edges),
                         "event_count": sum(n["node_type"] == "event" for n in nodes_by_id.values()),
                         "comparison_candidate_count": len(comparison_nodes),
                         "possible_cross_modal_disagreement_count": sum(bool(c.get("cross_modal_disagreement_flags")) for c in claims),
                         "human_confirmed_claim_count": 0, "human_review_status": "UNREVIEWED",
                         "production_selection_changed": False}}
    return validate_story_graph(graph)


def save_story_graph(path: Path, graph: dict) -> None:
    validate_story_graph(graph)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def make_review_dataset(old: dict, graph: dict, windows: list[dict]) -> dict:
    """Extend the existing protected review set; keep every prior ID and annotation unchanged."""
    validate_dataset(old)
    if old["source"]["fingerprint"] != graph["source_fingerprint"]:
        raise ValueError("Task 06F review data belongs to a different source fingerprint")
    data = json.loads(json.dumps(old))
    known = {row["candidate_id"] for row in data["candidates"]}
    evidence_by_id = {row["evidence_id"]: row for row in graph["evidence"]}
    nodes = {row["node_id"]: row for row in graph["nodes"]}
    claims_by_subject: dict[str, list[dict]] = {}
    for claim in graph["claims"]:
        claims_by_subject.setdefault(claim["subject"], []).append(claim)
    model_events = [n for n in graph["nodes"] if n["node_type"] == "event" and n.get("visual_observation_id")]
    for event in model_events:
        window_id = nodes[event["visual_observation_id"]]["identity"]
        window = next((w for w in windows if w["window_id"] == window_id), None)
        if window is None:
            continue
        cid = f"vlm_{window['window_id']}"
        if cid in known:
            continue
        observation = nodes[event["visual_observation_id"]]
        related_claims = claims_by_subject.get(event["visual_observation_id"], [])
        raw_confidence = observation.get("model_reported_confidence")
        frame_ids = observation.get("frame_evidence_ids", [])
        quote = window.get("transcript_excerpt", "")
        frames = [Path(evidence_by_id[eid]["frame_path"]).name for eid in frame_ids]
        frame_refs = [f"../candidate_review_06f/frames/{name}" for name in frames]
        data["candidates"].append({"candidate_id": cid, "start": event["start"], "end": event["end"],
            "duration": round(event["end"]-event["start"], 3), "predicted_category": "Unclassified",
            "score": min(1.0, len(frame_ids)/4), "score_label": "evidence availability (not editorial quality)",
            "selected": False, "baseline": False, "reviewer_label": None, "boundary_valid": True,
            "sources": ["cached_qwen_vlm", "cached_whisper_if_present"],
            "reason": "Chronological Qwen observation; all visual claims remain unverified.",
            "transcript_excerpt": quote, "evidence": {"frame_timestamps": window["timestamps"],
                "claim_ids": [claim["claim_id"] for claim in related_claims],
                "model_reported_confidence": raw_confidence, "confidence_is_not_calibrated": True,
                "action_progression": event["action_progression"]},
            "event_summary": json.dumps({"model_description": observation.get("description"),
                "claim_status": "unverified", "category_prediction": observation.get("category_prediction"),
                "boundary": {"start": event["start"], "end": event["end"],
                    "status": "SOURCE_WINDOW_ONLY", "fixed_duration": False},
                "editorial_status": "NOT_ASSESSED"}, ensure_ascii=False, indent=2),
            "review_cluster_id": cid, "review_cluster_size": 1, "alternate_candidates": [],
            "frames": frame_refs, "frame_timestamps": window["timestamps"], "preview_audio_available": False})
        known.add(cid)
    data["candidate_count"] = len(data["candidates"])
    data["annotation_policy"] = "Human decisions only. Graph claims are unverified hypotheses; no annotations are prefilled. Existing Task 06F annotations are preserved verbatim."
    data["review_sampling"] = {**data.get("review_sampling", {}),
        "displayed_cluster_count": len(data["candidates"]), "task06g_windows_added": len(model_events)}
    data["graph_path"] = "story_graph.json"
    validate_dataset(data)
    return data
