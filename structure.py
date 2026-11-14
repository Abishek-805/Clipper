"""Evidence-based episode structure; no model calls or changes to signal caches."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

from diagnostics import ffmpeg_path, ffprobe_path

VERSION = "episode-structure-v1"
LABELS = {"op": "OP", "opening": "OP", "ed": "ED", "ending": "ED",
          "credits": "credits", "preview": "preview", "next episode": "preview",
          "recap": "recap", "song": "song", "karaoke": "song"}


def label(text):
    # Separators in release style names are common; never match substrings like 'Default'.
    text = re.sub(r"[_-]+", " ", text).lower()
    return next((value for key, value in LABELS.items() if re.search(r"\b" + key + r"\b", text)), None)


def timestamp(text):
    h, m, s = text.strip().replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def clean_text(text):
    # ASS vector drawings are signs, not dialogue.
    if re.search(r"\\p[1-9]", text):
        return ""
    return re.sub(r"\s+", " ", re.sub(r"\{[^}]*\}|<[^>]*>", "", text)
                  .replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")).strip()


def parse_subtitles(text, ass=True):
    cues, styles, section, fields = [], [], "", []
    if not ass:
        for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
            lines = block.splitlines()
            timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
            if timing is not None:
                left, right = lines[timing].split("-->")
                raw = " ".join(lines[timing + 1:])
                cues.append(dict(start=timestamp(left), end=timestamp(right.split()[0]), style="Default",
                                 raw=raw, text=clean_text(raw), karaoke=False))
        return cues, ["Default"]
    for line in text.splitlines():
        if line.startswith("["):
            section, fields = line.strip().lower(), []
        elif line.lower().startswith("format:"):
            fields = [part.strip().lower() for part in line.split(":", 1)[1].split(",")]
        elif line.startswith("Style:") and section in ("[v4+ styles]", "[v4 styles]"):
            parts = line.split(":", 1)[1].strip().split(",", len(fields) - 1)
            if "name" in fields:
                styles.append(parts[fields.index("name")])
        elif line.startswith("Dialogue:") and section == "[events]" and fields:
            item = dict(zip(fields, line.split(":", 1)[1].strip().split(",", len(fields) - 1)))
            raw = item.get("text", "")
            cues.append(dict(start=timestamp(item["start"]), end=timestamp(item["end"]),
                             style=item.get("style", "Default"), raw=raw, text=clean_text(raw),
                             karaoke=bool(re.search(r"\\[kK](?:f|o)?\d", raw))))
    return sorted(cues, key=lambda c: (c["start"], c["end"])), styles


def _zone(start, end, name, level, source, evidence):
    identity = json.dumps([start, end, name, level, source], sort_keys=True)
    return dict(id="Z_" + hashlib.sha256(identity.encode()).hexdigest()[:12], start=start, end=end,
                label=name, level=level, source=source, evidence=evidence, reason="; ".join(evidence))


def detect_zones(chapters, tracks, duration):
    zones = []
    for chapter in chapters:
        title = chapter.get("tags", {}).get("title", "")
        name = label(title)
        if name and name != "song":
            zones.append(_zone(float(chapter["start_time"]), float(chapter["end_time"]), name,
                               "HARD", "chapter", ["Explicit chapter: " + title]))
    for track in tracks:
        groups = {}
        for cue in track.get("cues", []):
            name = label(cue["style"])
            if name or cue["karaoke"]:
                groups.setdefault((cue["style"], name or "song"), []).append(cue)
        for (style, name), cues in groups.items():
            runs = []
            for cue in sorted(cues, key=lambda c: c["start"]):
                if not runs or cue["start"] - max(c["end"] for c in runs[-1]) > 12:
                    runs.append([])
                runs[-1].append(cue)
            for run in runs:
                start, end = min(c["start"] for c in run), max(c["end"] for c in run)
                support = []
                if any(c["karaoke"] for c in run):
                    support.append("ASS karaoke timing tags")
                if sum("♪" in c["raw"] or "♫" in c["raw"] for c in run) >= 3:
                    support.append("At least three timed musical-note lyric lines")
                if any(z["level"] == "HARD" and z["start"] < end and z["end"] > start for z in zones):
                    support.append("Overlapping explicit structural chapter")
                zones.append(_zone(start, end, name, "VERIFIED" if support else "SOFT",
                                   f"subtitle:{track['index']}:{style}",
                                   ["Style: " + style] + (support or ["Style name alone; unverified"])))
    # Priors warn only. Intro may contain a cold open or ordinary story content.
    if duration >= 600:
        zones.extend([_zone(0., min(240., duration), "OP", "SOFT", "position", ["Early episode prior; not proof of an opening"]),
                      _zone(max(0., duration - 180), duration, "ED", "SOFT", "position", ["Late episode prior; story remains eligible"])])
    return sorted(zones, key=lambda z: (z["start"], z["end"], z["id"]))


def apply_overrides(detected, overrides, duration):
    active = {z["id"]: dict(z) for z in detected}
    for record in overrides:
        action, identity = record.get("action"), record.get("id")
        if action == "suppress":
            if identity not in {z["id"] for z in detected}:
                raise ValueError("Suppression must reference a detected zone")
            active.pop(identity, None)
            continue
        if action not in ("add", "edit") or not isinstance(identity, str):
            raise ValueError("Use add/edit/suppress with a zone id; remove manual zones by removing their add record")
        if (action == "edit" and identity not in active) or (action == "add" and identity in active):
            raise ValueError("Unknown edit or duplicate zone id")
        item = {**active.get(identity, {}), **record.get("zone", {}), "id": identity}
        left, right = item.get("start"), item.get("end")
        if (type(left) not in (float, int) or type(right) not in (float, int) or
                not math.isfinite(left) or not math.isfinite(right) or not 0 <= left < right <= duration):
            raise ValueError("Zone bounds must be finite, ordered and inside the source")
        if item.get("level") not in ("HARD", "VERIFIED", "SOFT") or item.get("label") not in set(LABELS.values()):
            raise ValueError("Invalid zone level or label")
        item.update(source="manual override", reason=str(item.get("reason", "Owner override")), evidence=["Owner override"])
        active[identity] = item
    return sorted(active.values(), key=lambda z: (z["start"], z["id"]))


def zone_check(candidate, zones):
    rejected, warnings = [], []
    start, end = candidate["start"], candidate["end"]
    for zone in zones:
        overlap = max(0., min(end, zone["end"]) - max(start, zone["start"]))
        if overlap <= 0:
            continue
        item = dict(id=zone["id"], level=zone["level"], label=zone["label"],
                    overlap_fraction=round(overlap / max(.001, end - start), 6), reason=zone["reason"])
        if zone["level"] == "SOFT":
            warnings.append(item)
        elif (zone["start"] <= start < zone["end"] or zone["start"] < end <= zone["end"]
              or overlap / max(.001, end - start) > .30):
            rejected.append(item)
    return rejected, warnings


def inspect_source(path):
    metadata = json.loads(subprocess.run([ffprobe_path(), "-v", "error", "-show_streams", "-show_chapters",
                                         "-show_format", "-of", "json", str(path)], capture_output=True,
                                        text=True, encoding="utf-8", check=True).stdout)
    tracks = []
    with tempfile.TemporaryDirectory(prefix="clipper-subtitles-") as tmp:
        for stream in metadata.get("streams", []):
            if stream.get("codec_type") != "subtitle":
                continue
            track = dict(index=stream["index"], codec=stream.get("codec_name"),
                         language=stream.get("tags", {}).get("language", "und"),
                         title=stream.get("tags", {}).get("title", ""),
                         default=bool(stream.get("disposition", {}).get("default")),
                         forced=bool(stream.get("disposition", {}).get("forced")), cues=[])
            try:
                ass = track["codec"] in ("ass", "ssa")
                target = Path(tmp) / (str(stream["index"]) + (".ass" if ass else ".srt"))
                subprocess.run([ffmpeg_path(), "-v", "error", "-i", str(path), "-map", f"0:{stream['index']}",
                                "-c:s", "ass" if ass else "srt", str(target)], capture_output=True, check=True)
                track["cues"], track["styles"] = parse_subtitles(target.read_text(encoding="utf-8-sig"), ass)
                track["style_counts"] = dict(Counter(c["style"] for c in track["cues"]))
            except (subprocess.CalledProcessError, ValueError, OSError) as exc:
                track["error"] = str(exc)
            tracks.append(track)
    duration = float(metadata["format"]["duration"])
    chapters = metadata.get("chapters", [])
    return dict(version=VERSION, duration=duration, chapters=chapters, tracks=tracks,
                detected=detect_zones(chapters, tracks, duration))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def prepare_structure(path, cache_folder, job_folder=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    source_hash = digest.hexdigest()
    artifact = Path(cache_folder) / f"structure-{source_hash[:20]}.json"
    try:
        data = json.loads(artifact.read_text(encoding="utf-8"))
        if data.get("version") != VERSION or data.get("source_hash") != source_hash:
            raise ValueError("Structure cache version/source mismatch")
    except (OSError, ValueError):
        data = {**inspect_source(path), "source_hash": source_hash}
        write_json(artifact, data)
    override_path = Path(cache_folder) / f"zone-overrides-{source_hash[:20]}.json"
    records = json.loads(override_path.read_text(encoding="utf-8")) if override_path.exists() else []
    if not isinstance(records, list) or len(records) > 500:
        raise ValueError("Expected at most 500 zone override records")
    result = {**data, "overrides": records, "override_file": str(override_path),
              "active": apply_overrides(data["detected"], records, data["duration"])}
    if job_folder is not None:
        write_json(Path(job_folder) / "structure.json", result)
    return result
