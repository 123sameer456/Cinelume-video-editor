"""TTL cleanup for uploads and work dirs."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ..storage.jobs_db import JobsDB
from .config import Settings
from .logging import get_logger

log = get_logger("cinelume.housekeeping")

_ACTIVE_STATUSES = {"queued", "running", "interrupted"}


def run_housekeeping(db: JobsDB, settings: Settings) -> dict[str, int]:
    """Delete uploads and work dirs past their TTL, skipping active jobs."""
    now = time.time()
    upload_ttl = settings.upload_ttl_hours * 3600
    work_ttl = settings.work_ttl_hours * 3600

    protected_uploads: set[str] = set()
    active_jobs: set[str] = set()
    for job in db.list_jobs():
        if job["status"] in _ACTIVE_STATUSES:
            protected_uploads.add(job["upload_id"])
            active_jobs.add(job["id"])

    removed = {"uploads": 0, "work": 0}
    removed["uploads"] = _sweep(
        settings.uploads_dir, upload_ttl, now, skip=protected_uploads
    )
    removed["work"] = _sweep(settings.work_dir, work_ttl, now, skip=active_jobs)
    if removed["uploads"] or removed["work"]:
        log.info(f"housekeeping removed {removed}")
    return removed


def _sweep(base: Path, ttl_seconds: float, now: float, skip: set[str]) -> int:
    if not base.exists():
        return 0
    from ..services.uploads import _delete_tree

    count = 0
    for entry in base.iterdir():
        if not entry.is_dir() or entry.name in skip:
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age > ttl_seconds:
            _delete_tree(entry)
            count += 1
    return count


async def housekeeping_loop(db: JobsDB, settings: Settings, interval: float = 3600.0) -> None:
    while True:
        try:
            run_housekeeping(db, settings)
        except Exception:  # pragma: no cover - never let the task die
            log.exception("housekeeping failed")
        await asyncio.sleep(interval)
