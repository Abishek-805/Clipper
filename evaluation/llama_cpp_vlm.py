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
            "Return JSON with description, entities, actions, visual_changes, emotional_cues, category, confidence, "
            "evidence_timestamps, uncertainty. Transcript and audio text are context, not proof of visible events. "
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
        payload = json.dumps({"model": self.model, "messages": [{"role": "user", "content": content}],
                              "temperature": 0, "max_tokens": 512}).encode("utf-8")
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
        return validate_result(normalized)
