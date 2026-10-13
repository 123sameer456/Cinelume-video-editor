"""Chunk planner (phase 5.1): boundaries, silences, hard fallbacks, tiny last chunk."""

from __future__ import annotations

import numpy as np

from app.models.chunk import ChunkSpec
from app.services.chunker import (
    best_silence,
    chunks_from_boundaries,
    find_boundaries,
    lowest_energy_point,
    merge_tiny_last,
    plan_chunks,
    read_manifest,
)


class _Perf:
    def __init__(self, target=10.0, window=2.0):
        self.target_chunk_sec = target
        self.boundary_window_sec = window
        self.chunking = "on"
        self.chunk_threshold_sec = 60


def envelope_of(spec: list[tuple[float, float]], hop_sec=0.05):
    values: list[float] = []
    for length, db in spec:
        values.extend([db] * int(round(length / hop_sec)))
    return np.asarray(values, dtype=float)


def test_best_silence_finds_the_longest_run() -> None:
    env = envelope_of([(1.0, -10), (0.5, -60), (0.4, -10), (1.0, -60), (1.0, -10)])
    silence = best_silence(env, 0.05, 0.0, 5.0, -35)
    assert silence is not None
    start, end = silence
    assert 1.8 <= start <= 2.0 and 2.8 <= end <= 3.0


def test_best_silence_returns_none_when_too_short() -> None:
    env = envelope_of([(1.0, -10), (0.2, -60), (1.0, -10)])
    assert best_silence(env, 0.05, 0.0, 3.0, -35) is None


def test_lowest_energy_point() -> None:
    env = envelope_of([(1.0, -10), (0.4, -60), (1.0, -10)])
    point = lowest_energy_point(env, 0.05, 0.5, 2.5)
    assert 0.9 <= point <= 1.6


def test_boundaries_snap_to_silences() -> None:
    # 30s: tone bursts with silences around 10s and 20s
    env = envelope_of(
        [(9.5, -10), (1.0, -60), (9.5, -10), (1.0, -60), (9.0, -10)]
    )
    boundaries = find_boundaries(env, 0.05, 30.0, _Perf(target=10.0, window=3.0), 30, -35)
    assert len(boundaries) == 2
    assert all(not hard for _, hard in boundaries)
    assert 9.0 <= boundaries[0][0] <= 11.0
    assert 19.0 <= boundaries[1][0] <= 21.0


def test_boundaries_hard_when_no_silence() -> None:
    env = envelope_of([(30.0, -10)])
    boundaries = find_boundaries(env, 0.05, 30.0, _Perf(target=10.0, window=3.0), 30, -35)
    assert len(boundaries) == 2
    assert all(hard for _, hard in boundaries)


def test_chunks_from_boundaries() -> None:
    chunks = chunks_from_boundaries([(10.0, False), (20.0, False)], 30.0)
    assert [(c.src_start, c.src_end) for c in chunks] == [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]


def test_tiny_last_chunk_is_merged() -> None:
    chunks = [
        ChunkSpec(0, 0.0, 10.0),
        ChunkSpec(1, 10.0, 20.0),
        ChunkSpec(2, 20.0, 22.0),  # 2s of a 10s target -> merged
    ]
    merged = merge_tiny_last(chunks, target_chunk_sec=10)
    assert len(merged) == 2
    assert merged[-1].src_end == 22.0


def test_plan_chunks_single_for_short_videos(tmp_path, make_talking_video) -> None:
    from app.models.options import JobOptions
    from app.services.probe import probe

    video = make_talking_video(duration=4.0)
    info = probe(video)
    chunks = plan_chunks(video, JobOptions(), info, tmp_path, 30)
    assert len(chunks) == 1
    assert (chunks[0].src_start, chunks[0].src_end) == (0.0, info.duration)
    manifest = read_manifest(tmp_path)
    assert len(manifest) == 1


def test_plan_chunks_splits_long_videos(tmp_path) -> None:
    from app.models.options import JobOptions
    from app.services.probe import probe
    from conftest import ffmpeg_cmd

    video = tmp_path / "long.mp4"
    ffmpeg_cmd(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
            "-f", "lavfi", "-i",
            "aevalsrc='0.5*sin(2*PI*220*t)*(between(t,9,19)+between(t,19,29))':s=48000:d=40",
            "-t", "40",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(video),
        ]
    )
    options = JobOptions(
        performance={"chunking": "on", "chunk_threshold_sec": 60, "target_chunk_sec": 30}
    )
    info = probe(video)
    chunks = plan_chunks(video, options, info, tmp_path, 30)
    assert len(chunks) >= 3
    assert chunks[0].src_start == 0.0
    assert abs(chunks[-1].src_end - info.duration) < 0.1
    assert len(read_manifest(tmp_path)) == len(chunks)
