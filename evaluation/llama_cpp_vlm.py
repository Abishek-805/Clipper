"""Opt-in adapter for a user-started llama.cpp OpenAI-compatible loopback server."""
from __future__ import annotations

import base64
import ipaddress
import json
import mimetypes
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from pathlib import Path

from evaluation.providers import validate_request, validate_result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class LlamaCppVisualEventAnalyzer:
    """Calls only a local llama.cpp endpoint; construction never starts or downloads a model."""

    def __init__(self, endpoint: str = "http://127.0.0.1:8080/v1/chat/completions",
                 model: str = "Qwen3-VL-2B-Instruct-GGUF", timeout: float = 90.0):
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("The llama.cpp adapter requires an HTTP loopback endpoint")
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname.casefold() == "localhost"
        if not local:
            raise ValueError("External inference endpoints are not allowed")
        self.endpoint, self.model, self.timeout = endpoint, model, timeout

    def analyze(self, request: dict) -> dict:
        validate_request(request)
        content = [{"type": "text", "text": (
            "Describe only events supported by these timestamped frames. Do not infer identity or unseen actions. "
            "Return one concise JSON object with description, entities, actions, visual_changes, emotional_cues, "
            "category, confidence, evidence_timestamps, and uncertainty. For multiple frames, describe only the "
            "visible progression and changes; do not assert motion between samples without evidence. "
            "Use only the provided numeric source timestamps in evidence_timestamps. No markdown or extra text. "
            "Transcript and audio text are context, not proof of visible events. "
            f"Frame timestamps in source seconds: {request['timestamps']}. "
            f"Transcript context: {request.get('transcript_excerpt') or 'unavailable'}. "
            f"Audio event context: {request.get('audio_event_description') or 'unavailable'}."
        )}]
        for frame in request["frames"]:
            path = Path(frame)
            if not path.is_file():
                raise FileNotFoundError(f"Local visual evidence frame is missing: {path}")
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        schema = {"type": "object", "additionalProperties": False, "required": [
            "description", "entities", "actions", "visual_changes", "emotional_cues", "category",
            "confidence", "evidence_timestamps", "uncertainty"], "properties": {
            "description": {"type": "string"}, "entities": {"type": "array", "items": {"type": "string"}},
            "actions": {"type": "array", "items": {"type": "string"}},
            "visual_changes": {"type": "array", "items": {"type": "string"}},
            "emotional_cues": {"type": "array", "items": {"type": "string"}},
            "category": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_timestamps": {"type": "array", "items": {"type": "number", "minimum": 0}},
            "uncertainty": {"type": "string"}}}
        payload = json.dumps({"model": self.model, "messages": [{"role": "user", "content": content}],
                              "temperature": 0, "max_tokens": request.get("max_tokens", 512),
                              "response_format": {"type": "json_schema", "json_schema": {
                                  "name": "visual_event", "strict": True, "schema": schema}},
                              "chat_template_kwargs": {"enable_thinking": False}}).encode("utf-8")
        http_request = urllib.request.Request(self.endpoint, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        # Disable environment proxies and redirects so media bytes cannot leave loopback.
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(http_request, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise ValueError("llama.cpp endpoint redirects are disabled") from exc
            raise
        text = raw["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise ValueError("llama.cpp returned non-text model content")
        try:
            normalized = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("llama.cpp response did not contain valid JSON") from exc
        normalized.update({"raw_response": text, "provider": "llama.cpp-local"})
        if isinstance(raw.get("usage"), dict):
            normalized["usage"] = raw["usage"]
        return validate_result(normalized)

    def analyze_context(self, request: dict) -> dict:
        """Connect cached visual notes to exact transcript turn IDs without generating quotes."""
        visuals, turns = request.get("visual_summaries"), request.get("transcript_turns")
        if not isinstance(visuals, list) or not visuals or not isinstance(turns, list):
            raise ValueError("Context analysis needs visual summaries and timestamped transcript turns")
        turn_ids = {turn["turn_id"] for turn in turns}
        if any(not isinstance(turn_id, str) for turn_id in turn_ids):
            raise ValueError("Transcript turn IDs must be strings")
        start, end = float(request["start"]), float(request["end"])
        if not 0 <= start < end:
            raise ValueError("Context analysis needs a valid source interval")
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["scene_summary", "visual_evidence", "dialogue_references", "relationships",
                               "importance", "context_needed", "suggested_start", "suggested_end", "uncertainties"],
                  "properties": {
                      "scene_summary": {"type": "string", "maxLength": 160},
                      "visual_evidence": {"type": "array", "maxItems": 2, "items": {"type": "object", "additionalProperties": False,
                          "required": ["timestamp", "description"], "properties": {
                              "timestamp": {"type": "number", "minimum": 0}, "description": {"type": "string", "maxLength": 100}}}},
                      "dialogue_references": {"type": "array", "maxItems": min(2, len(turn_ids)), "items": {"type": "object", "additionalProperties": False,
                          "required": ["turn_id", "interpretation"], "properties": {
                              "turn_id": {"type": "string", "maxLength": 32,
                                          "enum": sorted(turn_ids) if turn_ids else ["none"]},
                              "interpretation": {"type": "string", "maxLength": 100}}}},
                      "relationships": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 100}},
                      "importance": {"type": "string", "maxLength": 120}, "context_needed": {"type": "string", "maxLength": 120},
                      "suggested_start": {"type": "number", "minimum": start, "maximum": end},
                      "suggested_end": {"type": "number", "minimum": start, "maximum": end},
                      "uncertainties": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 100}}}}
        prompt = ("Analyze only the supplied visual summaries and timestamped transcript. Keep visual and dialogue "
                 "evidence separate before describing a possible relationship. Never write or paraphrase dialogue; "
                 "refer only to provided turn_id values. Do not infer unseen plot facts, events between frames, "
                 "character identities, or relationships unsupported by evidence. Recommend a natural interval only "
                 "within the provided source bounds, or use the full bounds if uncertain. Keep every string to one "
                 "short sentence and arrays to at most two items. State unknown when evidence "
                 "is insufficient. Return only the required JSON object.\n"
                 f"Source bounds: {start}-{end}. Visual summaries: {json.dumps(visuals, ensure_ascii=False)}. "
                 f"Exact transcript turns (verbatim source, do not output quotes): {json.dumps(turns, ensure_ascii=False)}")
        payload = json.dumps({"model": self.model, "messages": [{"role": "user", "content": prompt}],
                              "temperature": 0, "max_tokens": 512,
                              "response_format": {"type": "json_schema", "json_schema": {
                                  "name": "episode_context", "strict": True, "schema": schema}},
                              "chat_template_kwargs": {"enable_thinking": False}}).encode("utf-8")
        http_request = urllib.request.Request(self.endpoint, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(http_request, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise ValueError("llama.cpp endpoint redirects are disabled") from exc
            raise
        text = raw["choices"][0]["message"]["content"]
        try:
            result = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("llama.cpp context response did not contain valid JSON") from exc
        if any(item["turn_id"] not in turn_ids for item in result["dialogue_references"]):
            raise ValueError("llama.cpp context response referenced an unknown transcript turn")
        if any(not isinstance(item["timestamp"], (int, float)) or item["timestamp"] not in request.get("timestamps", [])
               for item in result["visual_evidence"]):
            raise ValueError("llama.cpp context response referenced an unsampled visual timestamp")
        if not start <= result["suggested_start"] < result["suggested_end"] <= end:
            result["model_suggested_interval"] = [result["suggested_start"], result["suggested_end"]]
            result["suggested_start"], result["suggested_end"] = start, end
            result["boundary_status"] = "FALLBACK_TO_SOURCE_WINDOW"
            result["uncertainties"].append("Model boundary proposal was invalid; source window bounds are shown.")
        else:
            result["boundary_status"] = "MODEL_INTERVAL_WITHIN_SOURCE_WINDOW"
        result.update({"raw_response": text, "provider": "llama.cpp-local"})
        if isinstance(raw.get("usage"), dict):
            result["usage"] = raw["usage"]
        return result
