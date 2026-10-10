"""Duration-weighted progress math (rule 7: never by step count)."""

from __future__ import annotations

from typing import Any

# Stage weights within a chunk (phase 5.4). Master/merge is the 0.10 tail.
STAGE_WEIGHTS: dict[str, float] = {"audio": 0.15, "transcribe": 0.35, "video": 0.40}
TAIL_WEIGHT = 0.10


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


class ProgressTracker:
    """Aggregates per-chunk, per-stage fractions into one duration-weighted percentage."""

    def __init__(self, chunks: list[dict[str, Any]], weights: dict[str, float] | None = None):
        self.weights = weights or STAGE_WEIGHTS
        self.entries: dict[int, dict[str, Any]] = {}
        for c in chunks:
            duration = float(c.get("duration") or 1.0)
            self.entries[c["index"]] = {
                "index": c["index"],
                "duration": max(duration, 0.01),
                "status": c.get("status", "pending"),
                "stage": None,
                "stage_pct": 0.0,
                "stages": {},
            }
        self.total_duration = sum(e["duration"] for e in self.entries.values()) or 1.0
        self.tail_pct = 0.0

    def set_stage(self, index: int, stage: str | None, pct: float) -> None:
        entry = self.entries.get(index)
        if not entry:
            return
        if stage:
            entry["stage"] = stage
            entry["stages"][stage] = max(0.0, min(1.0, pct))
            entry["stage_pct"] = max(0.0, min(1.0, pct))
        if entry["status"] == "pending":
            entry["status"] = "running"

    def set_status(self, index: int, status: str) -> None:
        entry = self.entries.get(index)
        if entry:
            entry["status"] = status
            if status == "done":
                for stage in self.weights:
                    entry["stages"][stage] = 1.0
                entry["stage_pct"] = 1.0

    def set_tail(self, pct: float) -> None:
        self.tail_pct = max(0.0, min(1.0, pct))

    def _chunk_fraction(self, entry: dict[str, Any]) -> float:
        if entry["status"] == "done":
            return 1.0
        return sum(self.weights.get(stage, 0.0) * frac for stage, frac in entry["stages"].items())

    def overall_pct(self) -> float:
        done = sum(e["duration"] * self._chunk_fraction(e) for e in self.entries.values())
        media = (done / self.total_duration) * (1.0 - TAIL_WEIGHT)
        return _clamp((media + TAIL_WEIGHT * self.tail_pct) * 100.0)
