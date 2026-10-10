"""Time-range chunk descriptor used by every stage (rule G10: one chunk is not special)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkSpec:
    index: int
    src_start: float
    src_end: float
    hard_boundary: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.src_end - self.src_start)


def whole_file_chunk(duration: float) -> ChunkSpec:
    """Phase 2/3 use a single chunk covering the whole file."""
    end = max(0.0, float(duration))
    return ChunkSpec(index=0, src_start=0.0, src_end=end)
