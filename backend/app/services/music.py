"""Music library: upload, validate by probing, measure loudness, pre-normalize (4.1).

Each track is normalized once to a reference loudness and cached as `normalized.wav`,
so the `volume` option behaves the same across tracks. No music is bundled here: the
user is responsible for having the rights to the tracks they upload.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from ..core.config import get_settings
from ..core.errors import ErrorCode, PipelineError
from ..core.paths import ensure_dir
from .ffmpeg import parse_loudnorm, run_ffmpeg, run_ffmpeg_capture
from .probe import probe
from .uploads import _delete_tree, require_free_space

REFERENCE_LUFS = -20.0
_TRUE_PEAK = -1.5
_LRA = 7.0
# Micro-fade at both edges of the cached track, so looping it never clicks.
_LOOP_FADE_SEC = 0.01
_ALLOWED_EXT = {"mp3", "wav", "m4a", "ogg", "flac", "aac", "opus", "wma", "mka"}



def music_root() -> Path:
    return get_settings().music_dir


def track_dir(track_id: str) -> Path:
    return music_root() / track_id


def _meta_path(track_id: str) -> Path:
    return track_dir(track_id) / "meta.json"


def _read_meta(track_id: str) -> dict[str, Any]:
    path = _meta_path(track_id)
    if not path.exists():
        raise PipelineError(ErrorCode.NOT_FOUND, "Music track not found.", stage="music")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(ErrorCode.NOT_FOUND, "Music metadata is unreadable.") from exc


def _write_meta(track_id: str, meta: dict[str, Any]) -> None:
    path = _meta_path(track_id)
    tmp = path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _measure_loudness(path: Path) -> dict[str, float]:
    """Run loudnorm's measurement pass and parse its JSON report."""
    stderr = run_ffmpeg_capture(
        ["-i", str(path), "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        cwd=path.parent,
        timeout=max(300.0, 6 * 60.0),
        log_path=path.parent / "loudnorm.log",
        loglevel="info",
    )
    return parse_loudnorm(stderr)


def _audio_duration(path: Path) -> float:
    try:
        info = probe(path)
    except PipelineError:
        return 0.0
    return info.duration


def validate_music_upload(info) -> None:
    """Raise when the file is not usable audio."""
    if info.has_video and not info.has_audio:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "That file is a video, not an audio track.",
            stage="music",
            hint="Upload mp3, wav, m4a, ogg or flac.",
        )
    if not info.has_audio:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "No audio stream found in the music file.",
            stage="music",
        )
    if info.duration <= 0:
        raise PipelineError(ErrorCode.UNSUPPORTED_MEDIA, "The music file has zero duration.")


def _store(
    track_id: str, source: Path, name: str, meta_extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    measured = _measure_loudness(source)
    duration = _audio_duration(source)
    meta = {
        "id": track_id,
        "name": name,
        "source": source.name,
        "size_bytes": source.stat().st_size,
        "duration": duration,
        "integrated_lufs": measured.get("input_i"),
        "created_at": time.time(),
        **(meta_extra or {}),
    }
    _write_meta(track_id, meta)
    return get_track(track_id)


def _prepare(filename: str) -> tuple[str, Path, Path, Path, str]:
    """Create the track dir and return (id, dir, tmp_path, target_path, ext)."""
    track_id = uuid.uuid4().hex
    directory = ensure_dir(track_dir(track_id))
    ext = Path(filename).suffix.lstrip(".").lower() or "bin"
    if ext not in _ALLOWED_EXT:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA,
            f"'{ext}' is not a supported audio format.",
            stage="music",
            hint="Use mp3, wav, m4a, ogg or flac.",
        )
    target = directory / f"source.{ext}"
    tmp = target.with_suffix(f".{ext}.partial")
    return track_id, directory, tmp, target, ext


def _finish(
    track_id: str, directory: Path, tmp: Path, target: Path, name: str, written: int
) -> dict[str, Any]:
    if written == 0:
        _delete_tree(directory)
        raise PipelineError(ErrorCode.BAD_REQUEST, "Empty upload.", stage="music")
    require_free_space(get_settings().music_dir, written, "music")
    tmp.replace(target)
    try:
        info = probe(target)
        validate_music_upload(info)
    except PipelineError:
        _delete_tree(directory)
        raise
    return _store(track_id, target, name)


def add_track(filename: str, stream: BinaryIO) -> dict[str, Any]:
    """Synchronous variant used by tests/tools; the API streams asynchronously."""
    track_id, directory, tmp, target, _ext = _prepare(filename)
    written = 0
    try:
        with open(tmp, "wb") as handle:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                handle.write(block)
                written += len(block)
    except OSError as exc:
        _delete_tree(directory)
        raise PipelineError(ErrorCode.DISK_SPACE, f"Failed to store the track: {exc}") from exc
    return _finish(track_id, directory, tmp, target, Path(filename).name, written)


async def add_track_stream(filename: str, stream) -> dict[str, Any]:
    """Stream a multipart body to disk (never buffered whole in RAM)."""
    track_id, directory, tmp, target, _ext = _prepare(filename)
    written = 0
    try:
        with open(tmp, "wb") as handle:
            async for block in stream:
                if not block:
                    continue
                handle.write(block)
                written += len(block)
    except OSError as exc:
        _delete_tree(directory)
        raise PipelineError(ErrorCode.DISK_SPACE, f"Failed to store the track: {exc}") from exc
    return _finish(track_id, directory, tmp, target, Path(filename).name, written)


def list_tracks() -> list[dict[str, Any]]:
    root = music_root()
    if not root.exists():
        return []
    tracks = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not _meta_path(directory.name).exists():
            continue
        try:
            tracks.append(get_track(directory.name))
        except PipelineError:
            continue
    return tracks


def get_track(track_id: str) -> dict[str, Any]:
    meta = _read_meta(track_id)
    normalized = normalized_path(track_id)
    return {
        "id": meta["id"],
        "name": meta.get("name", ""),
        "duration": meta.get("duration", 0.0),
        "integrated_lufs": meta.get("integrated_lufs"),
        "size_bytes": meta.get("size_bytes", 0),
        "normalized": normalized is not None,
        "file_url": f"/api/music/{track_id}/file",
    }


def source_path(track_id: str) -> Path:
    meta = _read_meta(track_id)
    return track_dir(track_id) / meta["source"]


def normalized_path(track_id: str) -> Path | None:
    path = track_dir(track_id) / "normalized.wav"
    return path if path.exists() else None


def normalize_track(track_id: str) -> Path:
    """Pre-normalize once to the reference loudness and cache it (click-free looping)."""
    cached = normalized_path(track_id)
    if cached is not None:
        return cached
    source = source_path(track_id)
    meta = _read_meta(track_id)
    duration = float(meta.get("duration", 0.0)) or _audio_duration(source)
    measured = _measure_loudness(source)
    out = track_dir(track_id) / "normalized.wav"

    if measured.get("input_i", -100.0) <= -70.0:
        # Silent track: normalization is meaningless, keep it as-is.
        run_ffmpeg(
            ["-i", str(source), "-c:a", "pcm_s16le", "-ar", "48000", "-y", str(out)],
            cwd=out.parent,
            timeout=600,
            log_path=out.parent / "loudnorm.log",
        )
    else:
        filters = (
            f"loudnorm=I={REFERENCE_LUFS}:TP={_TRUE_PEAK}:LRA={_LRA}"
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured.get('target_offset', 0.0)}"
            ":linear=true"
        )
        if duration > _LOOP_FADE_SEC * 2:
            filters += (
                f",afade=t=in:st=0:d={_LOOP_FADE_SEC}"
                f",afade=t=out:st={max(0.0, duration - _LOOP_FADE_SEC):.4f}:d={_LOOP_FADE_SEC}"
            )
        run_ffmpeg(
            [
                "-i",
                str(source),
                "-af",
                filters,
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-y",
                str(out),
            ],
            cwd=out.parent,
            timeout=600,
            log_path=out.parent / "loudnorm.log",
        )
    if not out.exists():
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED, "Could not normalize the track.", stage="music"
        )
    return out


def delete_track(track_id: str) -> bool:
    directory = track_dir(track_id)
    if not directory.exists():
        return False
    _delete_tree(directory)
    return True


def clear_partial_cache(track_id: str) -> None:
    path = track_dir(track_id) / "normalized.wav.partial"
    if path.exists():
        path.unlink(missing_ok=True)
