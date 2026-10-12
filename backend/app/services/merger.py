"""Merge chunk outputs with the concat demuxer, then run the master (phase 5.3)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..core.errors import ErrorCode, PipelineError
from ..services.ffmpeg import run_ffmpeg, run_ffprobe_json
from ..services.probe import probe

_EPS = 1e-6


def merge_chunks(chunks: list[Path], out_path: Path, workdir: Path, cancel_event=None) -> Path:
    """Concat demuxer, stream copy. Sorted, empty chunks skipped."""
    existing = [path for path in sorted(chunks) if path.exists() and path.stat().st_size > 0]
    if not existing:
        raise PipelineError(
            ErrorCode.NO_SPEECH, "Every chunk was empty; there is nothing to merge.", stage="merge"
        )
    list_file = Path(workdir) / "concat_chunks.txt"
    list_file.write_text("".join(f"file '{path.name}'\n" for path in existing), encoding="utf-8")
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-fflags",
            "+genpts",
            "-i",
            list_file.name,
            "-c",
            "copy",
            "-y",
            out_path.name,
        ],
        cwd=Path(workdir),
        timeout=1800,
        cancel_event=cancel_event,
        log_path=Path(workdir) / "ffmpeg.log",
    )
    if not out_path.exists():
        raise PipelineError(ErrorCode.FFMPEG_FAILED, "Merge produced no output.", stage="merge")
    return out_path


def verify_merge(merged: Path, expected_duration: float, fps: float) -> None:
    """The merged duration must match the chunks and the stream layout must be consistent."""
    info = probe(merged)
    tolerance = 1 / fps if fps > 0 else 0.05
    if abs(info.duration - expected_duration) > tolerance + 0.2:
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED,
            f"Merged duration {info.duration:.3f}s does not match the chunks "
            f"({expected_duration:.3f}s).",
            stage="merge",
        )
    data = run_ffprobe_json(merged)
    streams = [s for s in data.get("streams", [])]
    codecs = {(s.get("codec_type"), s.get("codec_name")) for s in streams}
    if len([s for s in streams if s.get("codec_type") == "video"]) != 1:
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED, "Merged output has more than one video stream.", stage="merge"
        )
    if any(name == "aac" for kind, name in codecs):
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED,
            "Merged output has an AAC track; chunk audio must be PCM for seamless joins.",
            stage="merge",
        )


def cleanup_chunks(workdir: Path) -> None:
    """Delete chunk intermediates once the merge succeeded."""
    for path in Path(workdir).glob("chunk_*"):
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def finalize_outputs(workdir: Path, outdir: Path) -> list[Path]:
    """Move the merged/mastered files into the outputs folder."""
    outdir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for name in ("final.mp4", "cover.png", "cover.jpg", "transcript.json"):
        source = Path(workdir) / name
        if source.exists():
            destination = Path(outdir) / name
            os.replace(source, destination)
            moved.append(destination)
    return moved
