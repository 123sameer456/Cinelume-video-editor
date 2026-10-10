"""Job endpoints: create, list, get, events (SSE), cancel, resume, delete, files."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from ..core.errors import ErrorCode, PipelineError
from ..deps import get_manager
from ..models.job import ChunkProgress, JobCreateRequest, JobRecord, JobStatus
from ..models.options import JobOptions
from ..models.upload import UploadStatus
from ..services import uploads as up
from ..services.artifacts import media_type_for, resolve_job_file

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _record(manager: Any, job: dict[str, Any]) -> JobRecord:
    chunks = [ChunkProgress(**c) for c in manager.db.get_chunks(job["id"])]
    return JobRecord(
        id=job["id"],
        upload_id=job["upload_id"],
        status=JobStatus(job["status"]),
        options=JobOptions(**job["options"]),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        stage=job["stage"],
        overall_pct=job["overall_pct"],
        message=job["message"],
        error=job["error"],
        warnings=job["warnings"],
        chunks=chunks,
    )


@router.post("", response_model=JobRecord, status_code=201)
async def create_job(req: JobCreateRequest) -> JobRecord:
    manager = get_manager()
    info = up.get_upload(req.upload_id)  # raises NOT_FOUND when the id is unknown
    if info["status"] != UploadStatus.COMPLETE:
        raise PipelineError(
            ErrorCode.CONFLICT,
            "The upload is not complete yet.",
            stage="job",
            hint="Finish uploading all parts, then complete the upload.",
        )
    job = manager.submit(req.upload_id, req.options)
    return _record(manager, job)


@router.get("", response_model=list[JobRecord])
async def list_jobs() -> list[JobRecord]:
    manager = get_manager()
    return [_record(manager, job) for job in manager.list_jobs()]


@router.get("/{job_id}", response_model=JobRecord)
async def get_job(job_id: str) -> JobRecord:
    manager = get_manager()
    return _record(manager, manager.get(job_id))


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    manager = get_manager()
    manager.get(job_id)  # 404 before the stream starts

    async def gen() -> AsyncIterator[str]:
        snap = manager.snapshot(job_id)
        yield _sse(snap.model_dump_json())
        if snap.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return
        queue = manager.subscribe(job_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snap = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(snap.model_dump_json())
                if snap.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    break
        finally:
            manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{job_id}/cancel", response_model=JobRecord)
async def cancel_job(job_id: str) -> JobRecord:
    manager = get_manager()
    job = await manager.cancel(job_id)
    return _record(manager, job)


@router.post("/{job_id}/resume", response_model=JobRecord)
async def resume_job(job_id: str) -> JobRecord:
    manager = get_manager()
    job = await manager.resume(job_id)
    return _record(manager, job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    get_manager().delete(job_id)


@router.get("/{job_id}/files/{kind}")
async def get_job_file(job_id: str, kind: str) -> FileResponse:
    get_manager().get(job_id)  # 404 if the job is unknown
    path = resolve_job_file(job_id, kind)
    return FileResponse(path, media_type=media_type_for(kind), filename=path.name)


def _sse(data: str) -> str:
    return f"data: {data}\n\n"
