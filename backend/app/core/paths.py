"""Filesystem layout helpers. All paths are derived from ids, never user text."""

from __future__ import annotations

from pathlib import Path

from .config import get_settings
from .errors import ErrorCode, PipelineError


def upload_dir(upload_id: str) -> Path:
    return get_settings().uploads_dir / upload_id


def upload_parts_dir(upload_id: str) -> Path:
    return upload_dir(upload_id) / "parts"


def upload_meta_path(upload_id: str) -> Path:
    return upload_dir(upload_id) / "meta.json"


def work_dir(job_id: str) -> Path:
    return get_settings().work_dir / job_id


def chunk_dir(job_id: str, index: int) -> Path:
    return work_dir(job_id) / f"chunk_{index:03d}"


def output_dir(job_id: str) -> Path:
    return get_settings().outputs_dir / job_id


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_child(parent: Path, name: str) -> Path:
    """Join `name` under `parent`, rejecting anything that escapes it (path traversal)."""
    parent = parent.resolve()
    candidate = (parent / name).resolve()
    if candidate != parent and parent not in candidate.parents:
        raise PipelineError(
            ErrorCode.BAD_REQUEST,
            "Invalid path.",
            hint="The requested path escapes its allowed directory.",
        )
    return candidate
