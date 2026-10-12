"""Cover endpoints: render/re-roll a cover for an upload without running a job."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import ErrorCode, PipelineError
from ..models.options import CoverOptions, JobOptions
from ..services import uploads as up
from ..services.cover import render_cover

router = APIRouter(prefix="/api/cover", tags=["cover"])


class CoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    options: CoverOptions = Field(default_factory=CoverOptions)


def _cover_path(upload_id: str):
    directory = up.upload_dir(upload_id)
    for suffix in ("png", "jpg"):
        candidate = directory / f"cover.{suffix}"
        if candidate.exists():
            return candidate
    return None


@router.post("")
def create_cover(req: CoverRequest) -> dict:
    # 404 with a clear message when the upload was already deleted.
    source = up.source_path(req.upload_id)
    out_dir = up.upload_dir(req.upload_id)
    result = render_cover(source, JobOptions(cover=req.options), out_dir / "cover.png")
    return {
        "time_sec": result.time_sec,
        "image_url": f"/api/cover/{req.upload_id}",
        "warnings": result.warnings,
    }


@router.get("/{upload_id}")
def get_cover(upload_id: str) -> FileResponse:
    up.get_upload(upload_id)
    path = _cover_path(upload_id)
    if path is None:
        raise PipelineError(
            ErrorCode.NOT_FOUND, "No cover has been generated for this upload.", stage="cover"
        )
    return FileResponse(path, media_type=f"image/{path.suffix.lstrip('.')}")
