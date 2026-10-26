"""Shorts/reels extraction: cut start-end ranges out of a finished job's final.mp4.

Each requested range is re-encoded (frame-accurate seek, libx264 + AAC) and stored as
`outputs/<job_id>/clips/short_<index>.mp4`, so markers stay decoupled from filenames.
"""

from __future__ import annotations

from pathlib import Path

from ..core.errors import ErrorCode, PipelineError
from .ffmpeg import run_ffmpeg
from .probe import probe

MAX_CLIPS = 30
MIN_CLIP_SEC = 0.5
MAX_CLIP_SEC = 600.0


def clips_dir(job_id: str) -> Path:
    from ..core.paths import output_dir

    return output_dir(job_id) / "clips"


def clip_path(job_id: str, index: int) -> Path:
    return clips_dir(job_id) / f"short_{index:02d}.mp4"


def validate_ranges(ranges: list[tuple[float, float]], duration: float) -> None:
    for start, end in ranges:
        if start < 0 or end <= 0:
            raise PipelineError(
                ErrorCode.BAD_REQUEST,
                "Clip times must be positive.",
                stage="clips",
            )
        if end <= start:
            raise PipelineError(
                ErrorCode.BAD_REQUEST,
                "A clip's end must come after its start.",
                stage="clips",
            )
        if start >= duration:
            raise PipelineError(
                ErrorCode.BAD_REQUEST,
                f"A clip starts at {start:.2f}s but the video is only {duration:.2f}s long.",
                stage="clips",
            )
        length = end - start
        if length < MIN_CLIP_SEC:
            raise PipelineError(
                ErrorCode.BAD_REQUEST,
                f"Clips must be at least {MIN_CLIP_SEC}s long.",
                stage="clips",
            )
        if length > MAX_CLIP_SEC:
            raise PipelineError(
                ErrorCode.BAD_REQUEST,
                f"Clips can be at most {MAX_CLIP_SEC:.0f}s long.",
                stage="clips",
            )


def render_clip_to(
    video: Path,
    out_path: Path,
    start: float,
    end: float,
    cancel_event=None,
) -> Path:
    """Cut [start, end) seconds from `video` into `out_path` (re-encoded, accurate)."""
    duration = end - start
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-ss",
            f"{max(0.0, start):.6f}",
            "-i",
            str(video),
            "-t",
            f"{duration:.6f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-y",
            str(out_path),
        ],
        cwd=out_path.parent,
        timeout=max(300.0, 12 * duration),
        cancel_event=cancel_event,
        log_path=out_path.parent / "clips.log",
    )
    if not out_path.exists():
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED, "Clip export produced no output.", stage="clips"
        )
    return out_path


def probe_duration(video: Path) -> float:
    return probe(video).duration
