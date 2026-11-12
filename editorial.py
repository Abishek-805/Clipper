"""Optional local narrative assessment; heuristic scores remain separate."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

PROMPT_VERSION = "editorial-v1"
CATEGORIES = {"COMEDY", "ACTION", "DRAMA", "SUSPENSE", "CLIFFHANGER", "EMOTIONAL",
              "CONFRONTATION", "ROMANCE", "REVEAL", "TRANSFORMATION", "MYSTERY", "OTHER", "UNCERTAIN"}
WEIGHTS = {"hook": .20, "coherence": .20, "payoff": .20, "impact": .15,
           "self_containment": .10, "visual_clarity": .10, "audio_clarity": .05}
QUALITY_FLOOR = .58


def local_endpoint(value: str | None = None) -> str | None:
    """Only permit explicit loopback OpenAI-compatible endpoints."""
    endpoint = (value if value is not None else os.getenv("CLIPPER_LOCAL_LLM_ENDPOINT", "")).strip()
    if not endpoint:
        return None
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Narrative model endpoint must be an HTTP loopback URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Narrative model endpoint cannot contain credentials, query parameters or fragments")
    if not parsed.path.rstrip("/").endswith("/v1/chat/completions"):
        raise ValueError("Use the local server's OpenAI-compatible /v1/chat/completions endpoint")
    return endpoint


def transcript_windows(words: list, start: float, end: float, max_words: int = 180, overlap: int = 30) -> list[dict]:
    selected = [(i, word) for i, word in enumerate(words) if word.end > start and word.start < end]
    if not selected:
        return []
    windows, step = [], max(1, max_words - overlap)
    for offset in range(0, len(selected), step):
        batch = selected[offset:offset + max_words]
        windows.append({"first_word_index": batch[0][0], "last_word_index": batch[-1][0],
                       "text": " ".join(word.text for _, word in batch)})
        if offset + max_words >= len(selected):
            break
    return windows


def validate_assessment(value: dict, words: list, start: float, end: float) -> dict:
    if not isinstance(value, dict) or value.get("category") not in CATEGORIES:
        raise ValueError("Invalid editorial category")
    dimensions = value.get("scores")
    if not isinstance(dimensions, dict):
        raise ValueError("Editorial scores are missing")
    scores = {}
    for key in WEIGHTS:
        score = dimensions.get(key)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 5:
            raise ValueError(f"Invalid editorial score: {key}")
        scores[key] = round(float(score), 2)
    first, last = value.get("first_word_index"), value.get("last_word_index")
    if first is not None or last is not None:
        if (isinstance(first, bool) or isinstance(last, bool) or not isinstance(first, int) or
                not isinstance(last, int) or first < 0 or last < first or last >= len(words) or
                words[first].start < start - .25 or words[last].end > end + .25):
            raise ValueError("Editorial word indices do not fit the candidate")
    for key in ("hook", "build", "payoff"):
        if not isinstance(value.get(key, ""), str) or len(value.get(key, "")) > 500:
            raise ValueError(f"Invalid editorial {key} description")
    overall = sum(scores[key] * weight for key, weight in WEIGHTS.items()) / 5
    confidence = value.get("confidence", 0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("Invalid editorial confidence")
    secondary = value.get("secondary_category")
    if secondary is not None and secondary not in CATEGORIES:
        raise ValueError("Invalid secondary editorial category")
    rejection_reasons = value.get("rejection_reasons", [])
    if not isinstance(rejection_reasons, list) or any(not isinstance(item, str) or len(item) > 300 for item in rejection_reasons):
        raise ValueError("Invalid editorial rejection reasons")
    return {"category": value["category"], "secondary_category": secondary,
            "scores": scores, "editorial_score": round(overall, 4), "confidence": round(float(confidence), 3),
            "hook": value.get("hook", ""), "build": value.get("build", ""), "payoff": value.get("payoff", ""),
            "first_word_index": first, "last_word_index": last,
            "rejection_reasons": rejection_reasons, "prompt_version": PROMPT_VERSION}


def assess_candidate(endpoint: str, model: str, words: list, start: float, end: float,
                     language: str | None) -> dict:
    windows = transcript_windows(words, start, end)
    relevant = [(i, w) for i, w in enumerate(words) if w.end > start and w.start < end]
    if not relevant:
        raise ValueError("Text model cannot assess a candidate without transcript words")
    transcript = " ".join(f"[{i}] {word.text}" for i, word in relevant)
    prompt = ("Assess this fictional-video highlight as an editor. Do not infer visuals from text. "
              "Return JSON only with category, secondary_category, scores (hook,coherence,payoff,impact,"
              "self_containment,visual_clarity,audio_clarity: each 0..5), confidence 0..1, first_word_index, "
              "last_word_index, hook, build, payoff, rejection_reasons. Use UNCERTAIN if evidence is weak. "
              "A cliffhanger can have intentional unresolved payoff. Do not inflate scores. "
              f"Language: {language or 'unknown'}. Candidate {start:.3f}-{end:.3f}s. "
              f"Overlapping transcript-window metadata: {json.dumps(windows, ensure_ascii=False)}\n{transcript}")
    body = json.dumps({"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}],
                       "response_format": {"type": "json_object"}}).encode()
    request = urllib.request.Request(endpoint, body, {"Content-Type": "application/json"})
    class LoopbackRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urlparse(newurl)
            if target.scheme != "http" or target.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Local model server redirected outside loopback")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    try:
        with urllib.request.build_opener(LoopbackRedirect).open(request, timeout=90) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Local narrative model request failed: {exc}") from exc
    content = data["choices"][0]["message"]["content"]
    return validate_assessment(json.loads(content), words, start, end)


def apply_assessment(candidate: dict, assessment: dict) -> dict:
    candidate["editorial"] = assessment
    candidate["editorial_assessed"] = True
    candidate["editorial_selected"] = assessment["editorial_score"] >= QUALITY_FLOOR and not assessment["rejection_reasons"]
    return candidate
