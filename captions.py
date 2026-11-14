"""Small deterministic word-level caption grouping and ASS preset helpers."""
from __future__ import annotations

PRESETS = {"bold_pop", "karaoke", "cinematic_minimal", "boxed"}
ANCHOR_BOXES = {"lower": (.08, .70, .92, .90), "middle": (.08, .40, .92, .60),
                "upper": (.08, .16, .92, .36)}


def choose_placement(obstructions: list[dict], override: str = "auto", previous: str | None = None,
                     hysteresis: float = .08) -> dict:
    if override not in {"auto", "lower", "middle", "upper"}:
        raise ValueError("Caption placement must be auto, lower, middle or upper")
    if override != "auto":
        return {"anchor": override, "scores": {}, "reason": "user override"}
    scores = {}
    for anchor, zone in ANCHOR_BOXES.items():
        left, top, right, bottom = zone
        score = .2 if top < .1 or bottom > .92 else 0.0
        for item in obstructions:
            box = item.get("box", ())
            if len(box) != 4:
                continue
            x1, y1, x2, y2 = box
            overlap = max(0.0, min(right, x2) - max(left, x1)) * max(0.0, min(bottom, y2) - max(top, y1))
            kind_weight = {"face": 3.0, "subject": 2.0, "action": 1.8, "source_text": 4.0}.get(item.get("kind"), 1.0)
            score += overlap * kind_weight
        scores[anchor] = round(score, 5)
    selected = min(scores, key=lambda key: (scores[key], {"lower": 0, "middle": 1, "upper": 2}[key]))
    if previous in scores and scores[previous] <= scores[selected] + hysteresis:
        selected = previous
    return {"anchor": selected, "scores": scores, "reason": "lowest obstruction score with stable-anchor hysteresis"}


def anchor_override(anchor: str) -> str:
    return {"lower": r"{\an2\pos(540,1600)}", "middle": r"{\an5\pos(540,960)}",
            "upper": r"{\an8\pos(540,420)}"}[anchor]


def shot_placements(shots: list[dict], override: str = "auto") -> list[dict]:
    result = []
    for shot in shots:
        selected = choose_placement(shot.get("obstructions", []), override)
        result.append({"start": shot["start"], "end": shot["end"], **selected})
    return result


def join_word_tokens(tokens: list[str], prefixes: list[str] | None = None) -> str:
    if not tokens:
        return ""
    cleaned = [token.replace("\n", " ").replace("\r", " ") for token in tokens]
    cleaned[0] = cleaned[0].lstrip()
    cleaned[-1] = cleaned[-1].rstrip()
    prefixes = prefixes or [""] * len(cleaned)
    return "".join(prefix + token for prefix, token in zip(prefixes, cleaned))


def group_words(words: list, start: float, end: float, style: str = "bold_pop") -> list[list]:
    if style not in PRESETS:
        raise ValueError(f"Unknown caption style: {style}")
    limit = 9 if style == "cinematic_minimal" else 5
    groups, current = [], []
    for word in words:
        if word.end <= start or word.start >= end:
            continue
        line_width = sum(len(item.text.strip()) for item in current) + max(0, len(current) - 1)
        if current and (len(current) >= limit or line_width + len(word.text.strip()) + 1 > 36 or
                        word.start - current[-1].end > .65 or
                        current[-1].text.rstrip().endswith((".", "?", "!", "…"))):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    for index in range(1, len(groups)):
        previous, trailing = groups[index - 1], groups[index]
        fragment_duration = trailing[-1].end - trailing[0].start
        if not 1 <= len(trailing) <= 2 or fragment_duration >= .5 or len(previous) < 2:
            continue
        if trailing[0].start - previous[-1].end > .3:
            continue
        if previous[-1].text.rstrip().endswith((".", "?", "!", "…")):
            continue
        if len(previous) + len(trailing) > 7 or len(join_word_tokens([previous[-1].text, *[word.text for word in trailing]])) > 36:
            continue
        trailing[:0] = previous[-1:]
        del previous[-1:]
    return groups


def caption_lines(words: list) -> list[str]:
    split = caption_line_break(words)
    if split is None:
        return [join_word_tokens([word.text for word in words])]
    return [join_word_tokens([word.text for word in words[:split]]),
            join_word_tokens([word.text for word in words[split:]])]


def caption_line_break(words: list) -> int | None:
    if len(words) <= 4:
        return None
    return min(range(1, len(words)), key=lambda n: (
        max(len(join_word_tokens([w.text for w in words[:n]])),
            len(join_word_tokens([w.text for w in words[n:]]))), abs(2 * n - len(words))))


def cue_times(groups: list[list], start: float, end: float, minimum: float = .7) -> list[tuple[float, float]]:
    duration = end - start
    bounds = [(max(0.0, group[0].start - start), min(duration, group[-1].end - start)) for group in groups]
    for index, (cue_start, cue_end) in enumerate(bounds):
        next_start = bounds[index + 1][0] if index + 1 < len(bounds) else duration
        cue_start = max(cue_start, bounds[index - 1][1] if index else 0.0)
        cue_end = min(next_start, max(cue_end, min(duration, cue_start + minimum)))
        bounds[index] = (cue_start, max(cue_start, cue_end))
    return bounds


def ass_style(style: str = "bold_pop", placement: str = "lower") -> str:
    if placement == "auto":
        placement = "lower"
    if style not in PRESETS or placement not in {"lower", "middle", "upper"}:
        raise ValueError("Invalid caption style or placement")
    fontsize, outline, border = (52, 3, 1) if style == "cinematic_minimal" else (66, 5, 1)
    if style == "boxed":
        border, outline = 3, 0
    alignment, margin = {"lower": (2, 250), "middle": (5, 0), "upper": (8, 300)}[placement]
    bold = 0 if style == "cinematic_minimal" else -1
    return (f"Style: Default,Arial,{fontsize},&H00FFFFFF,&H0000D7FF,&H00101010,&H80000000,{bold},0,0,0,100,100,0,0,{border},{outline},2,{alignment},70,70,{margin},1")
