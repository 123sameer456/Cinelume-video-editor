"""Dummy pipeline (phase 1).

Simulates the A -> B -> C chunk stages with sleeps and one *real* ffmpeg call per
chunk, so progress streaming, cancel, and restart can be verified before any real
editing exists. Replaced by the real stage runners in phases 2-5.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from ..core.errors import ErrorCode, PipelineError
from ..models.options import ChunkingMode, JobOptions
from ..services.ffmpeg import run_ffmpeg

# Stage sequence within a chunk, matching pipeline/progress.py weights.
STAGE_SEQUENCE = ("audio", "transcribe", "video")
STEPS_PER_STAGE = 5
STEP_SLEEP = 0.18


def plan_dummy_chunks(duration: float, options: JobOptions) -> list[dict[str, Any]]:
    """Fake chunk plan. Chunking OFF -> one chunk; else 3..12 evenly sized chunks."""
    if options.performance.chunking is ChunkingMode.OFF or duration <= 0:
        count = 1
    else:
        target = max(1, options.performance.target_chunk_sec)
        count = max(3, min(12, math.ceil(duration / target)))
    step = duration / count if count else duration
    chunks: list[dict[str, Any]] = []
    for i in range(count):
        start = i * step
        end = duration if i == count - 1 else (i + 1) * step
        chunks.append(
            {
                "index": i,
                "src_start": round(start, 3),
                "src_end": round(end, 3),
                "hard_boundary": False,
            }
        )
    return chunks


def _send(progress_queue: Any, payload: dict[str, Any]) -> None:
    try:
        progress_queue.put(payload)
    except Exception:  # pragma: no cover - manager queue should not fail
        pass


def run_dummy_chunk(
    job_id: str,
    index: int,
    src_start: float,
    src_end: float,
    source_path: str,
    workdir: str,
    cancel_event: Any,
    progress_queue: Any,
    log_path: str,
) -> int:
    """Runs in a spawned worker process. Must stay module-level importable."""
    chunk_dir = Path(workdir) / f"chunk_{index:03d}"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, src_end - src_start)
    stage = STAGE_SEQUENCE[0]

    try:
        for stage in STAGE_SEQUENCE:
            for step in range(STEPS_PER_STAGE):
                if cancel_event.is_set():
                    raise PipelineError(
                        ErrorCode.CANCELLED, "Job cancelled.", stage=stage, chunk=index
                    )
                time.sleep(STEP_SLEEP)
                _send(
                    progress_queue,
                    {"index": index, "stage": stage, "stage_pct": (step + 1) / STEPS_PER_STAGE},
                )
            if stage == "video":
                # One real ffmpeg call per chunk proves workers, progress and cancel.
                mid = src_start + duration / 2
                run_ffmpeg(
                    [
                        "-ss",
                        f"{mid:.3f}",
                        "-i",
                        source_path,
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=320:-2",
                        "-q:v",
                        "3",
                        "-y",
                        str(chunk_dir / "frame.jpg"),
                    ],
                    cwd=chunk_dir,
                    timeout=60,
                    cancel_event=cancel_event,
                    log_path=Path(log_path),
                )
        _send(
            progress_queue,
            {"index": index, "stage": "video", "stage_pct": 1.0, "status": "done"},
        )
        return index
    except PipelineError as exc:
        _send(
            progress_queue,
            {"index": index, "stage": stage, "status": "failed", "error": exc.to_dict()},
        )
        raise
