"""GET /api/fonts."""

from __future__ import annotations

from fastapi import APIRouter

from ..services.fonts import list_fonts

router = APIRouter(prefix="/api/fonts", tags=["fonts"])


@router.get("")
def get_fonts() -> dict[str, list[dict[str, str]]]:
    return {"fonts": list_fonts()}
