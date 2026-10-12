"""Music library endpoints: list, upload, delete, preview."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from ..services import music as music_service
from ..services.music import source_path

router = APIRouter(prefix="/api/music", tags=["music"])


@router.get("")
def list_tracks() -> dict:
    return {"tracks": music_service.list_tracks()}


@router.post("", status_code=201)
async def upload_track(request: Request) -> dict:
    filename = request.headers.get("x-filename") or "track.mp3"
    content_type = request.headers.get("content-type", "")
    if "filename=" in content_type:
        raw = content_type.split("filename=")[-1].strip('"')
        if raw:
            filename = raw
    return await music_service.add_track_stream(filename, request.stream())


@router.get("/{track_id}/file")
def download_track(track_id: str) -> FileResponse:
    path = source_path(track_id)
    return FileResponse(path, filename=path.name)


@router.delete("/{track_id}", status_code=204)
def delete_track(track_id: str) -> None:
    music_service.delete_track(track_id)


@router.get("/{track_id}")
def get_track(track_id: str) -> dict:
    return music_service.get_track(track_id)
