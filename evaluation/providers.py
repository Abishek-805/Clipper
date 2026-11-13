"""Provider-neutral visual event contract and deterministic test provider."""
from __future__ import annotations

import math
from typing import Protocol


class VisualEventAnalyzer(Protocol):
    def analyze(self, request: dict) -> dict: ...


def validate_request(request: dict) -> dict:
    if not isinstance(request, dict) or not isinstance(request.get("frames"), list) or not request["frames"]:
        raise ValueError("A visual analysis request needs sampled frames")
    timestamps = request.get("timestamps")
    if not isinstance(timestamps, list) or len(timestamps) != len(request["frames"]):
        raise ValueError("Each sampled frame needs a source timestamp")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
           for value in timestamps):
        raise ValueError("Frame timestamps must be numeric source times")
    for key in ("transcript_excerpt", "audio_event_description"):
        if request.get(key) is not None and not isinstance(request[key], str):
            raise ValueError(f"{key} must be text or null")
    return request


def validate_result(result: dict) -> dict:
    required = {"description", "entities", "actions", "visual_changes", "emotional_cues",
                "category", "confidence", "evidence_timestamps", "uncertainty", "raw_response", "provider"}
    if not isinstance(result, dict) or not required <= result.keys():
        raise ValueError("Visual result is missing normalized fields or raw response")
    if not isinstance(result["raw_response"], (str, dict)):
        raise ValueError("raw_response must preserve provider output")
    if not isinstance(result["confidence"], (int, float)) or not 0 <= result["confidence"] <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if any(not isinstance(result[key], list) for key in ("entities", "actions", "visual_changes", "emotional_cues", "evidence_timestamps")):
        raise ValueError("Normalized event collections must be arrays")
    if any(not isinstance(result[key], str) for key in ("description", "category", "uncertainty", "provider")):
        raise ValueError("Normalized event text fields must be strings")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
           for value in result["evidence_timestamps"]):
        raise ValueError("Evidence timestamps must be nonnegative finite source times")
    return result


class MockVisualEventAnalyzer:
    """Returns an explicitly simulated result; never call this model inference."""
    def analyze(self, request: dict) -> dict:
        validate_request(request)
        timestamp = request["timestamps"][len(request["timestamps"]) // 2]
        result = {"description": "Simulated scene description for contract tests only.", "entities": [],
                  "actions": [], "visual_changes": [], "emotional_cues": [], "category": "UNCERTAIN",
                  "confidence": 0.0, "evidence_timestamps": [timestamp],
                  "uncertainty": "Mock provider; no pixels were semantically analyzed.",
                  "raw_response": {"mock": True, "frame_count": len(request["frames"])},
                  "provider": "deterministic-mock"}
        return validate_result(result)
