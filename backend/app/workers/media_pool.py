"""Worker pool sizing and process pools (spawn on Windows)."""

from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

import psutil

# Rough RAM we want to keep free per concurrent media worker (bytes).
RESERVE_PER_WORKER = 1_500_000_000


def cpu_count() -> int:
    return os.cpu_count() or 2


def compute_worker_count(requested: int | None = None) -> int:
    """clamp(cpu_count // 2, 1, 4), reduced further when free RAM is low."""
    cpu = cpu_count()
    if requested:
        workers = requested
    else:
        workers = max(1, min(4, cpu // 2))
    try:
        available = psutil.virtual_memory().available
        while workers > 1 and available / workers < RESERVE_PER_WORKER:
            workers -= 1
    except Exception:  # pragma: no cover - psutil should always work
        pass
    return workers


def ffmpeg_threads(workers: int) -> int:
    return max(1, cpu_count() // max(1, workers))


def create_media_pool(workers: int) -> ProcessPoolExecutor:
    ctx = multiprocessing.get_context("spawn")
    return ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
