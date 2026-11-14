"""Conservative, auditable text checks; these do not establish semantic truth."""
from __future__ import annotations

import json
import re
import unicodedata

VERSION = "literal-evidence-v2.1"
TONES = ["neutral", "instruction", "exposition", "suspense", "emotional", "comedy", "confrontation", "unclear"]
GENERIC = re.compile(r"(?i)^(?:(?:the|a|an)\s+)?(?:(?:mmo\s+)?streamer|person|character|speaker|player|npc|narrator|someone|somebody|friend|mom|mother|sister|developer|system|man|woman|other person|two people|two characters)(?:\s+[ab12])?$")
RELATION = re.compile(r"\b(?:close(?:r|st)?|relationship|friendship|friends?|feels?|felt|wants?|wanted|wishes?|intends?|hopes?|hoped|loves?|hates?|trusts?|because|in order to|so that)\b", re.I)
SPEECH = r"(?:says?|said|asks?|asked|explains?|explained|warns?|announces?|states?|tells?|told|replies|responds?|discuss(?:es|ing)?|pleads?|begs?)"


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    return re.sub(r"\s+", " ", re.sub(r"\s*-\s*", "-", text)).strip()


def name_list(lines, glossary=()):
    source = " ".join(line["text"] for line in lines)
    names = {entry["to"].strip() for entry in glossary if entry.get("to", "").strip()}
    stop = {"I", "A", "The", "This", "That", "It", "You", "My", "We", "They", "He", "She", "And", "But", "If", "So", "No", "Yes", "Yeah", "Hey", "Oh", "Well", "What", "How", "Why", "Who", "All", "As", "In", "On", "To", "For", "Is", "Do", "Don't", "I'm", "It's", "There's", "That's"}
    for token in re.findall(r"\b[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*){0,2}\b", source):
        if token not in stop and not GENERIC.fullmatch(token):
            names.add(token)
    return sorted(names)


def claim_violations(summary, source, names):
    attribution, relationships = [], []
    for name in names:
        if GENERIC.fullmatch(name):
            continue
        patterns = [re.escape(name) + r"\b\s*,?\s*(?:(?:who|in)\b[^,.;!?]{0,65},\s*)?(?:(?:is|was)\s+)?" + SPEECH + r"\b",
                    re.escape(name) + r"\b,?\s+who\s+(?:is|was)\b[^,.;!?]{0,65}"]
        for pattern in patterns:
            for match in re.finditer(pattern, summary, re.I):
                # A named prepositional object is not the speaker: 'journey into SAO, discussing ...'.
                if re.search(r"\b(?:in|into|of|about|from|for|to|called|named|game)\s+$", summary[:match.start()], re.I):
                    continue
                text = match.group(0)
                if normalize(text) not in normalize(source):
                    attribution.append(text)
    tokens = list(re.finditer(r"\S+", summary))
    for match in RELATION.finditer(summary):
        indexes = [i for i, token in enumerate(tokens) if token.start() < match.end() and token.end() > match.start()]
        if not indexes:
            continue
        left, right = max(0, indexes[0]-3), min(len(tokens)-1, indexes[-1]+3)
        text = summary[tokens[left].start():tokens[right].end()].strip(".,;!?")
        # Keep short literal support inside a quotation, without swallowing the following sentence.
        supported = normalize(text) in normalize(source)
        for lo in range(max(0, indexes[0]-3), indexes[0]+1):
            hi = min(len(tokens)-1, lo+3)
            if hi < indexes[-1] or hi-lo < 3:
                continue
            fragment = summary[tokens[lo].start():tokens[hi].end()].strip(".,;!?\"'“”‘’")
            supported = supported or normalize(fragment) in normalize(source)
        if not supported:
            relationships.append(text)
    return sorted(set(attribution)), sorted(set(relationships))


def audit_literal_response(raw, scene, lines, allowed_names=(), revised=False):
    from evaluation.text_scenes import audit_response
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        value = None
    converted = dict(value) if isinstance(value, dict) else value
    if revised and isinstance(converted, dict) and "selected_names" in converted:
        converted["names"] = converted.pop("selected_names")
    base = audit_response(json.dumps(converted), scene, lines)
    base.update(validator_version=VERSION, generic_labels=[], unsupported_proper_names=[], normalizations=[],
                attribution_violations=[], relationship_violations=[])
    if not isinstance(converted, dict) or any(e.startswith("invalid") or e == "malformed JSON" for e in base["errors"]):
        if value is None:
            base["json_valid"] = False
        return base
    base["errors"] = [e for e in base["errors"] if e not in ("name absent from supplied scene text", "quote not present in cited source utterance")]
    source = " ".join(line["text"] for line in lines)
    lookup = {line["id"]: line["text"] for line in lines}
    proper = []
    for name in converted["names"]:
        if GENERIC.fullmatch(name.strip()):
            base["generic_labels"].append(name)
        elif re.search(r"\b[A-Z][a-zA-Z]+", name) or name in allowed_names:
            proper.append(name)
            if normalize(name) not in normalize(source) and normalize(name) not in {normalize(n) for n in allowed_names}:
                base["unsupported_proper_names"].append(name)
    base["name_count"] = len(proper)
    base["supported_names"] = len(proper) - len(base["unsupported_proper_names"])
    base["supported_quotes"] = 0
    for index, reference in enumerate(converted["references"]):
        quote, text = reference["quote"], lookup.get(reference["utterance_id"], "")
        if quote != normalize(quote):
            base["normalizations"].append({"field": f"references[{index}].quote", "before": quote, "after": normalize(quote)})
        base["supported_quotes"] += int(bool(quote.strip()) and normalize(quote) in normalize(text))
    if base["unsupported_proper_names"]:
        base["errors"].append("unsupported proper name")
    if base["supported_quotes"] != base["quote_count"]:
        base["errors"].append("quote not present after normalization")
    names = set(allowed_names) | set(proper) | set(name_list(lines))
    attribution, relationships = claim_violations(converted["summary"], source, names)
    base.update(attribution_violations=attribution, relationship_violations=relationships)
    if attribution:
        base["errors"].append("unsupported named-speaker attribution")
    if relationships:
        base["errors"].append("unsupported relationship/motive claim")
    if revised:
        if len(converted["summary"].split()) > 20:
            base["errors"].append("summary exceeds 20 words")
        if any(t not in TONES for t in converted["tones"]):
            base["errors"].append("tone outside fixed list")
        if any(n not in allowed_names for n in converted["names"]):
            base["errors"].append("selection outside provided name list")
    return base


def violation_metrics(rows):
    audits = [r["audit"] for r in rows if r.get("status") == "executed"]
    return dict(attribution_violation_responses=sum(bool(a.get("attribution_violations")) for a in audits),
                relationship_violation_responses=sum(bool(a.get("relationship_violations")) for a in audits),
                generic_label_entries=sum(len(a.get("generic_labels", [])) for a in audits),
                unsupported_proper_name_entries=sum(len(a.get("unsupported_proper_names", [])) for a in audits),
                normalization_records=sum(len(a.get("normalizations", [])) for a in audits))
