"""Small deterministic word-level caption grouping and ASS preset helpers."""
from __future__ import annotations

PRESETS = {"bold_pop", "karaoke", "cinematic_minimal", "boxed"}


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
    return groups


def ass_style(style: str = "bold_pop", placement: str = "lower") -> str:
    if style not in PRESETS or placement not in {"lower", "middle", "upper"}:
        raise ValueError("Invalid caption style or placement")
    fontsize, outline, border = (52, 3, 1) if style == "cinematic_minimal" else (66, 5, 1)
    if style == "boxed":
        border, outline = 3, 0
    alignment, margin = {"lower": (2, 250), "middle": (5, 0), "upper": (8, 300)}[placement]
    bold = 0 if style == "cinematic_minimal" else -1
    return (f"Style: Default,Arial,{fontsize},&H00FFFFFF,&H0000D7FF,&H00101010,&H80000000,{bold},0,0,0,100,100,0,0,{border},{outline},2,{alignment},70,70,{margin},1")
