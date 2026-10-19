"""Diagnostics bundle: system info + job manifest + last log lines, no media (7.3)."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

from ..deps import get_manager
from ..services.ffmpeg import capabilities

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}/diagnostics")
def job_diagnostics(job_id: str) -> Response:
    manager = get_manager()
    job = manager.get(job_id)
    chunks = manager.db.get_chunks(job_id)

    try:
        caps = capabilities()
    except Exception as exc:  # pragma: no cover - ffmpeg missing
        caps = {"error": str(exc)}

    log_path = Path(work_dir_of(job_id)) / "job.log"
    tail = ""
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-200:])

    system = {
        "job": {key: value for key, value in job.items() if key != "options"},
        "chunks": chunks,
        "ffmpeg": caps,
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("diagnostics/system.json", json.dumps(system, indent=1, default=str))
        bundle.writestr("diagnostics/options.json", json.dumps(job["options"], indent=1))
        bundle.writestr("diagnostics/manifest.json", json.dumps(chunks, indent=1))
        bundle.writestr("diagnostics/job_tail.log", tail)

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="cinelume-{job_id}.zip"'},
    )


def work_dir_of(job_id: str) -> Path:
    from ..core.paths import work_dir

    return work_dir(job_id)
