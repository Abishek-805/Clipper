#!/usr/bin/env python3
"""Clipper editorial playbook: prompt builder and output validators (standard library only).

  python scripts/playbook.py build <module> --vars vars.json     print the full prompt
  python scripts/playbook.py validate <module> --input out.json --context ctx.json
  python scripts/playbook.py schema <module>                     print the JSON schema
Modules: scene_summary, nominate_moments, action_verify, hook_context

Validation never calls a model. It checks a model's JSON against the source text.
Exit code 0 = accepted, 1 = rejected.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = {
    "scene_summary": ("modules/scene_summary.md", "schemas/scene_summary.schema.json"),
    "nominate_moments": ("modules/nominate_moments.md", "schemas/nominations.schema.json"),
    "action_verify": ("modules/action_verify.md", "schemas/action_verdict.schema.json"),
    "hook_context": ("modules/hook_context.md", "schemas/hook_context.schema.json"),
}

GENERIC_LABELS = {"person", "someone", "somebody", "narrator", "speaker", "npc", "character", "man", "woman", "boy", "girl", "everyone", "crowd"}
RELATION_PATTERNS = [r"\bfriend\w*", r"\bbrother\w*", r"\bsister\w*", r"\brival\w*", r"\blover\w*", r"\blov(?:e|es|ed|ing)\b",
                     r"\benem(?:y|ies)\b", r"\btrust\w*", r"\bhat(?:e|es|ed|ing)\b", r"\bclose relationship\b", r"\bwants? to\b",
                     r"\bfeel(?:s|ing)?\b", r"\bafraid of\b", r"\bjealous\w*", r"\bangry at\b"]
CLICKBAIT = ["you won't believe", "you wont believe", "shocking", "insane", "mind-blowing", "mind blowing", "goes viral", "gone wrong", "must watch", "must-watch", "epic fail"]
FIGHT_WORDS = ["fight", "attack", "sword", "slash", "kill", "battle", "strike", "hit", "dodge", "swing", "duel", "defeat"]
SPEECH_VERBS = "says|said|tells|told|asks|asked|replies|replied|shouts|shouted|whispers|whispered|explains|explained|announces|announced"
ATTRIB_RE = re.compile(r"\b([A-Z][a-z]+)\s+(?:" + SPEECH_VERBS + r")\b")
STOP = set("a an the and or but if of to in on at for with is are was were be been it its this that these those i you he she we they me my your our their do does did not no so as by from into out up down just very can could would should will have has had".split())
TOKEN_STOP_CAPS = {"I", "I'm", "I'll", "I've", "I'd"}


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.4) + 1


def normalize(text: str) -> str:
    t = text.lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"\s*[-\u2013\u2014]\s*", "-", t)
    return re.sub(r"\s+", " ", t).strip()


def loose(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 '\-]", " ", normalize(text))).strip()


def words(text: str):
    return re.findall(r"[A-Za-z0-9'\-]+", text)


def format_utterances(items) -> str:
    return "\n".join(f"{u['id']}: {u['text']}" for u in items)


def names_in_text(text: str, glossary):
    """Glossary names plus capitalized words found in the middle of sentences."""
    found = set(glossary)
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        toks = sentence.split()
        for tok in toks[1:]:
            w = re.sub(r"^[^A-Za-z]+|[^A-Za-z'\-]+$", "", tok)
            if w and w[0].isupper() and w not in TOKEN_STOP_CAPS and len(w) > 1:
                found.add(w)
    return sorted(found)


def build_prompt(module: str, variables: dict) -> str:
    template = (ROOT / MODULES[module][0]).read_text(encoding="utf-8")
    common = (ROOT / "modules/common_rules.md").read_text(encoding="utf-8")
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", str(value))
    left = re.findall(r"\{\{[A-Z_]+\}\}", template)
    if left:
        raise ValueError(f"Unfilled placeholders: {left}")
    return common.strip() + "\n\n" + template.strip() + "\n"


def schema(module: str) -> dict:
    return json.loads((ROOT / MODULES[module][1]).read_text(encoding="utf-8"))


def result(errors, warnings, **extra):
    return {"ok": not errors, "errors": errors, "warnings": warnings, **extra}


def unsupported_names(text, allowed, source_text):
    bad = []
    src = normalize(source_text)
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for tok in sentence.split()[1:]:
            w = re.sub(r"^[^A-Za-z]+|[^A-Za-z'\-]+$", "", tok)
            if not w or not w[0].isupper() or w in TOKEN_STOP_CAPS or len(w) < 2:
                continue
            if w.lower() in GENERIC_LABELS:
                continue
            if w in allowed or normalize(w) in src:
                continue
            bad.append(w)
    return sorted(set(bad))


def check_claims(text, source_text, errors, label):
    """Speaker attribution and relationship/motive claims not supported by the lines."""
    for m in ATTRIB_RE.finditer(text):
        errors.append(f"{label}: speaker attribution '{m.group(0)}' (no speaker detection exists)")
    low = normalize(text)
    src = normalize(source_text)
    for pat in RELATION_PATTERNS:
        m = re.search(pat, low)
        if m and not re.search(pat, src):
            errors.append(f"{label}: unsupported relationship/motive term '{m.group(0)}'")


def validate_scene_summary(out, ctx):
    errors, warnings = [], []
    text = format_utterances(ctx["utterances"])
    allowed = set(ctx.get("names", [])) | set(ctx.get("glossary", []))
    for key in ("summary", "names_used", "tone", "fight_described"):
        if key not in out:
            errors.append(f"missing field {key}")
    if errors:
        return result(errors, warnings)
    if len(words(out["summary"])) > 20:
        errors.append("summary longer than 20 words")
    for n in out["names_used"]:
        if n not in allowed and n.lower() not in GENERIC_LABELS:
            errors.append(f"names_used contains '{n}' which is not in the NAMES list")
        elif n.lower() in GENERIC_LABELS:
            warnings.append(f"generic label '{n}' in names_used")
    for n in unsupported_names(out["summary"], allowed, text):
        errors.append(f"summary mentions unsupported name '{n}'")
    check_claims(out["summary"], text, errors, "summary")
    if out["fight_described"] and not any(w in normalize(text) for w in FIGHT_WORDS):
        errors.append("fight_described is true but the lines do not describe fighting")
    return result(errors, warnings)


def overlap_fraction(a0, a1, b0, b1):
    inter = max(0, min(a1, b1) - max(a0, b0))
    return inter / max(1, a1 - a0)


def validate_nominations(out, ctx):
    """ctx: utterances [{id,text,start_ms,end_ms,scene_id}], scene_ids, zones [{start_ms,end_ms,level,label}],
    min_s, max_s, lead_s, tail_s."""
    index = {u["id"]: i for i, u in enumerate(ctx["utterances"])}
    utts = ctx["utterances"]
    scenes = set(ctx.get("scene_ids", []))
    min_s, max_s = ctx.get("min_s", 20.0), ctx.get("max_s", 58.0)
    lead, tail = ctx.get("lead_s", 0.3), ctx.get("tail_s", 0.6)
    zones = ctx.get("zones", [])
    allowed = set(ctx.get("names", [])) | set(ctx.get("glossary", []))
    results = []
    for n, nom in enumerate(out.get("nominations", [])):
        errors, warnings, resolved = [], [], None
        s, e = nom.get("start_utterance_id"), nom.get("end_utterance_id")
        if s not in index or e not in index:
            errors.append("start or end utterance ID does not exist")
        elif index[s] > index[e]:
            errors.append("start comes after end")
        else:
            i0, i1 = index[s], index[e]
            span_text = " ".join(u["text"] for u in utts[i0:i1 + 1])
            for sc in nom.get("scene_ids", []):
                if scenes and sc not in scenes:
                    errors.append(f"scene ID {sc} does not exist")
            for q in nom.get("quotes", []):
                qi = index.get(q.get("utterance_id"))
                if qi is None:
                    errors.append(f"quote cites unknown ID {q.get('utterance_id')}")
                elif not (i0 <= qi <= i1):
                    errors.append(f"quote {q['utterance_id']} lies outside the nominated range")
                elif loose(q["text"]) not in loose(utts[qi]["text"]):
                    errors.append(f"quote does not match {q['utterance_id']}: '{q['text'][:50]}'")
            reason = nom.get("reason", "")
            if len(words(reason)) > 35:
                errors.append("reason longer than 35 words")
            check_claims(reason, span_text, errors, "reason")
            for bad in unsupported_names(reason, allowed, span_text):
                errors.append(f"reason mentions unsupported name '{bad}'")
            t0 = max(0, utts[i0]["start_ms"] - int(lead * 1000))
            t1 = utts[i1]["end_ms"] + int(tail * 1000)
            dur = (t1 - t0) / 1000.0
            resolved = {"start_ms": t0, "end_ms": t1, "duration_s": round(dur, 2)}
            if dur < min_s:
                errors.append(f"duration {dur:.1f}s is below {min_s}s")
            if dur > max_s:
                errors.append(f"duration {dur:.1f}s is above {max_s}s")
            for z in zones:
                frac = overlap_fraction(t0, t1, z["start_ms"], z["end_ms"])
                inside = (z["start_ms"] <= t0 <= z["end_ms"]) or (z["start_ms"] <= t1 <= z["end_ms"])
                if z["level"] in ("HARD", "VERIFIED") and (frac > 0.30 or inside):
                    errors.append(f"overlaps {z['level']} zone {z.get('label', '')}")
                elif z["level"] == "SOFT" and frac > 0:
                    warnings.append(f"touches SOFT zone {z.get('label', '')} ({frac:.0%}); flag and rank lower")
            if not nom.get("standalone", False):
                warnings.append("needs earlier context; rank lower or add a context line")
            if nom.get("type") == "fight" and not any(w in normalize(span_text) for w in FIGHT_WORDS):
                warnings.append("type 'fight' but the lines do not describe fighting; needs the visual check")
        results.append({"index": n, "accepted": not errors, "errors": errors, "warnings": warnings, "resolved": resolved})
    all_ok = all(r["accepted"] for r in results)
    return {"ok": all_ok, "results": results, "accepted_count": sum(r["accepted"] for r in results)}


def validate_action(out, ctx):
    errors, warnings = [], []
    allowed = set(ctx.get("names", [])) | set(ctx.get("glossary", []))
    if out.get("verdict") not in ("CONFIRM", "REJECT", "UNCLEAR"):
        errors.append("verdict must be CONFIRM, REJECT or UNCLEAR")
    ev = out.get("evidence", [])
    if len(ev) > 4:
        errors.append("more than 4 evidence items")
    for item in ev:
        if len(words(item)) > 12:
            errors.append(f"evidence too long: '{item[:40]}'")
        for bad in unsupported_names(" x " + item, allowed, ""):
            errors.append(f"evidence names '{bad}' which is not in NAMES")
    if out.get("verdict") == "CONFIRM" and len(ev) < 2:
        errors.append("CONFIRM needs at least 2 evidence items")
    if out.get("verdict") == "CONFIRM" and out.get("confidence") == "low":
        warnings.append("CONFIRM with low confidence; treat as UNCLEAR")
    return result(errors, warnings)


def validate_hook(out, ctx):
    errors, warnings = [], []
    utts = ctx["utterances"]
    ids = {u["id"] for u in utts}
    text = format_utterances(utts)
    allowed = set(ctx.get("names", [])) | set(ctx.get("glossary", []))
    hooks, context = out.get("hooks", []), out.get("context", "")
    if len(hooks) != 3:
        errors.append("exactly 3 hooks required")
    final = loose(utts[-1]["text"]) if utts else ""
    for h in hooks:
        if len(words(h)) > 8:
            errors.append(f"hook longer than 8 words: '{h}'")
        if any(c in normalize(h) for c in CLICKBAIT):
            errors.append(f"clickbait phrase in hook: '{h}'")
        if len(final.split()) >= 4 and final in loose(h):
            errors.append(f"hook reveals the final line: '{h}'")
        for bad in unsupported_names(" x " + h, allowed, text):
            errors.append(f"hook names '{bad}' not found in the transcript or NAMES")
        content = [w.lower() for w in words(h) if w.lower() not in STOP]
        if content and not any(w in loose(text).split() for w in content):
            warnings.append(f"hook shares no words with the transcript: '{h}'")
    if len(words(context)) > 14:
        errors.append("context longer than 14 words")
    if any(c in normalize(context) for c in CLICKBAIT):
        errors.append("clickbait phrase in context")
    for bad in unsupported_names(" x " + context, allowed, text):
        errors.append(f"context names '{bad}' not found in the transcript or NAMES")
    check_claims(context, text, errors, "context")
    for sid in out.get("source_ids", []):
        if sid not in ids:
            errors.append(f"source ID {sid} does not exist")
    return result(errors, warnings)


VALIDATORS = {
    "scene_summary": validate_scene_summary,
    "nominate_moments": validate_nominations,
    "action_verify": validate_action,
    "hook_context": validate_hook,
}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("module", choices=MODULES); b.add_argument("--vars", required=True)
    v = sub.add_parser("validate"); v.add_argument("module", choices=MODULES)
    v.add_argument("--input", required=True); v.add_argument("--context", required=True)
    s = sub.add_parser("schema"); s.add_argument("module", choices=MODULES)
    a = p.parse_args(argv)
    if a.cmd == "build":
        prompt = build_prompt(a.module, json.loads(Path(a.vars).read_text(encoding="utf-8")))
        print(prompt)
        print(f"[approx tokens: {estimate_tokens(prompt)}]", file=sys.stderr)
        return 0
    if a.cmd == "schema":
        print(json.dumps(schema(a.module), indent=2))
        return 0
    out = json.loads(Path(a.input).read_text(encoding="utf-8"))
    ctx = json.loads(Path(a.context).read_text(encoding="utf-8"))
    res = VALIDATORS[a.module](out, ctx)
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
