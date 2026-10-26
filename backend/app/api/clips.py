"""Shorts/reels endpoints: export start-end ranges of a finished job's final video."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import ErrorCode, PipelineError
from ..deps import get_manager
from ..services import clips as cl
from ..services.artifacts import resolve_job_file

router = APIRouter(prefix="/api/jobs", tags=["clips"])


class ClipSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=80)
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class ClipExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clips: list[ClipSpec] = Field(min_length=1, max_length=cl.MAX_CLIPS)


def _safe_name(name: str, index: int) -> str:
    """Filename-safe label for the download; the stored file is named by index only."""
    cleaned = re.sub(r"[^A-Za-z0-9-_ ]+", "", name).strip().replace(" ", "_")
    return (cleaned[:60] or f"short_{index:02d}") + ".mp4"


@router.post("/{job_id}/clips")
def export_clips(job_id: str, req: ClipExportRequest) -> dict[str, Any]:
    get_manager().get(job_id)  # 404 when the job is unknown
    video = resolve_job_file(job_id, "video")
    duration = cl.probe_duration(video)

    ranges = [(clip.start, clip.end) for clip in req.clips]
    cl.validate_ranges(ranges, duration)

    exported: list[dict[str, Any]] = []
    for index, clip in enumerate(req.clips):
        out = cl.clip_path(job_id, index)
        cl.render_clip_to(video, out, clip.start, clip.end)
        exported.append(
            {
                "index": index,
                "name": clip.name or f"Short {index + 1}",
                "start": round(clip.start, 3),
                "end": round(clip.end, 3),
                "duration": round(clip.end - clip.start, 3),
                "file_url": f"/api/jobs/{job_id}/files/clip/{index}",
                "download_name": _safe_name(clip.name, index),
            }
        )
    return {"clips": exported}


@router.get("/{job_id}/files/clip/{index}")
def get_clip(job_id: str, index: int) -> FileResponse:
    get_manager().get(job_id)
    if index < 0 or index >= cl.MAX_CLIPS:
        raise PipelineError(ErrorCode.NOT_FOUND, "No such clip.", stage="clips")
    path = cl.clip_path(job_id, index)
    if not path.exists():
        raise PipelineError(
            ErrorCode.NOT_FOUND, "This clip has not been exported yet.", stage="clips"
        )
    return FileResponse(path, media_type="video/mp4", filename=f"short_{index:02d}.mp4")
