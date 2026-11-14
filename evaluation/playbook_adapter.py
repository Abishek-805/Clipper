"""Load the immutable runtime playbook without shadowing the existing clipper.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "clipper" / "playbook"
_spec = importlib.util.spec_from_file_location("clipper_editorial_vendor", ROOT / "scripts" / "playbook.py")
playbook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(playbook)


def schema_errors(value, rules, path="output"):
    """Only the JSON Schema keywords present in the four supplied schemas are needed."""
    errors = []
    types = {"object": dict, "array": list, "string": str, "boolean": bool}
    kind = rules.get("type")
    if kind not in types or type(value) is not types[kind]:
        return [f"{path}: expected {kind}"]
    if "enum" in rules and value not in rules["enum"]:
        errors.append(f"{path}: outside enum")
    if kind == "object":
        properties = rules.get("properties", {})
        errors.extend(f"{path}: missing {key}" for key in rules.get("required", []) if key not in value)
        if rules.get("additionalProperties") is False:
            errors.extend(f"{path}: extra field {key}" for key in value if key not in properties)
        for key in value.keys() & properties.keys():
            errors.extend(schema_errors(value[key], properties[key], f"{path}.{key}"))
    elif kind == "array":
        if not rules.get("minItems", 0) <= len(value) <= rules.get("maxItems", float("inf")):
            errors.append(f"{path}: invalid array length")
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, rules["items"], f"{path}[{index}]"))
    elif kind == "string":
        if len(value) > rules.get("maxLength", float("inf")):
            errors.append(f"{path}: string too long")
        if "pattern" in rules and not re.search(rules["pattern"], value):
            errors.append(f"{path}: pattern mismatch")
    return errors


def validate(module, raw, context):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return dict(ok=False, errors=["malformed JSON"], warnings=[], json_valid=False)
    errors = schema_errors(value, playbook.schema(module))
    if errors:
        return dict(ok=False, errors=errors, warnings=[], json_valid=True)
    try:
        return {**playbook.VALIDATORS[module](value, context), "json_valid": True}
    except (KeyError, TypeError, ValueError) as exc:
        return dict(ok=False, errors=[f"validator input error: {exc}"], warnings=[], json_valid=True)


def scene_prompt(scene, lines, glossary):
    glossary_names = [entry["to"] for entry in glossary if entry.get("to", "").strip()]
    names = playbook.names_in_text("\n".join(u["text"] for u in lines), glossary_names)
    context = dict(utterances=[dict(id=u["id"], text=u["text"]) for u in lines], names=names, glossary=glossary_names)
    prompt = playbook.build_prompt("scene_summary", dict(NAMES=json.dumps(names, ensure_ascii=False),
        SCENE_ID=scene["id"], UTTERANCES=playbook.format_utterances(context["utterances"])))
    if playbook.estimate_tokens(prompt) > 1800:
        raise ValueError("Scene playbook prompt exceeds the 1800-token estimate; no truncation/inference attempted")
    return prompt, context


def audit_scene(raw, context):
    verdict = validate("scene_summary", raw, context)
    errors = verdict["errors"]
    return dict(json_valid=verdict["json_valid"], errors=errors, warnings=verdict.get("warnings", []),
                accepted=verdict["ok"], name_count=0, supported_names=0, reference_count=0,
                valid_references=0, quote_count=0, supported_quotes=0,
                attribution_violations=[e for e in errors if "speaker attribution" in e],
                relationship_violations=[e for e in errors if "relationship/motive" in e],
                generic_labels=[], unsupported_proper_names=[e for e in errors if "unsupported name" in e],
                normalizations=[], fact_audit="NOT_ASSESSED_RULE_CHECKS_ONLY")
