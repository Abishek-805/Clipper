"""Release-subtitle assessment and deterministic source units, without semantic inference."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from datetime import datetime, timezone

from structure import label, write_json, zone_check

VERSION = "episode-units-v1"


def dialogue_cues(track, zones):
    cues = []
    for cue in track.get("cues", []):
        style = cue.get("style", "")
        if (not cue.get("text") or cue.get("karaoke") or label(style) or
                re.search(r"(?i)(?:^|[ _-])(?:signs?|title|logo|prev\d*|preview|msg|news\d*|text\d*|char.menu)(?:$|[ _-])", style)):
            continue
        if zone_check(cue, [z for z in zones if z["level"] != "SOFT"])[0]:
            continue
        cues.append({"start": cue["start"], "end": cue["end"], "text": cue["text"]})
    # ASS layers sometimes repeat a spoken line; preserve just one timed instance.
    return sorted({(c["start"], c["end"], c["text"]): c for c in cues}.values(),
                  key=lambda c: (c["start"], c["end"], c["text"]))


def assess_tracks(structure):
    output = []
    for track in structure.get("tracks", []):
        cues = dialogue_cues(track, structure["active"])
        title = track.get("title", "").lower()
        reason = "Too little unambiguous dialogue evidence"
        kind = "unknown"
        if "commentary" in title:
            kind, reason = "commentary", "Track title identifies commentary"
        elif track.get("forced"):
            kind, reason = "forced", "Forced flag; not a complete dialogue source"
        elif len(cues) < 20 and len(cues) < len(track.get("cues", [])) * .2:
            kind, reason = "signs/songs", "Predominantly signs/songs; fewer than 20 dialogue cues remain"
        elif len(cues) >= 20:
            letters = [c for cue in cues for c in cue["text"] if c.isalpha()]
            latin = sum("LATIN" in unicodedata.name(c, "") for c in letters) / max(1, len(letters))
            upper = sum(c["text"].isupper() for c in cues) / len(cues)
            covered, cursor = 0., 0.
            for cue in cues:
                covered += max(0., cue["end"] - max(cursor, cue["start"]))
                cursor = max(cursor, cue["end"])
            # Language metadata is evidence, not truth. A mismatch needs owner selection.
            if (track.get("language", "").lower() in ("en", "eng", "english") and latin >= .85
                    and upper < .5 and covered >= structure["duration"] * .15):
                kind, reason = "dialogue", "English metadata and substantial non-song Latin-script dialogue"
            else:
                reason = "Dialogue-like content, but language metadata is absent/conflicting; explicit review required"
        confirmation = next((r for r in structure.get("subtitle_confirmations", [])
                             if r.get("source_hash") == structure.get("source_hash") and r.get("stream_index") == track["index"]), None)
        if confirmation and confirmation.get("decision") == "english_dialogue" and cues:
            kind, reason = "dialogue", "Owner confirmed this source hash and track as English dialogue"
        elif confirmation and confirmation.get("decision") == "not_dialogue":
            kind, reason = "not dialogue", "Owner rejected this source hash and track as dialogue"
        sample_pool = [c for c in cues if len(c["text"]) >= 20] or cues
        positions = sorted({round(i * (len(sample_pool) - 1) / 4) for i in range(5)}) if sample_pool else []
        output.append({"index": track["index"], "language": track.get("language"), "title": track.get("title"),
                       "codec": track.get("codec"), "default": track.get("default", False), "forced": track.get("forced", False),
                       "owner_confirmation": confirmation.get("decision") if confirmation else None,
                       "samples": [sample_pool[i] for i in positions],
                       "classification": kind, "reason": reason, "dialogue_cue_count": len(cues), "cues": cues,
                       "style_counts": track.get("style_counts", {})})
    return output


def confirm_track(structure, stream_index, decision):
    if type(stream_index) is not int or decision not in ("english_dialogue", "not_dialogue", "unreviewed"):
        raise ValueError("Choose a subtitle stream and an explicit review decision")
    track = next((t for t in assess_tracks(structure) if t["index"] == stream_index), None)
    if track is None or (decision == "english_dialogue" and not track["dialogue_cue_count"]):
        raise ValueError("This track has no usable dialogue cues")
    records = [r for r in structure.get("subtitle_confirmations", []) if not
               (r.get("source_hash") == structure["source_hash"] and r.get("stream_index") == stream_index)]
    if decision != "unreviewed":
        records.append(dict(source_hash=structure["source_hash"], stream_index=stream_index, decision=decision,
                            confirmed_at=datetime.now(timezone.utc).isoformat()))
    write_json(structure["confirmation_file"], records)
    return records


def choose_dialogue(structure, words):
    tracks = assess_tracks(structure)
    usable = [t for t in tracks if t["classification"] == "dialogue"]
    confirmed = [t for t in usable if t["owner_confirmation"] == "english_dialogue"]
    if confirmed:
        usable = confirmed
    if len(usable) == 1:
        selected = usable[0]
        return {"source": "release subtitles", "stream_index": selected["index"],
                "reason": selected["reason"], "cues": selected["cues"], "tracks": tracks}
    cues = []
    for word in sorted(words, key=lambda w: (w.start, w.end)):
        text = word.text.strip()
        if not text:
            continue
        if cues and word.start - cues[-1]["end"] <= .8 and not re.search(r"[.!?][\"']?$", cues[-1]["text"]):
            cues[-1]["end"] = max(cues[-1]["end"], word.end)
            cues[-1]["text"] += " " + text
        else:
            cues.append({"start": word.start, "end": word.end, "text": text})
    return {"source": "cached Whisper words" if cues else "unavailable", "stream_index": None,
            "reason": "No unique reliable English dialogue track; Whisper fallback", "cues": cues, "tracks": tracks}


def release_cues(structure):
    result = choose_dialogue(structure, [])
    if result["source"] != "release subtitles":
        raise ValueError("No unique reliable English dialogue subtitle track. Choose Whisper captions; inspect track classification before using release subtitles.")
    return result


def clipped_cues(cues, start, end):
    return [dict(start=max(start, c["start"]), end=min(end, c["end"]), text=c["text"])
            for c in cues if c["end"] > start and c["start"] < end]


def build_units(duration, cuts, dialogue, zones, source_hash, audio_index):
    cuts = sorted({float(c) for c in cuts if 0 < c < duration})
    cues = [c for c in dialogue["cues"] if 0 <= c["start"] < c["end"] <= duration and c["text"]]
    boundaries = {0., float(duration)}
    for zone in zones:
        boundaries.update((max(0., zone["start"]), min(duration, zone["end"])))
    # A cut alone is a shot, not necessarily a new scene.
    for previous, current in zip(cues, cues[1:]):
        if current["start"] - previous["end"] >= 3:
            between = [cut for cut in cuts if previous["end"] <= cut <= current["start"]]
            if between:
                center = (previous["end"] + current["start"]) / 2
                boundaries.add(min(between, key=lambda c: (abs(c - center), c)))
    boundaries = sorted(boundaries)
    scenes = [dict(id=f"SC_{i:04}", start=start, end=end, utterance_ids=[], shot_ids=[])
              for i, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1)]
    utterances = [dict(id=f"U_{i:04}", **cue) for i, cue in enumerate(cues, 1)]
    shot_edges = [0.] + cuts + [float(duration)]
    shots = [dict(id=f"S_{i:04}", start=start, end=end)
             for i, (start, end) in enumerate(zip(shot_edges, shot_edges[1:]), 1)]
    for scene in scenes:
        scene["utterance_ids"] = [u["id"] for u in utterances if u["start"] < scene["end"] and u["end"] > scene["start"]]
        scene["shot_ids"] = [s["id"] for s in shots if s["start"] < scene["end"] and s["end"] > scene["start"]]
    return dict(version=VERSION, source_hash=source_hash, audio_stream_index=audio_index,
                subtitle_source={k: dialogue[k] for k in ("source", "stream_index", "reason")},
                scenes=scenes, utterances=utterances, shots=shots,
                boundary_rule="zone boundary OR >=3s dialogue gap containing a shot cut; no semantic scene claim")


def save_units(folder, units):
    key = hashlib.sha256(json.dumps(units, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    path = Path(folder) / f"episode-units-{key}.json"
    if not path.exists():
        write_json(path, units)
    return path
