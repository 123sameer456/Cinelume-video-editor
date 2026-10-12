"""Chunk planner (phase 5.1).

Long videos are split at natural silences; a short video is one chunk through the same
code path (rule G10). The silence map is coarse and computed from audio only.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..core.errors import ErrorCode, PipelineError
from ..models.chunk import ChunkSpec
from ..models.media import MediaInfo
from ..models.options import ChunkingMode, JobOptions
from ..services.envelope import Envelope, rms_envelope_db
from ..services.ffmpeg import run_ffmpeg

# A boundary looks for a silence at least this long.
MIN_BOUNDARY_SILENCE_SEC = 0.3
# The last chunk shorter than this fraction of the target is merged into the previous one.
TINY_LAST_CHUNK_FRACTION = 0.3


def coarse_envelope(
    source: Path, workdir: Path, sample_rate: int = 8000, hop_sec: float = 0.05
) -> Envelope:
    """Low-rate mono envelope of the whole file: seconds even for hours of video."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    wav = workdir / "coarse_audio.wav"
    run_ffmpeg(
        [
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(wav),
        ],
        cwd=workdir,
        timeout=1800,
        log_path=workdir / "ffmpeg.log",
    )
    return rms_envelope_db(wav, hop_sec)


def plan_chunks(
    source: Path,
    options: JobOptions,
    info: MediaInfo,
    workdir: Path,
    fps: int,
) -> list[ChunkSpec]:
    performance = options.performance
    duration = info.duration
    if duration <= 0:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA, "The video has zero duration.", stage="chunker"
        )

    if performance.chunking is ChunkingMode.OFF or duration <= performance.chunk_threshold_sec:
        chunks = [ChunkSpec(index=0, src_start=0.0, src_end=duration)]
    else:
        env = coarse_envelope(source, workdir)
        boundaries = find_boundaries(
            env, env.hop_sec, duration, performance, fps, options.silence.threshold_db
        )
        chunks = chunks_from_boundaries(boundaries, duration)
        chunks = merge_tiny_last(chunks, performance.target_chunk_sec)
        chunks = [
            ChunkSpec(
                index=i, src_start=c.src_start, src_end=c.src_end, hard_boundary=c.hard_boundary
            )
            for i, c in enumerate(chunks)
        ]

    write_manifest(workdir, chunks)
    return chunks


def _snap(value: float, fps: float) -> float:
    if fps <= 0:
        return value
    return round(value * fps) / fps


def find_boundaries(
    env: Envelope | "np.ndarray",
    hop_sec: float,
    duration: float,
    performance,
    fps: float,
    threshold_db: float,
) -> list[tuple[float, bool]]:
    """Target boundaries snapped to silences; `hard=True` when no silence was found."""
    values = env.values if hasattr(env, "values") else env
    boundaries: list[tuple[float, bool]] = []
    target = performance.target_chunk_sec
    window = performance.boundary_window_sec
    index = 1
    while index * target < duration:
        point = index * target
        low = max(0.0, point - window)
        high = min(duration, point + window)
        silence = best_silence(values, hop_sec, low, high, threshold_db)
        if silence is not None:
            start, end = silence
            boundary = _snap((start + end) / 2, fps)
            hard = False
        else:
            boundary = _snap(lowest_energy_point(env.values, hop_sec, low, high), fps)
            hard = True
        boundaries.append((boundary, hard))
        index += 1
    return boundaries


def best_silence(
    env_values, hop_sec: float, low: float, high: float, threshold_db: float
) -> tuple[float, float] | None:
    """Longest silent run >= MIN_BOUNDARY_SILENCE_SEC inside the window."""
    import numpy as np

    values = np.asarray(env_values, dtype=float)
    if values.size == 0:
        return None
    first = max(0, int(low / hop_sec))
    last = min(len(values), max(first + 1, int(high / hop_sec) + 1))
    window = values[first:last]
    if window.size == 0:
        return None

    best: tuple[float, float] | None = None
    best_len = 0.0
    run_start: int | None = None
    for i, value in enumerate(window):
        if value < threshold_db:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None:
            length = (i - run_start) * hop_sec
            if length >= MIN_BOUNDARY_SILENCE_SEC and length > best_len:
                best_len = length
                best = ((first + run_start) * hop_sec, (first + i) * hop_sec)
            run_start = None
    if run_start is not None:
        length = (len(window) - run_start) * hop_sec
        if length >= MIN_BOUNDARY_SILENCE_SEC and length > best_len:
            best = ((first + run_start) * hop_sec, (first + len(window)) * hop_sec)
    return best


def lowest_energy_point(env_values, hop_sec: float, low: float, high: float) -> float:
    """The 100 ms point with the lowest RMS in the window (a word may be cut)."""
    import numpy as np

    values = np.asarray(env_values, dtype=float)
    if values.size == 0 or high <= low:
        return low
    first = max(0, int(low / hop_sec))
    last = min(len(values), max(first + 1, int(high / hop_sec) + 1))
    window = values[first:last]
    if window.size == 0:
        return low
    block = max(1, int(round(0.1 / hop_sec)))
    means = [float(np.mean(window[i : i + block])) for i in range(0, len(window), block)]
    position = int(np.argmin(means)) if means else 0
    return min(high, max(low, (first + position * block) * hop_sec))


def chunks_from_boundaries(
    boundaries: list[tuple[float, bool]], duration: float
) -> list[ChunkSpec]:
    chunks: list[ChunkSpec] = []
    start = 0.0
    for boundary, hard in boundaries:
        if not (start < boundary < duration):
            continue
        chunks.append(
            ChunkSpec(index=len(chunks), src_start=start, src_end=boundary, hard_boundary=hard)
        )
        start = boundary
    if start < duration:
        chunks.append(ChunkSpec(index=len(chunks), src_start=start, src_end=duration))
    if not chunks:
        chunks = [ChunkSpec(index=0, src_start=0.0, src_end=duration)]
    return chunks


def merge_tiny_last(chunks: list[ChunkSpec], target_chunk_sec: int) -> list[ChunkSpec]:
    if len(chunks) > 1 and chunks[-1].duration < TINY_LAST_CHUNK_FRACTION * target_chunk_sec:
        last = chunks.pop()
        previous = chunks.pop()
        chunks.append(
            ChunkSpec(
                index=previous.index,
                src_start=previous.src_start,
                src_end=last.src_end,
                hard_boundary=previous.hard_boundary,
            )
        )
    return chunks


def write_manifest(workdir: Path, chunks: list[ChunkSpec]) -> Path:
    manifest = Path(workdir) / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    **asdict(chunk),
                    "status": "pending",
                    "cut_duration": chunk.duration,
                    "attempts": 0,
                    "files": {},
                }
                for chunk in chunks
            ],
            indent=1,
        ),
        encoding="utf-8",
    )
    return manifest


def read_manifest(workdir: Path) -> list[dict]:
    path = Path(workdir) / "manifest.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
