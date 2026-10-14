"""Benchmark: the same synthetic file through 1, 2 and 4 media workers.

    python -m scripts.benchmark --duration 600 --target 120

Reports wall time and peak RSS per worker count so the defaults can be tuned with real
numbers (phase 5.6). Paste the summary into docs/decisions.md.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import psutil

from app.models.options import JobOptions
from app.pipeline.orchestrator import JobManager
from app.services.probe import probe
from app.services.uploads import add_like_upload
from app.storage.jobs_db import JobsDB
from app.workers.media_pool import create_media_pool

TERMINAL = {"completed", "failed", "cancelled"}


def build_clip(path: Path, duration: float) -> None:
    from conftest import ffmpeg_cmd

    bursts = "+".join(
        f"between(t,{i * 10},{i * 10 + 6})" for i in range(int(duration // 10) + 1)
    )
    ffmpeg_cmd(
        [
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
            "-f", "lavfi", "-i",
            f"aevalsrc='0.6*sin(2*PI*220*t)*({bursts})+0.02*random(0)':s=48000:d={duration}",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ]
    )


def peak_rss_mib() -> float:
    process = psutil.Process()
    total = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            continue
    return total / (1024 * 1024)


async def run_once(clip: Path, workers: int, timeout: float) -> dict[str, float]:
    db = JobsDB(Path(os.environ["DATA_DIR"]) / "jobs.db")
    manager = JobManager(db)
    await manager.start()
    if workers != manager.workers:
        manager._pool.shutdown(wait=False, cancel_futures=True)
        manager._pool = create_media_pool(workers)
    try:
        upload_id = add_like_upload(clip, clip.name)
        options = JobOptions(
            subtitles={"enabled": False},
            performance={
                "chunking": "on",
                "chunk_threshold_sec": 60,
                "target_chunk_sec": 120,
                "workers": workers,
            },
        )
        job = manager.submit(upload_id, options)
        started = time.monotonic()
        while True:
            job = manager.get(job["id"])
            if job["status"] in TERMINAL:
                break
            if time.monotonic() - started > timeout:
                raise SystemExit(f"timeout after {timeout:.0f}s (status {job['status']})")
            await asyncio.sleep(1.0)
        if job["status"] != "completed":
            raise SystemExit(f"job {job['status']}: {job.get('error')}")
        return {"seconds": time.monotonic() - started, "peak_rss_mib": peak_rss_mib()}
    finally:
        await manager.shutdown()
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--counts", type=str, default="1,2,4")
    parser.add_argument("--timeout", type=float, default=3600.0)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="cinelume_bench_"))
    clip = tmp / "bench.mp4"
    print(f"building a {args.duration:.0f}s synthetic clip ...", flush=True)
    build_clip(clip, args.duration)
    print(f"clip: {probe(clip).duration:.1f}s, {clip.stat().st_size / 1e6:.1f} MB", flush=True)

    print(f"{'workers':>8} {'seconds':>9} {'speedup':>8} {'peak RSS MiB':>13}")
    baseline = None
    try:
        for count in [int(c) for c in args.counts.split(",")]:
            data_dir = tmp / f"data_w{count}"
            data_dir.mkdir(parents=True, exist_ok=True)
            os.environ["DATA_DIR"] = str(data_dir)
            from app.core.config import get_settings

            get_settings.cache_clear()
            get_settings().ensure_dirs()

            result = asyncio.run(run_once(clip, count, args.timeout))
            baseline = baseline or result["seconds"]
            print(
                f"{count:>8} {result['seconds']:>9.1f} "
                f"{baseline / result['seconds']:>7.2f}x {result['peak_rss_mib']:>13.0f}",
                flush=True,
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("paste this table into docs/decisions.md", flush=True)


if __name__ == "__main__":
    main()
