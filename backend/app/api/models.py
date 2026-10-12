"""Whisper model endpoints: list, download (SSE progress), cancel."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import ErrorCode, PipelineError
from ..services.transcribe import APPROX_MODEL_GB, get_model_manager, list_models

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def get_models() -> dict:
    return {"models": list_models()}


@router.post("/{size}/download")
async def start_download(size: str) -> dict:
    return get_model_manager().start(size)


@router.post("/{size}/cancel")
async def cancel_download(size: str) -> dict:
    if size not in APPROX_MODEL_GB:
        raise PipelineError(ErrorCode.NOT_FOUND, f"Unknown model size '{size}'.", stage="models")
    return get_model_manager().cancel(size)


@router.get("/{size}/events")
async def download_events(size: str, request: Request) -> StreamingResponse:
    if size not in APPROX_MODEL_GB:
        raise PipelineError(ErrorCode.NOT_FOUND, f"Unknown model size '{size}'.", stage="models")
    manager = get_model_manager()
    terminal = {"done", "failed", "cancelled"}

    async def gen():
        while True:
            detail = manager.detail(size)
            yield f"data: {json.dumps(detail)}\n\n"
            if detail["status"] in terminal:
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
