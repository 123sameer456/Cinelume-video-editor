"""GET /api/system - ffmpeg capabilities, hardware, cpu/ram, limits."""

from __future__ import annotations

import asyncio
import os

import psutil
from fastapi import APIRouter

from .. import __version__
from ..core.config import get_settings
from ..services.ffmpeg import capabilities
from ..services.fonts import list_fonts
from ..workers.media_pool import compute_worker_count

router = APIRouter(prefix="/api/system", tags=["system"])


def cuda_available(caps: dict) -> bool:
    try:
        import ctranslate2  # type: ignore

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return "h264_nvenc" in caps.get("hardware_encoders", [])


@router.get("")
async def get_system() -> dict:
    settings = get_settings()
    caps = await asyncio.get_running_loop().run_in_executor(None, capabilities)
    vm = psutil.virtual_memory()
    return {
        "app": "Cinelume",
        "version": __version__,
        "ffmpeg": caps,
        "cpu": {"count": os.cpu_count() or 1},
        "ram": {"total": vm.total, "available": vm.available},
        "cuda": cuda_available(caps),
        "workers": compute_worker_count(),
        "models": [],
        "fonts": list_fonts(),
        "limits": {
            "max_upload_gb": settings.max_upload_gb,
            "max_duration_min": settings.max_duration_min,
            "max_concurrent_jobs": settings.max_concurrent_jobs,
        },
    }
