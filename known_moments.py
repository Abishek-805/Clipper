"""Owner-reviewed reference intervals. Text matches are proposals, never human labels."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import threading

from structure import prepare_structure, write_json
from dialogue import choose_dialogue


def search_terms(description):
    text = description.casefold()
    terms = set(re.findall(r"[a-z]{4,}", text)) - {"time", "where", "with", "have", "little", "scene", "scence", "hero", "friend", "convo", "about"}
    core = set(terms)
    if "training" in text or "train" in terms:
        terms |= {"skill", "move", "activate", "sword", "attack", "tips", "technique", "fight", "practice", "motion"}
        core = {"skill", "move", "activate", "motion", "tips", "practice"}
    if "game" in text and ("master" in text or "explain" in text):
        terms |= {"logout", "nerve", "gear", "players", "world", "clear", "game", "control"}
        core = {"logout", "nerve", "players", "clear", "control"}
    return terms, core


def propose(template, cues, duration, source_hash):
    if template.get("source_hash") != source_hash:
        return []
    output = []
    for description in template.get("pending_owner_descriptions", []):
        label = description["label"]
        terms, core = search_terms(description.get("owner_description", label))
        pool = []
        for cue in cues:
            if not any(term in cue["text"].casefold() for term in core):
                continue
            start = max(0., cue["start"]-3)
            end = min(duration, start+45)
            window = [c for c in cues if c["start"] < end and c["end"] > start]
            if not window:
                continue
            start, end = min(c["start"] for c in window), max(c["end"] for c in window)
            text = " ".join(c["text"] for c in window).casefold()
            matched = sorted(t for t in terms if re.search(r"\b"+re.escape(t)+r"\w*\b", text))
            if len(matched) < 2 or not set(matched) & core:
                continue
            score = len(matched) + 2*len(set(matched) & core)
            quotes = sorted(window, key=lambda c: -sum(t in c["text"].casefold() for t in matched))[:3]
            identity = hashlib.sha256(json.dumps([source_hash, label, start, end]).encode()).hexdigest()[:20]
            pool.append(dict(id="ref-"+identity, source_hash=source_hash, label=label,
                             start=start, end=end, status="PROPOSED", score=score,
                             matched_terms=matched, quotes=quotes,
                             reason="Cached dialogue match using explicit query expansion: " + ", ".join(matched)))
        selected = []
        for candidate in sorted(pool, key=lambda c: (-c["score"], c["start"])):
            if any(max(0, min(c["end"], candidate["end"])-max(c["start"], candidate["start"])) /
                   max(.001, max(c["end"], candidate["end"])-min(c["start"], candidate["start"])) > .25 for c in selected):
                continue
            selected.append(candidate)
            if len(selected) == 3:
                break
        output.extend(sorted(selected, key=lambda c: c["start"]))
    return output


def load_decisions(path, source_hash):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("decisions", {}) if data.get("source_hash") == source_hash else {}


def save_decision(path, source_hash, proposals, identity, action, start, end, duration):
    if action not in ("confirm", "reject", "edit"):
        raise ValueError("Use confirm, reject or edit")
    proposal = next((p for p in proposals if p["id"] == identity and p["source_hash"] == source_hash), None)
    if proposal is None:
        raise ValueError("Unknown proposal/source")
    start, end = float(start), float(end)
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
        raise ValueError("Reference bounds must be finite, ordered and inside this source")
    records = load_decisions(path, source_hash)
    records[identity] = dict(id=identity, source_hash=source_hash, label=proposal["label"],
        rough_start=proposal["start"], rough_end=proposal["end"], start=start, end=end,
        status={"confirm": "CONFIRMED", "reject": "REJECTED", "edit": "PROPOSED"}[action],
        reviewed_at=datetime.now(timezone.utc).isoformat())
    write_json(path, dict(schema="clipper-known-moments-v1", source_hash=source_hash, decisions=records,
                         moments=[r for r in records.values() if r["status"] == "CONFIRMED"]))
    return records


def load_review(source, cache_folder, output, audio_index, templates):
    import clipper
    info = clipper.probe(source)
    if audio_index not in {s["index"] for s in info.streams or [] if s["type"] == "audio"}:
        raise ValueError("Select an available source audio track")
    structure = prepare_structure(source, cache_folder)
    source_hash = structure["source_hash"]
    template = None
    for path in sorted(templates.glob("*.json")):
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if candidate.get("source_hash") == source_hash:
            template = candidate
            break
    if template is None:
        raise ValueError("No owner descriptions exist for this source fingerprint")
    dialogue = choose_dialogue(structure, [])
    if dialogue["source"] != "release subtitles":
        transcript = clipper.transcribe(source, info.has_audio, "small", cache_folder/"transcription.json",
            threading.Event(), allow_inference=False, audio_index=audio_index)
        if not transcript.words:
            raise ValueError("Matching cached dialogue is unavailable; this tool never starts transcription")
        dialogue = choose_dialogue(structure, transcript.words)
    proposals = propose(template, dialogue["cues"], info.duration, source_hash)
    folder = output / "Review" / source_hash
    decisions = load_decisions(folder/"known-moments.json", source_hash)
    original_ids = set(decisions)
    for saved in template.get("moments", []):
        if saved.get("source_hash") == source_hash and saved.get("status") == "CONFIRMED":
            if not 0 <= saved["start"] < saved["end"] <= info.duration:
                raise ValueError("Saved owner reference is outside this source")
            decisions.setdefault(saved["id"], saved)
    if set(decisions) != original_ids:
        write_json(folder/"known-moments.json", dict(schema="clipper-known-moments-v1", source_hash=source_hash,
            decisions=decisions, moments=[r for r in decisions.values() if r["status"] == "CONFIRMED"]))
    for identity, saved in decisions.items():
        if not any(p["id"] == identity for p in proposals):
            proposals.append(dict(id=identity, source_hash=source_hash, label=saved["label"],
                start=saved["rough_start"], end=saved["rough_end"], status="PROPOSED", quotes=[], matched_terms=[],
                reason="Saved owner reference; the original text match is no longer current"))
    return dict(source_hash=source_hash, duration=info.duration, proposals=proposals, decisions=decisions,
                dialogue_source=dialogue["source"], folder=str(folder),
                confirmed_count=sum(r["status"] == "CONFIRMED" for r in decisions.values()))


def preview(source, folder, proposal, start, end, info, audio_index, cancel):
    import clipper
    from evaluation.build_review_board import preview_command, validate_preview
    start, end = float(start), float(end)
    if not all(map(math.isfinite, (start, end))) or not 0 <= start < end <= info.duration or end-start > 180:
        raise ValueError("Choose an interval inside the source, up to 180 seconds for a review preview")
    identity = hashlib.sha256(json.dumps([proposal["source_hash"], audio_index, start, end]).encode()).hexdigest()[:20]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"preview-{identity}.mp4"
    audio = next(s for s in info.streams if s["index"] == audio_index and s["type"] == "audio")
    language = audio.get("language")
    if target.exists():
        cached = validate_preview(target, audio_index, language, maximum_duration=end-start+.2)
        if abs(cached["duration"]-(end-start)) > .2:
            raise ValueError("Cached review preview duration differs from the requested interval")
        return target
    with tempfile.NamedTemporaryFile(suffix=".mp4", dir=folder, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        clipper._run(preview_command(source, temporary, start, end-start, audio_index, language), cancel)
        validation = validate_preview(temporary, audio_index, language, maximum_duration=end-start+.2)
        if abs(validation["duration"]-(end-start)) > .2:
            raise ValueError("Review preview duration differs from the requested interval")
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)
