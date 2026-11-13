"""Conservative playback coverage tracking for blind episode review."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageSample:
    media_ms: int
    monotonic_s: float
    state: str
    rate: float = 1.0


class CoverageTracker:
    """Count only adjacent samples consistent with continuous playback."""

    POLL_INTERVAL_MS = 250
    MAX_SAMPLE_GAP_S = 0.8
    CLOCK_TOLERANCE_MS = 180

    def __init__(self, intervals: list[tuple[int, int]] | None = None):
        self.intervals = list(intervals or [])
        self._previous: CoverageSample | None = None
        self.break_reason: str | None = None

    @property
    def chronological_frontier_ms(self) -> int:
        frontier = 0
        for start, end in sorted(self.intervals):
            if start > frontier:
                break
            frontier = max(frontier, end)
        return frontier

    def reset_baseline(self) -> None:
        self._previous = None

    def seed(self, sample: CoverageSample) -> None:
        self._previous = sample if sample.state == "playing" and sample.media_ms >= 0 else None
        self.break_reason = None

    def break_continuity(self, reason: str) -> None:
        self._previous = None
        self.break_reason = reason

    def sample(self, sample: CoverageSample) -> tuple[int, int] | None:
        previous = self._previous
        valid_state = sample.state in {"playing", "ended"}
        self._previous = sample if sample.state == "playing" and sample.media_ms >= 0 else None
        self.break_reason = None
        if previous is None or not valid_state or sample.media_ms < 0:
            return None
        wall_ms = (sample.monotonic_s - previous.monotonic_s) * 1000.0
        media_delta = sample.media_ms - previous.media_ms
        if wall_ms <= 0 or wall_ms > self.MAX_SAMPLE_GAP_S * 1000 or media_delta <= 0:
            self.break_reason = "non_contiguous_sample"
            self._previous = sample if sample.state == "playing" and sample.media_ms >= 0 else None
            return None
        expected = wall_ms * sample.rate
        if sample.rate <= 0 or abs(media_delta - expected) > max(self.CLOCK_TOLERANCE_MS, expected * 0.25):
            self.break_reason = "playback_discontinuity"
            self._previous = sample
            return None
        interval = (previous.media_ms, sample.media_ms)
        if interval[0] > self.chronological_frontier_ms:
            self.break_reason = "out_of_order_interval"
            return None
        self.add_interval(*interval)
        return interval

    def add_interval(self, start_ms: int, end_ms: int) -> None:
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("Coverage intervals must have non-negative increasing bounds")
        rows = sorted([*self.intervals, (start_ms, end_ms)])
        merged: list[tuple[int, int]] = []
        for start, end in rows:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        self.intervals = merged

    def gaps(self, duration_ms: int) -> list[tuple[int, int]]:
        if duration_ms <= 0:
            return []
        gaps: list[tuple[int, int]] = []
        cursor = 0
        for start, end in self.intervals:
            start, end = max(0, start), min(duration_ms, end)
            if start > cursor:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < duration_ms:
            gaps.append((cursor, duration_ms))
        return gaps

    def watched_ms(self, duration_ms: int) -> int:
        return max(0, duration_ms - sum(end - start for start, end in self.gaps(duration_ms)))

    def percent(self, duration_ms: int) -> float:
        return 0.0 if duration_ms <= 0 else 100.0 * self.watched_ms(duration_ms) / duration_ms

    def complete(self, duration_ms: int) -> bool:
        return duration_ms > 0 and not self.gaps(duration_ms)
