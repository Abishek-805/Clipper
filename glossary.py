"""User-owned caption replacements, applied only while formatting captions."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def glossary_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Clipper" / "glossary.json"


def load_glossary(path: Path | None = None) -> list[dict]:
    target = path or glossary_path()
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"replacements": []}\n', encoding="utf-8")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid caption glossary {target}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("replacements"), list):
        raise ValueError("Caption glossary must contain a replacements list")
    result = []
    for entry in data["replacements"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("from"), str) or not entry["from"].strip() or not isinstance(entry.get("to"), str):
            raise ValueError("Each glossary entry needs non-empty from and string to values")
        if not isinstance(entry.get("whole_word", True), bool) or not isinstance(entry.get("case_sensitive", False), bool):
            raise ValueError("whole_word and case_sensitive must be booleans")
        result.append({"from": entry["from"], "to": entry["to"],
                       "whole_word": entry.get("whole_word", True),
                       "case_sensitive": entry.get("case_sensitive", False)})
    return sorted(result, key=lambda entry: -len(entry["from"]))


def apply_replacements(text: str, replacements: list[dict], cue_number: int) -> tuple[str, list[dict]]:
    applied = []
    for entry in replacements:
        pattern = ""
        for index, char in enumerate(entry["from"]):
            if char.isspace():
                following = entry["from"][index + 1:].lstrip()
                optional = following.startswith(("-", "'", "’", ".", ",", "!", "?", ":", ";"))
                pattern += r"(?:\{\\kf\d+\})*\s*" if optional else r"(?:\{\\kf\d+\})*\s+"
            else:
                pattern += re.escape(char) + r"(?:\{\\kf\d+\})*"
        if entry["whole_word"]:
            pattern = r"(?<!\w)" + pattern + r"(?!\w)"
        flags = 0 if entry["case_sensitive"] else re.IGNORECASE
        matches = [match.group(0) for match in re.finditer(pattern, text, flags=flags)]
        text = re.sub(pattern, lambda _match: entry["to"], text, flags=flags)
        applied.extend({"original_text": original.replace("\n", " "), "replacement": entry["to"], "cue_number": cue_number}
                       for original in matches)
    return text, applied
