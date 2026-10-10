"""Runtime singletons (DB + JobManager) so route handlers stay thin and testable."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline.orchestrator import JobManager
    from .storage.jobs_db import JobsDB

_db: JobsDB | None = None
_manager: JobManager | None = None


def set_db(db: JobsDB) -> None:
    global _db
    _db = db


def get_db() -> JobsDB:
    if _db is None:
        raise RuntimeError("Database is not initialised.")
    return _db


def set_manager(manager: JobManager) -> None:
    global _manager
    _manager = manager


def get_manager() -> JobManager:
    if _manager is None:
        raise RuntimeError("Job manager is not initialised.")
    return _manager
