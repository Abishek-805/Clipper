"""Offline, evidence-first narrative event proposals; not a production selector."""
from __future__ import annotations

import math
import re


_TERMINAL = re.compile(r"[.!?…][\"'”’)]*$")
_QUESTION = re.compile(r"\?$|^(who|what|when|where|why|how|is|are|do|does|did|can|could|would|will|should)\b", re.I)
_REVEAL = re.compile(r"\b(the only way|only one way|you must|must clear|here's the truth|the truth is|it turns out|revealed|in fact)\b", re.I)
_REACTION = re.compile(r"\b(what\??|why\??|really\??|no way|i don't believe|how can|are you serious)\b", re.I)
_CLOSING = re.compile(r"\b(thanks for watching|the end|to be continued|next episode|credits|good night)\b", re.I)


def _word_text(word: dict) -> str:
    return str(word.get("text", word.get("word", ""))).strip()


def _merge_speech(words: list[dict], start: float, end: float) -> list[list[float]]:
    intervals = []
    for word in words:
        left, right = max(start, float(word["start"])), min(end, float(word["end"]))
        if right <= left:
            continue
        if intervals and left <= intervals[-1][1] + .15:
            intervals[-1][1] = max(intervals[-1][1], right)
        else:
            intervals.append([left, right])
    return intervals


def _utterances(words: list[dict]) -> list[dict]:
    rows = sorted((w for w in words if isinstance(w, dict) and
                   isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float)) and
                   w["end"] > w["start"] and _word_text(w)), key=lambda w: w["start"])
    groups, current = [], []
    for word in rows:
        if current and (word["start"] - current[-1]["end"] > 1.25 or
                        _TERMINAL.search(_word_text(current[-1])) or word["start"] - current[0]["start"] > 12):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    output = []
    for i, group in enumerate(groups, 1):
        text = " ".join(_word_text(w) for w in group)
        cues = []
        if _QUESTION.search(text):
            cues.append("question")
        if _REVEAL.search(text):
            cues.append("explicit_reveal_phrase")
        if _REACTION.search(text):
            cues.append("possible_reaction_phrase")
        tokens = re.findall(r"[a-z']+", text.casefold())
        lyric_like = len(tokens) >= 6 and len(set(tokens)) <= max(2, len(tokens) // 3)
        output.append({"beat_id": f"beat_{i:04}", "start": float(group[0]["start"]),
                       "end": float(group[-1]["end"]), "text": text,
                       "evidence_type": "asr_word_timestamps", "confidence": "transcript_only",
                       "cues": cues or ["statement_or_speech"],
                       "sentence_terminal": bool(_TERMINAL.search(_word_text(group[-1]))),
                       "possible_lyric_like_repetition": lyric_like})
    return output


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))] if ordered else math.inf


def _signals(start: float, end: float, motion: list[float], audio: list[float]) -> dict:
    left, right = max(0, int(start)), int(math.ceil(end))
    ms, au = motion[left:min(len(motion), right)], audio[left:min(len(audio), right)]
    return {"motion_mean": round(sum(ms) / len(ms), 4) if ms else None,
            "motion_peak": round(max(ms), 4) if ms else None,
            "audio_energy_mean": round(sum(au) / len(au), 4) if au else None,
            "scene_changes": None}


def _candidate_flags(start: float, end: float, duration: float, episode_duration: float, words: list[dict],
                     intervals: list[list[float]], motion_values: list[float], audio_values: list[float],
                     utterances: list[dict]) -> tuple[list[str], dict]:
    spans = []
    cursor = start
    for left, right in intervals:
        spans.append((left - cursor, cursor, left))
        cursor = right
    spans.append((end - cursor, cursor, end))
    longest = max(spans, default=(0.0, start, end))
    spoken = sum(right - left for left, right in intervals)
    count = sum(1 for w in words if start <= float(w["start"]) < end)
    flags = []
    if longest[0] >= max(8.0, duration * .3):
        flags.append("long_unexplained_speech_gap")
    if len(intervals) > 1 and longest[0] >= 6.5:
        flags.append("multiple_speech_islands_separated_by_long_gap")
    if spoken / max(duration, .001) < .2 or count < 8:
        flags.append("sparse_transcript_evidence_review_non_speech_before_rejecting")
    if start < 90:
        flags.append("near_episode_opening_review_title_or_recap_possible")
    if end >= episode_duration * .9:
        flags.append("near_episode_end_review_credits_possible")
    local_text = " ".join(_word_text(w) for w in words if start <= float(w["start"]) < end)
    if _CLOSING.search(local_text):
        flags.append("closing_language_detected_asr_only")
    if any(u.get("possible_lyric_like_repetition") for u in utterances if start <= u["start"] < end):
        flags.append("possible_lyrics_or_repetitive_asr_requires_review")
    if local_motion := motion_values[max(0, int(start)):min(len(motion_values), int(math.ceil(end)))]:
        if max(local_motion) <= _percentile(motion_values, .25):
            flags.append("low_visual_motion_signal")
    local_audio = audio_values[max(0, int(start)):min(len(audio_values), int(math.ceil(end)))]
    if local_audio and max(local_audio) <= _percentile(audio_values, .25):
        flags.append("low_audio_energy_signal")
    included = [u for u in utterances if start <= u["start"] < end]
    if included and not included[-1]["sentence_terminal"]:
        flags.append("final_asr_utterance_has_no_terminal_punctuation")
    if not included and ("low_visual_motion_signal" in flags or "low_audio_energy_signal" in flags):
        flags.append("no_speech_and_no_strong_activity_progression")
    return flags, {"word_count": count, "merged_speech_seconds": round(spoken, 3),
                   "speech_coverage": round(spoken / max(duration, .001), 4),
                   "largest_uncovered_gap_seconds": round(longest[0], 3),
                   "largest_gap_interval": [round(longest[1], 3), round(longest[2], 3)]}


def build_event_proposals(analysis: dict, words: list[dict], cache: dict) -> dict:
    """Build timestamp-grounded structural proposals without claiming semantic understanding."""
    duration = float(analysis.get("source_duration") or analysis.get("metadata", {}).get("duration", 0))
    if duration <= 0:
        raise ValueError("A positive source duration is required")
    motion = [float(x) for x in cache.get("motion", [])]
    audio = [float(x) for x in cache.get("audio_energy", [])]
    size = min(max(len(motion), len(audio), math.ceil(duration)), math.ceil(duration))
    motion += [0.0] * max(0, size - len(motion))
    audio += [0.0] * max(0, size - len(audio))
    motion_scale = _percentile(motion, .9)
    audio_scale = _percentile(audio, .9)
    scene_cuts = sorted(float(t) for t in analysis.get("scene_cuts", cache.get("scene_cuts", []))
                        if isinstance(t, (int, float)) and 0 <= t < duration)
    utterances = _utterances(words)
    events = []
    groups, current = [], []
    for beat in utterances:
        if current and (beat["start"] - current[-1]["end"] > 5.5 or beat["end"] - current[0]["start"] > 60):
            groups.append(current)
            current = []
        current.append(beat)
    if current:
        groups.append(current)
    for group in groups:
        start, end = group[0]["start"], group[-1]["end"]
        if end <= start:
            continue
        event_duration = end - start
        relevant_words = [w for w in words if start <= float(w.get("start", -1)) < end]
        intervals = _merge_speech(relevant_words, start, end)
        flags, coverage = _candidate_flags(start, end, event_duration, duration, relevant_words, intervals,
                                           motion, audio, group)
        cues = [cue for beat in group for cue in beat["cues"]]
        if "explicit_reveal_phrase" in cues and any("possible_reaction_phrase" in b["cues"] or "question" in b["cues"] for b in group[1:]):
            structure = "reveal_phrase_then_possible_reaction"
        elif "question" in cues and len(group) > 1:
            structure = "question_then_following_speech_turn"
        else:
            structure = "connected_speech_turns" if len(group) > 1 else "single_speech_turn"
        if event_duration < 12:
            flags.append("short_event_keep_natural_duration_do_not_pad")
        if not group[-1]["sentence_terminal"]:
            flags.append("incomplete_last_asr_sentence_review_boundary")
        signals = _signals(start, end, motion, audio)
        signals["scene_changes"] = sum(start <= cut <= end for cut in scene_cuts)
        signal_score = round(min(1.0, .35 * min(1, coverage["word_count"] / 30) +
                                 .3 * min(1, (signals["motion_mean"] or 0) / max(motion_scale, .001)) +
                                 .2 * min(1, (signals["audio_energy_mean"] or 0) / max(audio_scale, .001)) +
                                 .15 * min(1, len(set(cues)) / 3)), 4)
        events.append({"event_id": f"event_{len(events)+1:04}", "start": round(start, 3),
                       "end": round(end, 3), "duration": round(event_duration, 3),
                       "category": "unknown", "structure": structure,
                       "beats": [{**b, "start": round(b["start"], 3), "end": round(b["end"], 3)} for b in group],
                       "hook_evidence": group[0]["text"],
                       "context_evidence": " ".join(b["text"] for b in group[:2]),
                       "escalation_evidence": next((b["text"] for b in group if "explicit_reveal_phrase" in b["cues"]), None),
                       "payoff_evidence": next((b["text"] for b in reversed(group[1:]) if "possible_reaction_phrase" in b["cues"] or "question" in b["cues"]), None),
                       "transcript_excerpt": " ".join(b["text"] for b in group),
                       "evidence_timestamps": [[round(b["start"], 3), round(b["end"], 3)] for b in group],
                       "signal_evidence": {**signals, **coverage},
                       "signal_score": signal_score,
                       "semantic_interpretation": "unavailable_no_vlm",
                       "editorial_assessment": {"status": "NOT_ASSESSED", "score": None,
                                                "reason": "No semantic provider ran; signal score is not editorial quality."},
                       "negative_flags": sorted(set(flags)),
                       "recommended_clip_boundaries": [round(start, 3), round(end, 3)]})

    # Speech-free motion/audio intervals are review proposals only; activity is not labeled as action.
    threshold = _percentile(motion, .85)
    active = [i for i, value in enumerate(motion) if value > 0 and value >= threshold]
    spans = []
    for second in active:
        if not spans or second - spans[-1][-1] > 2:
            spans.append([second])
        else:
            spans[-1].append(second)
    for span in spans:
        start, end = float(span[0]), min(duration, float(span[-1] + 1))
        if end - start < 8 or any(max(start, e["start"]) < min(end, e["end"]) and
                                  (min(end, e["end"]) - max(start, e["start"])) / (end-start) > .25 for e in events):
            continue
        relevant_words = [w for w in words if start <= float(w.get("start", -1)) < end]
        intervals = _merge_speech(relevant_words, start, end)
        flags, coverage = _candidate_flags(start, end, end-start, duration, relevant_words, intervals, motion, audio, [])
        if not relevant_words and _signals(start, end, motion, audio)["audio_energy_mean"]:
            flags.append("possible_music_or_non_speech_audio_meaning_unverified")
        signals = _signals(start, end, motion, audio)
        signals["scene_changes"] = sum(start <= cut <= end for cut in scene_cuts)
        events.append({"event_id": f"event_{len(events)+1:04}", "start": round(start, 3),
                       "end": round(end, 3), "duration": round(end-start, 3), "category": "unknown",
                       "structure": "unclassified_visual_activity_span",
                       "beats": [{"beat_id": f"beat_{len(events)+1:04}", "start": round(start, 3), "end": round(end, 3),
                                  "text": "Motion signal span; its meaning is unverified.", "evidence_type": "cached_motion_signal",
                                  "confidence": "activity_only", "cues": ["visual_activity"]}],
                       "hook_evidence": None, "context_evidence": None, "escalation_evidence": None,
                       "payoff_evidence": None, "transcript_excerpt": "",
                       "evidence_timestamps": [[round(start, 3), round(end, 3)]],
                       "signal_evidence": {**signals, **coverage},
                       "signal_score": round(.55 * min(1, (signals["motion_mean"] or 0) / max(motion_scale, .001)) +
                                              .45 * min(1, (signals["audio_energy_mean"] or 0) / max(audio_scale, .001)), 4),
                       "semantic_interpretation": "unavailable_no_vlm",
                       "editorial_assessment": {"status": "NOT_ASSESSED", "score": None,
                                                "reason": "Motion is not proof of meaningful action."},
                       "negative_flags": sorted(set(flags + ["activity_only_meaning_unverified"])),
                       "recommended_clip_boundaries": [round(start, 3), round(end, 3)]})
    events.sort(key=lambda event: (event["start"], event["end"], event["event_id"]))
    for i, event in enumerate(events, 1):
        event["event_id"] = f"event_{i:04}"
        for beat_index, beat in enumerate(event["beats"], 1):
            if beat.get("evidence_type") == "cached_motion_signal":
                beat["beat_id"] = f"beat_{i:04}_{beat_index:02}"
    timeline = []
    cut_set = {int(t) for t in scene_cuts}
    for second in range(math.ceil(duration)):
        active_words = sum(1 for word in words if second <= float(word.get("start", -1)) < second + 1)
        timeline.append({"start": second, "end": min(duration, second+1),
                         "scene_boundary": second in cut_set,
                         "motion": round(motion[second], 4) if second < len(motion) else None,
                         "audio_energy": round(audio[second], 4) if second < len(audio) else None,
                         "word_count": active_words})
    events.sort(key=lambda event: (-event["signal_score"], event["start"], event["event_id"]))
    for rank, event in enumerate(events, 1):
        event["discovery_rank"] = rank
    return {"schema": "clipper-event-first-v1", "source": analysis.get("source"),
            "source_duration": duration, "provenance": "cached signals and ASR timestamps; no semantic model ran",
            "timeline": timeline, "beats": utterances, "events": events,
            "event_count": len(events), "editorial_assessment_count": 0}


def assess_candidate_interval(start: float, end: float, episode_duration: float,
                              words: list[dict], cache: dict) -> dict:
    """Add explainable coverage warnings to an existing selected interval."""
    start, end = max(0.0, float(start)), min(float(episode_duration), float(end))
    if end <= start:
        raise ValueError("Candidate interval must have positive duration")
    motion = [float(x) for x in cache.get("motion", [])]
    audio = [float(x) for x in cache.get("audio_energy", [])]
    utterances = _utterances(words)
    relevant = [w for w in words if start <= float(w.get("start", -1)) < end]
    intervals = _merge_speech(relevant, start, end)
    flags, coverage = _candidate_flags(start, end, end-start, episode_duration, relevant, intervals,
                                       motion, audio, utterances)
    prior = [w for w in words if 0 <= start-float(w.get("end", start)) <= 1.25]
    following = [w for w in words if 0 <= float(w.get("start", end))-end <= 1.25]
    first = min(relevant, key=lambda w: w["start"]) if relevant else None
    last = max(relevant, key=lambda w: w["end"]) if relevant else None
    if prior and first and not _TERMINAL.search(_word_text(max(prior, key=lambda w: w["end"]))):
        flags.append("candidate_starts_inside_transcript_sentence")
    if following and last and not _TERMINAL.search(_word_text(last)):
        flags.append("candidate_ends_inside_transcript_sentence")
    return {"start": round(start, 3), "end": round(end, 3), "duration": round(end-start, 3),
            "category": "unknown", "speech_evidence": coverage,
            "signal_evidence": _signals(start, end, motion, audio),
            "warnings": sorted(set(flags)),
            "editorial_assessment": {"status": "NOT_ASSESSED", "score": None,
                                     "reason": "Signal scores and transcript coverage do not assess editorial quality."}}
