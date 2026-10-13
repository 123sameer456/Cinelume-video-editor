"""Spawn-safe worker entry points for stages A/B/C (phase 5.2).

Every argument is picklable (manager proxies for cancel/progress), every function is
module-level, and each stage uses `.done` markers + per-stage cache keys so a resume
skips finished work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _send(progress_queue: Any, payload: dict[str, Any]) -> None:
    if progress_queue is None:
        return
    try:
        progress_queue.put(payload)
    except Exception:  # pragma: no cover - the manager queue should not fail
        pass


def _progress_reporter(progress_queue: Any, index: int, stage: str, scale: float, offset: float):
    def report(pct: float) -> None:
        _send(
            progress_queue,
            {"index": index, "stage": stage, "stage_pct": min(1.0, offset + pct / 100.0 * scale)},
        )

    return report


def stage_audio_entry(
    index: int,
    src_start: float,
    src_end: float,
    hard_boundary: bool,
    source: str,
    workdir: str,
    options_json: str,
    info_json: str,
    cancel_event: Any,
    progress_queue: Any,
    log_path: str,
) -> dict[str, Any]:
    """Stage A: extract -> denoise -> envelope -> keep-segments -> cut audio."""
    from ..core.errors import PipelineError
    from ..models.chunk import ChunkSpec
    from ..models.media import MediaInfo
    from ..models.options import JobOptions
    from ..pipeline.cache import read_done_marker, stage_key, write_done_marker
    from ..services.render import playback_fps
    from .stages import stage_audio

    options = JobOptions(**json.loads(options_json))
    info = MediaInfo(**json.loads(info_json))
    chunk = ChunkSpec(
        index=index, src_start=src_start, src_end=src_end, hard_boundary=hard_boundary
    )
    chunk_dir = Path(workdir) / f"chunk_{index:03d}"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    key = stage_key("audio", options, [source])
    if read_done_marker(chunk_dir, "audio") == key:
        data = json.loads((chunk_dir / "audio_stage.json").read_text(encoding="utf-8"))
        _send(
            progress_queue,
            {"index": index, "stage": "audio", "stage_pct": 1.0, "status": "done"},
        )
        return {**data, "cached": True}

    _send(progress_queue, {"index": index, "stage": "audio", "stage_pct": 0.0, "status": "running"})
    on_progress = _progress_reporter(progress_queue, index, "audio", 0.9, 0.0)
    try:
        result = stage_audio(
            Path(workdir),
            chunk,
            Path(source),
            options,
            info,
            playback_fps(options, info),
            cancel_event=cancel_event,
            on_progress=on_progress,
            log=None,
        )
    except PipelineError as exc:
        _send(
            progress_queue,
            {"index": index, "stage": "audio", "status": "failed", "error": exc.to_dict()},
        )
        raise

    payload = {
        "segments": [[segment.start, segment.end] for segment in result.segments],
        "threshold_db": result.threshold_db,
        "removed_sec": result.removed_sec,
        "output_duration": result.output_duration,
        "warnings": result.warnings,
        "has_audio": result.has_audio,
    }
    (chunk_dir / "audio_stage.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    write_done_marker(chunk_dir, "audio", key)
    _send(
        progress_queue,
        {"index": index, "stage": "audio", "stage_pct": 1.0, "status": "done"},
    )
    return {**payload, "cached": False}


def stage_transcribe_entry(
    index: int,
    audio_cut: str,
    language: str | None,
    workdir: str,
    options_json: str,
    cancel_event: Any,
    progress_queue: Any,
    log_path: str,
) -> dict[str, Any]:
    """Stage B: transcribe the already-cut audio (model already loaded in this worker)."""
    from ..core.errors import PipelineError
    from ..models.options import JobOptions
    from ..pipeline.cache import read_done_marker, stage_key, write_done_marker
    from ..workers.asr_pool import transcribe_chunk

    options = JobOptions(**json.loads(options_json))
    chunk_dir = Path(workdir) / f"chunk_{index:03d}"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    key = stage_key("transcribe", options, [audio_cut])
    if read_done_marker(chunk_dir, "transcribe") == key:
        words = json.loads((chunk_dir / "words.json").read_text(encoding="utf-8"))
        _send(
            progress_queue,
            {"index": index, "stage": "transcribe", "stage_pct": 1.0, "status": "done"},
        )
        return {"words": words, "cached": True}

    _send(
        progress_queue,
        {"index": index, "stage": "transcribe", "stage_pct": 0.0, "status": "running"},
    )
    try:
        result = transcribe_chunk(audio_cut, language, options_json, str(chunk_dir), index)
    except PipelineError as exc:
        _send(
            progress_queue,
            {"index": index, "stage": "transcribe", "status": "failed", "error": exc.to_dict()},
        )
        raise
    write_done_marker(chunk_dir, "transcribe", key)
    _send(
        progress_queue,
        {"index": index, "stage": "transcribe", "stage_pct": 1.0, "status": "done"},
    )
    return {**result, "cached": False}


def stage_video_entry(
    index: int,
    src_start: float,
    src_end: float,
    source: str,
    audio_dn: str | None,
    workdir: str,
    options_json: str,
    info_json: str,
    segments_json: str,
    cancel_event: Any,
    progress_queue: Any,
    log_path: str,
) -> dict[str, Any]:
    """Stage C: one video pass from the ORIGINAL file, burning the ASS subtitles."""
    from ..core.errors import PipelineError
    from ..models.chunk import ChunkSpec
    from ..models.media import MediaInfo
    from ..models.options import JobOptions
    from ..pipeline.cache import read_done_marker, stage_key, write_done_marker
    from ..services.denoise import target_channels
    from ..services.render import build_placed, playback_fps, render_video
    from ..services.segments import Segment

    options = JobOptions(**json.loads(options_json))
    info = MediaInfo(**json.loads(info_json))
    chunk = ChunkSpec(index=index, src_start=src_start, src_end=src_end)
    chunk_dir = Path(workdir) / f"chunk_{index:03d}"
    segments = [Segment(start, end) for start, end in json.loads(segments_json)]
    placed = build_placed(segments, options)
    out_name = f"chunk_{index:03d}.mkv"

    inputs = [source] + ([audio_dn] if audio_dn else [])
    key = stage_key("video", options, inputs)
    output_path = Path(workdir) / out_name
    if read_done_marker(chunk_dir, "video") == key and output_path.exists():
        _send(
            progress_queue,
            {"index": index, "stage": "video", "stage_pct": 1.0, "status": "done"},
        )
        return {"output": str(output_path), "cached": True}

    _send(progress_queue, {"index": index, "stage": "video", "stage_pct": 0.0, "status": "running"})
    on_progress = _progress_reporter(progress_queue, index, "video", 1.0, 0.0)
    try:
        render_video(
            Path(workdir),
            chunk,
            Path(source),
            Path(audio_dn) if audio_dn else None,
            placed,
            options,
            info,
            playback_fps(options, info),
            target_channels(info),
            ass_path=Path(chunk_dir) / "subs.ass"
            if (Path(chunk_dir) / "subs.ass").exists()
            else None,
            cancel_event=cancel_event,
            on_progress=on_progress,
            output_name=out_name,
        )
    except PipelineError as exc:
        _send(
            progress_queue,
            {"index": index, "stage": "video", "status": "failed", "error": exc.to_dict()},
        )
        raise
    write_done_marker(chunk_dir, "video", key)
    _send(
        progress_queue,
        {"index": index, "stage": "video", "stage_pct": 1.0, "status": "done"},
    )
    return {"output": str(output_path), "cached": False}
