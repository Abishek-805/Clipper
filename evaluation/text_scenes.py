"""Opt-in text-only scene benchmark. Never used by automatic production selection."""
from __future__ import annotations

import json
from urllib.request import Request, ProxyHandler, build_opener

from editorial import local_endpoint
from evaluation.llama_cpp_vlm import _NoRedirect

VERSION = "scene-text-audit-v1"
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["scene_id", "summary", "names", "tones", "references"], "properties": {
              "scene_id": {"type": "string"}, "summary": {"type": "string", "maxLength": 600},
              "names": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
              "tones": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
              "references": {"type": "array", "maxItems": 4, "items": {"type": "object",
                  "additionalProperties": False, "required": ["utterance_id", "quote"],
                  "properties": {"utterance_id": {"type": "string"}, "quote": {"type": "string"}}}}}}


def scene_lines(scene, units):
    identities = set(scene["utterance_ids"])
    return [u for u in units["utterances"] if u["id"] in identities]


def audit_response(raw, scene, lines):
    """Literal checks cannot determine whether a free-text summary is entailed."""
    errors = []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"json_valid": False, "errors": ["malformed JSON"], "supported_names": 0,
                "name_count": 0, "valid_references": 0, "reference_count": 0,
                "supported_quotes": 0, "quote_count": 0, "fact_audit": "NOT_ASSESSED"}
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        return {"json_valid": True, "errors": ["invalid response fields"], "supported_names": 0,
                "name_count": 0, "valid_references": 0, "reference_count": 0,
                "supported_quotes": 0, "quote_count": 0, "fact_audit": "NOT_ASSESSED"}
    source = " ".join(u["text"] for u in lines)
    lookup = {u["id"]: u["text"] for u in lines}
    references, names = value["references"], value["names"]
    if (not isinstance(value["summary"], str) or not value["summary"].strip() or len(value["summary"]) > 600
            or not isinstance(value["tones"], list) or any(not isinstance(t, str) for t in value["tones"])
            or not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names)
            or not isinstance(references, list) or any(not isinstance(r, dict) or set(r) != {"utterance_id", "quote"}
                or not isinstance(r["utterance_id"], str) or not isinstance(r["quote"], str) for r in references)):
        return {"json_valid": True, "errors": ["invalid field types"], "supported_names": 0,
                "name_count": 0, "valid_references": 0, "reference_count": 0,
                "supported_quotes": 0, "quote_count": 0, "fact_audit": "NOT_ASSESSED"}
    valid_ids = int(value["scene_id"] == scene["id"]) + sum(r["utterance_id"] in lookup for r in references)
    supported_names = sum(n in source for n in names)
    supported_quotes = sum(bool(r["quote"].strip()) and " ".join(r["quote"].split()) in
                           " ".join(lookup.get(r["utterance_id"], "").split()) for r in references)
    if valid_ids != len(references) + 1:
        errors.append("unknown scene or utterance ID")
    if supported_names != len(names):
        errors.append("name absent from supplied scene text")
    if supported_quotes != len(references):
        errors.append("quote not present in cited source utterance")
    if value["summary"].strip().lower().rstrip(".") != "unclear" and not references:
        errors.append("summary without source references")
    return dict(json_valid=True, errors=errors, supported_names=supported_names, name_count=len(names),
                valid_references=valid_ids, reference_count=len(references)+1, supported_quotes=supported_quotes,
                quote_count=len(references), fact_audit="PENDING_SOURCE_ENTAILMENT_REVIEW")


def request_scene(endpoint, model, scene, lines, timeout=90, revised=False, allowed_names=()):
    endpoint = local_endpoint(endpoint)
    if not endpoint:
        raise ValueError("An explicit loopback endpoint is required")
    prompt = ("Summarize only the supplied fictional-scene dialogue in one or two sentences. Do not use prior "
              "knowledge of any story. Do not infer visual actions, identities, speakers, relationships or facts "
              "that these lines do not establish. Return Unclear if insufficient. Names must occur literally in "
              "the supplied text (do not repair spellings). Return this scene_id, summary, names, tones, and "
              "references: exact verbatim quote plus utterance_id supporting the summary. IDs and quotes must "
              "come from this input. No timestamps, no candidate rankings, no additional fields.\n" +
              json.dumps({"scene_id": scene["id"], "utterances": [{"id": u["id"], "text": u["text"]} for u in lines]}, ensure_ascii=False))
    schema = SCHEMA
    if revised:
        from evaluation.literal_evidence import TONES
        schema = json.loads(json.dumps(SCHEMA))
        schema["required"].remove("names")
        schema["required"].append("selected_names")
        schema["properties"].pop("names")
        schema["properties"]["selected_names"] = {"type": "array", "maxItems": min(4, len(allowed_names)),
            "items": {"type": "string", "enum": list(allowed_names) or [""]}}
        schema["properties"]["tones"]["items"] = {"type": "string", "enum": TONES}
        schema["properties"]["summary"]["maxLength"] = 200
        schema["properties"]["references"]["maxItems"] = 2
        prompt = ("Use only the supplied dialogue. Output one factual restatement of AT MOST 20 WORDS, or 'unclear'. "
                  "NO SPEAKER ATTRIBUTION: speakers are unknown; never assign a line or role to a named person. "
                  "Do not write 'NAME says/asks/explains' or 'NAME, who is ...'. No relationships, friendship, "
                  "feelings, motives or intentions unless literally stated in the supplied text. Do not guess who "
                  "speaks, infer actions, or use outside story knowledge. selected_names is optional ([] is valid); "
                  "choose ONLY from name_list. A name selection never identifies a speaker. Use only fixed tone "
                  "tags, or unclear. Cite one or two supporting utterance IDs with exact source quotes. No timestamps, "
                  "no recommendations, no extra fields. Short and cautious is better than speculation.\n" +
                  json.dumps({"scene_id": scene["id"], "name_list": list(allowed_names), "tone_list": TONES,
                              "utterances": [{"id": u["id"], "text": u["text"]} for u in lines]}, ensure_ascii=False))
    return request_json(endpoint, model, prompt, schema, timeout)


def request_json(endpoint, model, prompt, schema, timeout=90):
    endpoint = local_endpoint(endpoint)
    if not endpoint:
        raise ValueError("An explicit loopback endpoint is required")
    body = dict(model=model, messages=[dict(role="user", content=prompt)], temperature=0, max_tokens=512,
                chat_template_kwargs={"enable_thinking": False}, response_format={"type": "json_schema",
                    "json_schema": {"name": "text_scene", "strict": True, "schema": schema}})
    request = Request(endpoint, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
        value = json.load(response)
    message = value["choices"][0]["message"]
    timings = {**(value.get("timings") or {}), "thinking_disabled_requested": True,
               "reasoning_content_present": bool(message.get("reasoning_content")),
               "thinking_tags_present": "<think>" in (message.get("content") or "")}
    return message["content"], value.get("usage"), timings


def benchmark_metrics(rows):
    executed = [r for r in rows if r.get("status") == "executed"]
    audits = [r["audit"] for r in executed]
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None
    names = sum(a["name_count"] for a in audits)
    quotes = sum(a["quote_count"] for a in audits)
    ids = sum(a["reference_count"] for a in audits)
    return dict(executed=len(executed), json_valid_rate=ratio(sum(a["json_valid"] for a in audits), len(audits)),
                literal_integrity_pass_count=sum(not a["errors"] for a in audits),
                unsupported_name_rate=ratio(names-sum(a["supported_names"] for a in audits), names), name_count=names,
                unsupported_quote_rate=ratio(quotes-sum(a["supported_quotes"] for a in audits), quotes), quote_count=quotes,
                valid_id_rate=ratio(sum(a["valid_references"] for a in audits), ids), reference_count=ids,
                invented_fact_rate=None, fact_audit="PENDING_SOURCE_ENTAILMENT_REVIEW")
