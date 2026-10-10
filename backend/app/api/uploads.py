"""Chunked upload endpoints: init / part / complete / info / thumbnail / delete."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from ..core.errors import ErrorCode, PipelineError
from ..models.upload import (
    UploadCompleteRequest,
    UploadInfo,
    UploadInitRequest,
    UploadInitResponse,
)
from ..services import uploads as up

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("", response_model=UploadInitResponse, status_code=201)
async def init_upload(req: UploadInitRequest) -> UploadInitResponse:
    data = up.init_upload(req.filename, req.total_size)
    return UploadInitResponse(**data)


@router.put("/{upload_id}/parts/{part_no}", status_code=204)
async def put_part(upload_id: str, part_no: int, request: Request) -> None:
    tmp_path, final_path = up.begin_part(upload_id, part_no)
    written = 0
    try:
        with open(tmp_path, "wb") as fh:
            async for block in request.stream():
                if not block:
                    continue
                fh.write(block)
                written += len(block)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise PipelineError(
            ErrorCode.DISK_SPACE, f"Failed to write upload part: {exc}", stage="upload"
        ) from exc
    up.commit_part(tmp_path, final_path, written)


@router.post("/{upload_id}/complete", response_model=UploadInfo)
async def complete_upload(upload_id: str, req: UploadCompleteRequest) -> UploadInfo:
    return UploadInfo(**up.complete_upload(upload_id, req.total_parts))


@router.get("/{upload_id}", response_model=UploadInfo)
async def get_upload(upload_id: str) -> UploadInfo:
    return UploadInfo(**up.get_upload(upload_id))


@router.get("/{upload_id}/thumbnail")
async def get_thumbnail(upload_id: str) -> FileResponse:
    path = up.thumbnail_path(upload_id)
    if path is None:
        raise PipelineError(ErrorCode.NOT_FOUND, "No thumbnail for this upload.", stage="upload")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/{upload_id}", status_code=204)
async def delete_upload(upload_id: str) -> None:
    up.delete_upload(upload_id)
