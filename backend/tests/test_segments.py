"""compute_keep_segments: table-driven tests over synthetic envelopes (phase 2.4)."""

from __future__ import annotations

import numpy as np

from app.services.segments import SegmentParams, compute_keep_segments, silence_mask

HOP = 0.02
DEFAULT = SegmentParams()  # threshold -35, min_silence 0.5, padding 0.12, min_speech 0.25


def env_of(spec: list[tuple[float, float]]) -> tuple[np.ndarray, float]:
    values: list[float] = []
    for length, db in spec:
        values.extend([db] * int(round(length / HOP)))
    return np.asarray(values, dtype=np.float64), len(values) * HOP


def bounds(segments):
    return [(round(s.start, 4), round(s.end, 4)) for s in segments]


def removed_total(segments, duration: float) -> float:
    return duration - sum(s.duration for s in segments)


def test_short_pause_is_kept() -> None:
    env, duration = env_of([(1.0, -10), (0.3, -60), (1.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, DEFAULT)
    assert len(segments) == 1
    assert segments[0].start == 0.0
    assert abs(segments[0].end - duration) < 1e-6


def test_long_pause_is_cut() -> None:
    env, duration = env_of([(1.0, -10), (1.0, -60), (1.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, DEFAULT)
    assert len(segments) == 2
    assert segments[0].start == 0.0
    # padding 0.12 kept on each side: cut is [1.12, 1.88], snapped to 1/30
    assert abs(segments[0].end - 1.12) <= 1 / 30
    assert abs(segments[1].start - 1.88) <= 1 / 30
    assert abs(segments[1].end - 3.0) < 1e-6
    assert abs(removed_total(segments, duration) - 0.76) <= 1 / 30 * 2


def test_padding_is_respected() -> None:
    params = SegmentParams(min_silence_sec=0.5, keep_padding_sec=0.1)
    env, duration = env_of([(1.0, -10), (0.5, -60), (1.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, params)
    removed = removed_total(segments, duration)
    assert abs(removed - 0.3) < 1 / 30 * 2  # 0.5 - 2*0.1


def test_min_silence_exactly_equal_is_cut() -> None:
    params = SegmentParams(min_silence_sec=0.5, keep_padding_sec=0.1)
    env, duration = env_of([(1.0, -10), (0.5, -60), (1.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, params)
    assert len(segments) == 2


def test_hysteresis_keeps_a_brief_dip_as_one_silence() -> None:
    # -33 dB is above the -35 threshold but below the -32 "leave silence" level,
    # so with hysteresis the two -36 runs stay one contiguous silence.
    spec = [(0.5, -10), (0.5, -36), (0.3, -33), (0.5, -36), (0.5, -10)]
    env, duration = env_of(spec)
    segments = compute_keep_segments(env, HOP, duration, 30, DEFAULT)
    assert len(segments) == 2
    removed = removed_total(segments, duration)
    assert 1.0 < removed < 1.12  # one 1.3s silence minus 2x0.12 padding


def test_silence_mask_hysteresis_unit() -> None:
    env = np.array([-10, -36, -33, -36, -10], dtype=float)
    mask = silence_mask(env, threshold_db=-35, hysteresis_db=3)
    assert list(mask) == [False, True, True, True, False]


def test_blip_shorter_than_min_speech_is_dropped() -> None:
    params = SegmentParams(min_speech_sec=0.5)
    env, duration = env_of([(1.0, -10), (1.0, -60), (0.1, -10), (1.0, -60), (1.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, params)
    assert len(segments) == 2  # the 0.34s blip (0.1 + padding) is dropped


def test_blip_never_drops_everything() -> None:
    params = SegmentParams(min_speech_sec=2.0)
    env, duration = env_of([(0.5, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, params)
    assert len(segments) == 1


def test_all_silent_returns_empty() -> None:
    env, duration = env_of([(2.0, -60)])
    assert compute_keep_segments(env, HOP, duration, 30, DEFAULT) == []


def test_no_silence_returns_full_segment() -> None:
    env, duration = env_of([(3.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, DEFAULT)
    assert bounds(segments) == [(0.0, duration)]


def test_negligible_removal_passes_through() -> None:
    # one 0.26s removal in 30.5s is under 1% -> keep the whole file
    env, duration = env_of([(15.0, -10), (0.5, -60), (15.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, DEFAULT)
    assert bounds(segments) == [(0.0, duration)]


def test_boundaries_snap_to_frame_grid() -> None:
    env, duration = env_of([(1.0, -10), (1.0, -60), (1.0, -10)])
    segments = compute_keep_segments(env, HOP, duration, 30, DEFAULT)
    for seg in segments:
        for value in (seg.start, seg.end):
            assert abs(value * 30 - round(value * 30)) < 1e-6


def test_segments_sorted_and_non_overlapping() -> None:
    env, duration = env_of(
        [(0.8, -10), (0.9, -60), (0.7, -10), (0.6, -60), (0.5, -10), (1.2, -60), (0.9, -10)]
    )
    segments = compute_keep_segments(env, HOP, duration, 30, DEFAULT)
    assert len(segments) == 4
    for a, b in zip(segments, segments[1:], strict=False):
        assert a.end <= b.start + 1e-9
        assert b.start >= a.start


def test_empty_envelope_returns_full_segment() -> None:
    assert bounds(compute_keep_segments(np.array([]), HOP, 5.0, 30, DEFAULT)) == [(0.0, 5.0)]
