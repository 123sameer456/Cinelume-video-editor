"""Chunked upload storage: init / parts / assemble / validate / thumbnail.

Parts are streamed straight to disk (never buffered) and the assembled file is
validated by probing, not by extension (G1, G6).
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from ..core.config import get_settings
from ..core.errors import ErrorCode, PipelineError
from ..core.paths import ensure_dir, upload_dir, upload_meta_path, upload_parts_dir
from ..models.media import MediaInfo
from ..models.upload import PART_SIZE, UploadStatus
from .ffmpeg import run_ffmpeg
from .probe import probe, validate_video_upload

_EXT_RE = re.compile(r"[^A-Za-z0-9]+")
_SAFE_EXT = re.compile(r"^[A-Za-z0-9]{1,5}$")
MAX_PARTS = 100_000
# Sanity ceiling for a single part (the recommended size is PART_SIZE = 8 MiB).
MAX_PART_BYTES = PART_SIZE * 8


def _part_path(upload_id: str, part_no: int) -> Path:
    return upload_parts_dir(upload_id) / f"{part_no:06d}.part"


def _part_tmp_path(upload_id: str, part_no: int) -> Path:
    return upload_parts_dir(upload_id) / f"{part_no:06d}.part.partial"


def _read_meta(upload_id: str) -> dict[str, Any]:
    meta_path = upload_meta_path(upload_id)
    if not meta_path.exists():
        raise PipelineError(ErrorCode.NOT_FOUND, "Upload not found.", stage="upload")
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(ErrorCode.NOT_FOUND, "Upload metadata is unreadable.") from exc


def _write_meta(upload_id: str, meta: dict[str, Any]) -> None:
    meta_path = upload_meta_path(upload_id)
    meta["updated_at"] = time.time()
    tmp = meta_path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(meta_path)


def require_free_space(path: Path, needed_bytes: int, stage: str) -> None:
    try:
        free = shutil.disk_usage(str(path)).free
    except OSError:
        return
    if free < needed_bytes:
        raise PipelineError(
            ErrorCode.DISK_SPACE,
            f"Not enough free disk space ({free / 1e9:.1f} GB free, "
            f"{needed_bytes / 1e9:.1f} GB needed).",
            stage=stage,
            hint="Free up space and try again.",
        )


def _safe_ext(filename: str) -> str:
    suffix = Path(filename).suffix.lstrip(".")
    if suffix and _SAFE_EXT.match(suffix):
        return suffix.lower()
    return "bin"


def init_upload(filename: str, total_size: int) -> dict[str, Any]:
    settings = get_settings()
    if total_size > settings.max_upload_bytes:
        raise PipelineError(
            ErrorCode.LIMIT_EXCEEDED,
            f"File is larger than the {settings.max_upload_gb:.0f} GB upload limit.",
            stage="upload",
        )
    upload_id = uuid.uuid4().hex
    ensure_dir(upload_parts_dir(upload_id))
    require_free_space(settings.uploads_dir, total_size, "upload")

    meta = {
        "upload_id": upload_id,
        "filename": filename,
        "size_bytes": total_size,
        "status": UploadStatus.INITIATED.value,
        "ext": _safe_ext(filename),
        "part_size": PART_SIZE,
        "created_at": time.time(),
        "media": None,
        "warnings": [],
    }
    _write_meta(upload_id, meta)
    return {
        "upload_id": upload_id,
        "part_size": PART_SIZE,
        "received_parts": [],
    }


def begin_part(upload_id: str, part_no: int) -> tuple[Path, Path]:
    """Validate a part number and return (tmp_path, final_path).

    The part number is not bounded by the advertised part size - clients may pick
    any part size; the assembled total is verified at complete time.
    """
    _read_meta(upload_id)  # raises NOT_FOUND when the upload is unknown
    if part_no < 0 or part_no >= MAX_PARTS:
        raise PipelineError(
            ErrorCode.BAD_REQUEST, f"Invalid part number {part_no}.", stage="upload"
        )
    ensure_dir(upload_parts_dir(upload_id))
    return _part_tmp_path(upload_id, part_no), _part_path(upload_id, part_no)


def commit_part(tmp_path: Path, final_path: Path, written: int) -> None:
    if written > MAX_PART_BYTES:
        tmp_path.unlink(missing_ok=True)
        raise PipelineError(
            ErrorCode.BAD_REQUEST,
            f"Part is larger than the {MAX_PART_BYTES} byte limit.",
            stage="upload",
        )
    tmp_path.replace(final_path)


def save_part(upload_id: str, part_no: int, stream: BinaryIO) -> int:
    """Synchronous variant used by tests/tools. The API streams asynchronously."""
    tmp_path, final_path = begin_part(upload_id, part_no)
    written = 0
    try:
        with open(tmp_path, "wb") as fh:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                fh.write(block)
                written += len(block)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise PipelineError(ErrorCode.DISK_SPACE, f"Failed to write upload part: {exc}") from exc
    commit_part(tmp_path, final_path, written)
    return written


def received_parts(upload_id: str) -> list[int]:
    parts_dir = upload_parts_dir(upload_id)
    if not parts_dir.exists():
        return []
    numbers: list[int] = []
    for p in parts_dir.glob("*.part"):
        try:
            numbers.append(int(p.stem))
        except ValueError:
            continue
    return sorted(numbers)


def _iter_parts(upload_id: str, total_parts: int) -> Iterator[Path]:
    parts_dir = upload_parts_dir(upload_id)
    for n in range(total_parts):
        yield parts_dir / f"{n:06d}.part"


def complete_upload(upload_id: str, total_parts: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    meta = _read_meta(upload_id)
    have = received_parts(upload_id)
    if not have:
        raise PipelineError(ErrorCode.BAD_REQUEST, "No upload parts were received.", stage="upload")
    if total_parts is None:
        total_parts = max(have) + 1
    missing = [n for n in range(total_parts) if n not in have]
    if missing:
        raise PipelineError(
            ErrorCode.BAD_REQUEST,
            f"Upload is missing part(s): {missing[:10]}{'...' if len(missing) > 10 else ''}",
            stage="upload",
            hint="Re-upload the missing parts, then complete again.",
        )

    expected_total = int(meta.get("size_bytes", 0))
    require_free_space(settings.uploads_dir, expected_total, "upload")

    ext = meta.get("ext") or "bin"
    source = upload_dir(upload_id) / f"source.{ext}"
    tmp_source = source.with_suffix(f".{ext}.partial")
    assembled = 0
    try:
        with open(tmp_source, "wb") as out:
            for part in _iter_parts(upload_id, total_parts):
                with open(part, "rb") as inp:
                    while True:
                        block = inp.read(4 * 1024 * 1024)
                        if not block:
                            break
                        out.write(block)
                        assembled += len(block)
    except OSError as exc:
        tmp_source.unlink(missing_ok=True)
        raise PipelineError(ErrorCode.DISK_SPACE, f"Failed to assemble upload: {exc}") from exc

    if assembled != expected_total:
        tmp_source.unlink(missing_ok=True)
        raise PipelineError(
            ErrorCode.BAD_REQUEST,
            f"Assembled size {assembled} does not match the declared size {expected_total}.",
            stage="upload",
            hint="Re-upload the file; a part may have been truncated.",
        )
    tmp_source.replace(source)

    info = probe(source)
    try:
        validate_video_upload(info, settings.max_duration_sec)
    except PipelineError:
        source.unlink(missing_ok=True)
        meta["status"] = UploadStatus.FAILED.value
        _write_meta(upload_id, meta)
        raise

    thumbnail = generate_thumbnail(source, upload_dir(upload_id) / "thumb.jpg", info.duration)

    shutil.rmtree(upload_parts_dir(upload_id), ignore_errors=True)

    meta["status"] = UploadStatus.COMPLETE.value
    meta["media"] = info.model_dump()
    meta["warnings"] = info.warnings
    meta["source"] = source.name
    meta["thumbnail"] = thumbnail is not None
    _write_meta(upload_id, meta)
    return get_upload(upload_id)


def generate_thumbnail(source: Path, out_path: Path, duration: float) -> Path | None:
    """Extract one frame around 1s (or the middle for very short clips)."""
    if duration <= 0:
        seek = 0.0
    elif duration < 2:
        seek = duration / 2
    else:
        seek = min(1.0, duration / 2)
    try:
        run_ffmpeg(
            [
                "-ss",
                f"{seek:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                "-q:v",
                "3",
                "-y",
                str(out_path),
            ],
            cwd=out_path.parent,
            timeout=60,
            log_path=out_path.parent / "thumbnail.log",
        )
    except PipelineError:
        return None
    return out_path if out_path.exists() else None


def get_upload(upload_id: str) -> dict[str, Any]:
    meta = _read_meta(upload_id)
    media = MediaInfo(**meta["media"]) if meta.get("media") else None
    return {
        "upload_id": upload_id,
        "filename": meta.get("filename", ""),
        "size_bytes": int(meta.get("size_bytes", 0)),
        "status": UploadStatus(meta.get("status", UploadStatus.INITIATED.value)),
        "received_parts": received_parts(upload_id),
        "media": media,
        "thumbnail_url": f"/api/uploads/{upload_id}/thumbnail"
        if meta.get("thumbnail")
        else None,
        "warnings": meta.get("warnings", []),
    }


def source_path(upload_id: str) -> Path:
    meta = _read_meta(upload_id)
    name = meta.get("source")
    if not name:
        candidate = next(upload_dir(upload_id).glob("source.*"), None)
        if candidate is None:
            raise PipelineError(
                ErrorCode.NOT_FOUND, "Upload has no assembled source file.", stage="upload"
            )
        return candidate
    return upload_dir(upload_id) / name


def thumbnail_path(upload_id: str) -> Path | None:
    path = upload_dir(upload_id) / "thumb.jpg"
    return path if path.exists() else None


def delete_upload(upload_id: str) -> bool:
    directory = upload_dir(upload_id)
    if not directory.exists():
        return False
    _delete_tree(directory)
    return True


def _delete_tree(path: Path, attempts: int = 3) -> None:
    """Windows antivirus can lock files briefly; retry with backoff."""
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if i == attempts - 1:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.2 * (i + 1))
