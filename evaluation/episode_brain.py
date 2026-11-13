"""Evidence-grounded whole-episode memory and moment proposals.

This module organizes cached signals and transcript evidence; it does not claim
semantic scene understanding when no visual model has run.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from evaluation.event_engine import _signals, _utterances, build_event_proposals


_STOPWORDS = set("a an and are as at be been but by can did do for from had has have he her him his i if in into is it its me my no not of on or our she so that the their them then there they this to was we were what when where which who why will with you your".split())
_TOKEN = re.compile(r"[a-z][a-z']{2,}", re.I)


def _text(word: dict) -> str:
    return str(word.get("text", word.get("word", ""))).strip()


def _seconds(values: list[float], start: float, end: float) -> list[float]:
    return values[max(0, int(start)):min(len(values), math.ceil(end))]


def _coverage(words: list[dict], start: float, end: float) -> dict:
    spans = []
    for word in words:
        left, right = max(start, float(word["start"])), min(end, float(word["end"]))
        if right <= left:
            continue
        if spans and left <= spans[-1][1] + .15:
            spans[-1][1] = max(spans[-1][1], right)
        else:
            spans.append([left, right])
    spoken = sum(right-left for left, right in spans)
    gaps, cursor = [], start
    for left, right in spans:
        gaps.append((left-cursor, cursor, left))
        cursor = right
    gaps.append((end-cursor, cursor, end))
    gap = max(gaps, default=(end-start, start, end))
    return {"word_count": sum(start <= float(w["start"]) < end for w in words),
            "speech_seconds": round(spoken, 3),
            "speech_coverage": round(spoken/max(.001, end-start), 4),
            "largest_uncovered_gap_seconds": round(gap[0], 3),
            "largest_gap_interval": [round(gap[1], 3), round(gap[2], 3)]}


def _connected_groups(turns: list[dict], max_pause: float = 5.5) -> list[list[dict]]:
    groups, current = [], []
    for turn in turns:
        if current and turn["start"] - current[-1]["end"] > max_pause:
            groups.append(current)
            current = []
        current.append(turn)
    if current:
        groups.append(current)
    return groups


def _scene_for(timestamp: float, cuts: list[float]) -> int:
    low, high = 0, len(cuts)
    while low < high:
        mid = (low + high) // 2
        if cuts[mid] <= timestamp:
            low = mid + 1
        else:
            high = mid
    return low + 1


def _subtitle_evidence(tracks: list[dict], start: float, end: float) -> list[dict]:
    return [{"stream_index": track["stream_index"], "declared_language": track.get("language"),
             "cue_start": cue["start"], "cue_end": cue["end"], "text": cue["text"]}
            for track in tracks for cue in track.get("cues", [])
            if cue["start"] < end and cue["end"] > start]


def _cross_scene_links(turns: list[dict], scene_cuts: list[float]) -> list[dict]:
    links = []
    for i, current in enumerate(turns):
        terms = {t.casefold() for t in _TOKEN.findall(current["text"])} - _STOPWORDS
        if not terms:
            continue
        found = 0
        for earlier in reversed(turns[:i]):
            if _scene_for(current["start"], scene_cuts) == _scene_for(earlier["start"], scene_cuts):
                continue
            shared = sorted(terms & ({t.casefold() for t in _TOKEN.findall(earlier["text"])} - _STOPWORDS))
            if len(shared) < 2:
                continue
            links.append({"from_turn_id": earlier["beat_id"], "to_turn_id": current["beat_id"],
                          "from_timestamp": earlier["start"], "to_timestamp": current["start"],
                          "shared_terms": shared, "relationship": "possible_cross_scene_lexical_callback",
                          "confidence": "lexical_overlap_only",
                          "interpretation": "Possible reference; no character or plot relationship inferred."})
            found += 1
            if found == 2:
                break
    return links


def _dialogue_moments(groups: list[list[dict]], words: list[dict], duration: float,
                      motion: list[float], audio: list[float], scene_cuts: list[float],
                      subtitle_tracks: list[dict]) -> list[dict]:
    moments = []
    for number, group in enumerate(groups, 1):
        start, end = float(group[0]["start"]), float(group[-1]["end"])
        source_words = [w for w in words if start <= float(w["start"]) < end]
        quotes = " ".join(_text(w) for w in source_words).strip()
        coverage = _coverage(source_words, start, end)
        signals = _signals(start, end, motion, audio)
        signals["scene_changes"] = sum(start <= t <= end for t in scene_cuts)
        turns = [{**turn, "speaker": "unknown"} for turn in group]
        cue_types = sorted({cue for turn in group for cue in turn["cues"]})
        score = round(min(1.0, .45*min(1, coverage["word_count"]/30) +
                          .25*min(1, (signals["motion_mean"] or 0)/.9) +
                          .2*min(1, (signals["audio_energy_mean"] or 0)/.9) +
                          .1*min(1, len(cue_types)/2)), 4)
        moments.append({"event_id": f"dialogue_{number:04}", "event_type": "unknown_dialogue_sequence",
                        "start": round(start, 3), "end": round(end, 3), "duration": round(end-start, 3),
                        "category": "unknown", "scene_ids": sorted({f"scene_{_scene_for(t['start'], scene_cuts):04}" for t in group}),
                        "exact_quote": quotes, "word_timestamps": [[round(float(w["start"]), 3), round(float(w["end"]), 3)] for w in source_words],
                        "turns": turns, "context_before_turn_id": None, "context_after_turn_id": None,
                        "context_requirement": "unknown_requires_human_or_semantic_review",
                        "subtitle_evidence": _subtitle_evidence(subtitle_tracks, start, end),
                        "structural_cues": cue_types, "transcript_evidence": coverage,
                        "signal_evidence": signals, "signal_evidence_score": score,
                        "semantic_confidence": None, "narrative_importance": None,
                        "editorial_assessment": {"status": "NOT_ASSESSED", "score": None},
                        "boundary_recommendation": {"start": round(start, 3), "end": round(end, 3),
                          "reason": "Complete connected timestamped utterances; exact first/last ASR word bounds; no fixed duration cap.",
                          "confidence": "transcript_boundary_only"},
                        "provenance": "cached_whisper_word_timestamps_and_signal_cache"})
    # Context is adjacent transcript evidence, kept separate from the verbatim candidate quote.
    flattened = [turn for group in groups for turn in group]
    for moment in moments:
        first_i = next((i for i, turn in enumerate(flattened) if turn["beat_id"] == moment["turns"][0]["beat_id"]), None)
        if first_i is not None and first_i > 0:
            turn = flattened[first_i-1]
            moment["context_before_turn_id"] = turn["beat_id"]
            moment["context_before"] = {"start": turn["start"], "end": turn["end"], "text": turn["text"]}
        last_i = first_i + len(moment["turns"])-1 if first_i is not None else None
        if last_i is not None and last_i + 1 < len(flattened):
            turn = flattened[last_i+1]
            moment["context_after_turn_id"] = turn["beat_id"]
            moment["context_after"] = {"start": turn["start"], "end": turn["end"], "text": turn["text"]}
    return moments


def build_episode_brain(analysis: dict, transcript: dict, cache: dict,
                        subtitle_tracks: list[dict] | None = None,
                        coarse_frame_timestamps: list[float] | None = None) -> dict:
    duration = float(analysis.get("source_duration") or analysis.get("metadata", {}).get("duration", 0))
    if duration <= 0:
        raise ValueError("A positive episode duration is required")
    words = sorted((w for w in transcript.get("words", []) if isinstance(w, dict) and
                    isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float)) and
                    w["end"] > w["start"] and _text(w) and 0 <= w["start"] < duration), key=lambda w: w["start"])
    motion = [float(v) for v in cache.get("motion", [])]
    audio = [float(v) for v in cache.get("audio_energy", [])]
    timeline = build_event_proposals(analysis, words, cache)
    cuts = sorted(float(t) for t in analysis.get("scene_cuts", cache.get("scene_cuts", []))
                  if isinstance(t, (int, float)) and 0 < t < duration)
    turns = _utterances(words)
    groups = _connected_groups(turns)
    subtitle_tracks = subtitle_tracks or []
    dialogue = _dialogue_moments(groups, words, duration, motion, audio, cuts, subtitle_tracks)
    activity = []
    for event in timeline["events"]:
        if event["structure"] != "unclassified_visual_activity_span":
            continue
        activity.append({**event, "event_type": "unknown_visual_activity",
                         "narrative_importance": None, "semantic_confidence": None,
                         "editorial_assessment": {"status": "NOT_ASSESSED", "score": None},
                         "boundary_recommendation": {"start": event["start"], "end": event["end"],
                           "reason": "Contiguous cached-motion evidence window; its semantic event boundary is unverified.",
                           "confidence": "signal_interval_only"}})
    moments = sorted(dialogue + activity, key=lambda e: (e["start"], e["end"], e["event_id"]))
    for i, moment in enumerate(moments, 1):
        moment["event_id"] = f"moment_{i:04}"
    # All scene intervals partition the episode; empty sections remain represented.
    scene_edges = [0.0, *cuts, duration]
    scene_memory = []
    for i, (start, end) in enumerate(zip(scene_edges, scene_edges[1:]), 1):
        scene_turns = [t for t in turns if start <= t["start"] < end]
        ms, au = _seconds(motion, start, end), _seconds(audio, start, end)
        scene_memory.append({"scene_id": f"scene_{i:04}", "start": round(start, 3), "end": round(end, 3),
          "duration": round(end-start, 3), "visual_description": None, "observable_events": [],
          "visible_entities": [], "visual_status": "UNAVAILABLE_NO_VLM",
          "evidence_frame_timestamps": [t for t in (coarse_frame_timestamps or []) if start <= t < end],
          "turn_ids": [t["beat_id"] for t in scene_turns],
          "transcript_excerpt": " ".join(t["text"] for t in scene_turns),
          "audio_evidence": {"cached_energy_mean": round(sum(au)/len(au), 4) if au else None,
                             "status": "cached_rms_proxy_only" if au else "unavailable"},
          "motion_evidence": {"mean": round(sum(ms)/len(ms), 4) if ms else None,
                               "peak": round(max(ms), 4) if ms else None,
                               "status": "cached_motion_proxy_only" if ms else "unavailable"},
          "confidence": None, "unknown_fields": ["visual description", "actions", "character identity", "emotion"]})
    phrase_index = [{"phrase_id": turn["beat_id"], "exact_text": turn["text"],
                     "start": round(turn["start"], 3), "end": round(turn["end"], 3),
                     "scene_id": f"scene_{_scene_for(turn['start'], cuts):04}",
                     "subtitle_evidence": _subtitle_evidence(subtitle_tracks, turn["start"], turn["end"]),
                     "structural_cues": turn["cues"], "importance": None,
                     "speaker": "unknown", "provenance": "cached_whisper_word_timestamps"}
                    for turn in turns]
    relationships = []
    for i, turn in enumerate(turns[:-1]):
        if "question" in turn["cues"] and 0 <= turns[i+1]["start"]-turn["end"] <= 8:
            relationships.append({"from_turn_id": turn["beat_id"], "to_turn_id": turns[i+1]["beat_id"],
              "type": "possible_question_followed_by_speech", "confidence": "sequence_only",
              "interpretation": "Not verified as an answer or consequence."})
    callbacks = _cross_scene_links(turns, cuts)
    event_memory = [{"event_id": e["event_id"], "start": e["start"], "end": e["end"],
                     "type": e["event_type"], "scene_ids": e.get("scene_ids", []),
                     "evidence": e.get("exact_quote") or e.get("transcript_excerpt") or e.get("signal_evidence"),
                     "semantic_status": "UNAVAILABLE_NO_VLM"} for e in moments]
    ranked = sorted(moments, key=lambda e: (-float(e.get("signal_evidence_score", e.get("signal_score", 0))), e["start"]))
    for i, event in enumerate(ranked, 1):
        event["signal_evidence_rank"] = i
        event["global_editorial_rank"] = None
    return {"schema": "clipper-episode-brain-v1", "source": analysis.get("source"),
      "source_duration": duration, "analysis_provenance": "cached Task 06C metadata/transcript/signals; no semantic model ran",
      "episode_overview": {"summary": None, "status": "NOT_SEMANTICALLY_SUMMARIZED",
        "factual_counts": {"scene_intervals": len(scene_memory), "scene_changes": len(cuts),
                           "whisper_words": len(words), "speech_turns": len(turns), "candidate_moments": len(moments)},
        "transcription": {"language": transcript.get("language"), "device": transcript.get("device"),
                          "precision": transcript.get("precision"), "outcome": transcript.get("outcome"),
                          "speaker_diarization": "unavailable; speakers are unknown"}},
      "timeline": {"duration": duration, "coverage_complete": True,
        "coverage_basis": "scene intervals partition [0, duration], plus cached 1-second signal bins",
        "scene_intervals": scene_memory, "signal_bins": timeline["timeline"],
        "coarse_frame_interval_seconds": 3, "coarse_frame_timestamps": coarse_frame_timestamps or []},
      "scene_memory": scene_memory,
      "entity_memory": {"entities": [], "status": "UNAVAILABLE_NO_VLM_OR_DIARIZATION",
                        "note": "No visual identities or speaker identities were inferred."},
      "plot_memory": {"chronological_summary": None, "status": "NOT_SEMANTICALLY_SUMMARIZED",
        "events": event_memory, "relationships": relationships, "possible_cross_scene_callbacks": callbacks,
        "plot_threads": [{"thread_id": f"thread_{t['beat_id']}", "evidence_turn_id": t["beat_id"],
                          "cue": t["cues"], "status": "requires_semantic_or_human_interpretation"}
                         for t in turns if "explicit_reveal_phrase" in t["cues"] or "question" in t["cues"]]},
      "transcript": {"language": transcript.get("language"), "word_count": len(words),
        "words": [{"text": _text(w), "start": float(w["start"]), "end": float(w["end"]),
                   "provenance": "cached_whisper_word_timestamps"} for w in words],
        "turns": turns, "subtitle_tracks": subtitle_tracks or []},
      "important_phrase_index": phrase_index,
      "visual_event_timeline": [{"event_id": e["event_id"], "start": e["start"], "end": e["end"],
        "category": "unknown", "evidence": e["signal_evidence"], "interpretation": "activity only; no visual semantics available"}
        for e in activity],
      "moments": moments,
      "ranking": {"ranked_by": "signal evidence only; not editorial quality", "moments": [e["event_id"] for e in ranked],
        "quality_qualified_count": 0, "editorial_assessment": "NOT_RUN_NO_SEMANTIC_PROVIDER",
        "forced_clip_quota": False, "fixed_duration_cap": False},
      "baseline_comparison": {"production_selection_count": len(analysis.get("clips", [])),
        "production_algorithm_changed": False,
        "intervals": [{"candidate_id": c.get("candidate_id", f"baseline_{i:03}"),
          "start": c["start"], "end": c["end"], "score": c.get("score"),
          "nearest_discovery": max(moments, key=lambda e: _overlap(e, c), default=None)["event_id"] if moments else None,
          "editorial_status": "NOT_ASSESSED"} for i, c in enumerate(analysis.get("clips", []), 1)]}}


def _overlap(moment: dict, clip: dict) -> float:
    overlap = max(0.0, min(moment["end"], clip["end"])-max(moment["start"], clip["start"]))
    union = max(moment["end"], clip["end"])-min(moment["start"], clip["start"])
    return overlap/union if union else 0.0
