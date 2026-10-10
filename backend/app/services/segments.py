"""Keep-segments algorithm: turn an RMS envelope into the list of ranges to keep.

Pure function, no I/O - heavily unit-tested (phase 2.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

# A silence must be cut only if it is at least this long, etc. - all from options.
_DEFAULT_HYSTERESIS_DB = 3.0
_MIN_CUT_FRACTION = 0.01  # below ~1% removed, keep the whole file untouched
_EPS = 1e-6


class Segment(NamedTuple):
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class SegmentParams:
    threshold_db: float = -35.0
    min_silence_sec: float = 0.5
    keep_padding_sec: float = 0.12
    min_speech_sec: float = 0.25
    hysteresis_db: float = _DEFAULT_HYSTERESIS_DB


def silence_mask(envelope: np.ndarray, threshold_db: float, hysteresis_db: float) -> np.ndarray:
    """Frames below threshold are silent; leaving silence needs +hysteresis_db."""
    env = np.asarray(envelope, dtype=np.float64)
    mask = np.zeros(len(env), dtype=bool)
    if env.size == 0:
        return mask
    leave_level = threshold_db + hysteresis_db
    in_silence = bool(env[0] < threshold_db)
    for i, value in enumerate(env):
        if in_silence:
            if value > leave_level:
                in_silence = False
        elif value < threshold_db:
            in_silence = True
        mask[i] = in_silence
    return mask


def compute_keep_segments(
    envelope: np.ndarray,
    hop_sec: float,
    duration: float,
    fps: float,
    params: SegmentParams,
) -> list[Segment]:
    """Return non-overlapping ranges to keep, in seconds, snapped to the frame grid.

    `hop_sec` is the envelope step in SECONDS (one value per `hop_sec` of audio).
    """
    duration = float(duration)
    if duration <= 0:
        return []
    env = np.asarray(envelope, dtype=np.float64)
    if env.size == 0:
        return [Segment(0.0, duration)]
    hop_sec = float(hop_sec)
    if hop_sec <= 0:
        hop_sec = duration / max(1, len(env))

    mask = silence_mask(env, params.threshold_db, params.hysteresis_db)
    if float(mask.mean()) >= 0.99:
        # Essentially silent everywhere: nothing to keep. The caller raises NO_SPEECH.
        return []
    removed = _long_silences(mask, hop_sec, params)
    kept = _complement(removed, duration)
    kept = _drop_short(kept, params.min_speech_sec)
    kept = _snap_and_clamp(kept, fps, duration)

    if not kept:
        return []
    removed_total = duration - sum(seg.duration for seg in kept)
    if removed_total < _MIN_CUT_FRACTION * duration:
        return [Segment(0.0, duration)]
    return kept


def _long_silences(
    mask: np.ndarray, hop_sec: float, params: SegmentParams
) -> list[tuple[float, float]]:
    """Silent runs >= min_silence_sec, trimmed by keep_padding_sec on each side."""
    removed: list[tuple[float, float]] = []
    n = len(mask)
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        run_len = (j - i) * hop_sec
        if run_len >= params.min_silence_sec:
            cut_start = i * hop_sec + params.keep_padding_sec
            cut_end = j * hop_sec - params.keep_padding_sec
            if cut_end - cut_start > _EPS:
                removed.append((cut_start, cut_end))
        i = j
    return removed


def _complement(removed: list[tuple[float, float]], duration: float) -> list[Segment]:
    if not removed:
        return [Segment(0.0, duration)]
    removed = sorted(removed)
    merged: list[list[float]] = [list(removed[0])]
    for start, end in removed[1:]:
        if start <= merged[-1][1] + _EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    kept: list[Segment] = []
    cursor = 0.0
    for start, end in merged:
        start = max(0.0, start)
        end = min(duration, end)
        if start - cursor > _EPS:
            kept.append(Segment(cursor, start))
        cursor = max(cursor, end)
    if duration - cursor > _EPS:
        kept.append(Segment(cursor, duration))
    return kept


def _drop_short(kept: list[Segment], min_speech_sec: float) -> list[Segment]:
    if min_speech_sec <= 0 or not kept:
        return kept
    filtered = [seg for seg in kept if seg.duration >= min_speech_sec]
    if filtered:
        return filtered
    # Never drop everything: keep the longest remaining piece.
    return [max(kept, key=lambda seg: seg.duration)]


def _snap_and_clamp(kept: list[Segment], fps: float, duration: float) -> list[Segment]:
    if not kept:
        return []
    if fps <= 0:
        fps = 30.0
    out: list[Segment] = []
    cursor = 0.0
    for seg in sorted(kept):
        start = round(seg.start * fps) / fps
        end = round(seg.end * fps) / fps
        start = max(start, cursor)
        end = min(end, duration)
        if end - start <= _EPS:
            continue
        out.append(Segment(start, end))
        cursor = end
    return out
