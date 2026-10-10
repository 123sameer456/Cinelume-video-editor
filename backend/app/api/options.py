"""GET /api/options/defaults - the server is the source of truth for defaults."""

from __future__ import annotations

from fastapi import APIRouter

from ..models.options import JobOptions

router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/defaults", response_model=JobOptions)
def get_defaults() -> JobOptions:
    return JobOptions()
