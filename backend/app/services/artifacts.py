"""Resolve downloadable job artifacts to paths + media types."""

from __future__ import annotations

from pathlib import Path

from ..core.errors import ErrorCode, PipelineError

# kind -> (scope, filename)
_KINDS: dict[str, tuple[str, str]] = {
    "video": ("output", "final.mp4"),
    "cover": ("output", "cover.png"),
    "transcript": ("output", "transcript.json"),
    "log": ("work", "job.log"),
}

_MEDIA_TYPES = {
    "video": "video/mp4",
    "cover": "image/png",
    "transcript": "application/json",
    "log": "text/plain; charset=utf-8",
}


def resolve_job_file(job_id: str, kind: str) -> Path:
    if kind not in _KINDS:
        raise PipelineError(
            ErrorCode.NOT_FOUND, f"Unknown file kind '{kind}'.", stage="files"
        )
    scope, name = _KINDS[kind]
    from ..core.paths import output_dir, work_dir

    base = output_dir(job_id) if scope == "output" else work_dir(job_id)
    path = base / name
    if not path.exists():
        raise PipelineError(
            ErrorCode.NOT_FOUND, f"This job has no {kind} file yet.", stage="files"
        )
    return path


def media_type_for(kind: str) -> str:
    return _MEDIA_TYPES.get(kind, "application/octet-stream")
