"""Stage runners (phase 2). Same code path for one chunk or many (rule G10).

Phase 5 wires these into the parallel orchestrator; the CLI uses them directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import get_settings
from ..core.errors import ErrorCode, PipelineError
from ..models.chunk import ChunkSpec, whole_file_chunk
from ..models.media import MediaInfo
from ..models.options import JobOptions
from ..services.denoise import denoise_audio, extract_audio, target_channels
from ..services.envelope import auto_threshold, looks_all_silent, rms_envelope_db
from ..services.probe import probe, validate_video_upload
from ..services.render import Placed, build_placed, playback_fps, render_cut_audio, render_video
from ..services.segments import Segment, SegmentParams, compute_keep_segments

ProgressCallback = Callable[[float], None]


@dataclass
class AudioStageResult:
    audio_raw: Path | None
    audio_dn: Path | None
    audio_cut: Path | None
    segments: list[Segment]
    placed: list[Placed]
    threshold_db: float
    removed_sec: float
    output_duration: float
    warnings: list[str] = field(default_factory=list)
    has_audio: bool = True


@dataclass
class CutResult:
    output: Path
    audio_cut: Path | None
    segments: list[Segment]
    threshold_db: float
    removed_sec: float
    output_duration: float
    warnings: list[str] = field(default_factory=list)


def segment_params(options: JobOptions, env_db) -> tuple[SegmentParams, float, list[str]]:
    warnings: list[str] = []
    threshold = options.silence.threshold_db
    if options.silence.auto_threshold:
        threshold = auto_threshold(env_db)
        warnings.append(f"Auto threshold selected: {threshold:.1f} dB")
    params = SegmentParams(
        threshold_db=threshold,
        min_silence_sec=options.silence.min_silence_sec,
        keep_padding_sec=options.silence.keep_padding_sec,
        min_speech_sec=options.silence.min_speech_sec,
    )
    return params, threshold, warnings


def stage_audio(
    workdir: Path,
    chunk: ChunkSpec,
    source: Path,
    options: JobOptions,
    info: MediaInfo,
    fps: int,
    cancel_event: Any | None = None,
    on_progress: ProgressCallback | None = None,
    log: logging.Logger | None = None,
) -> AudioStageResult:
    """Extract -> denoise -> envelope -> keep-segments -> cut audio."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if not info.has_audio:
        warnings.append("No audio track: denoise and silence removal are skipped.")
        segment = Segment(0.0, chunk.duration)
        return AudioStageResult(
            None,
            None,
            None,
            [segment],
            build_placed([segment], options),
            options.silence.threshold_db,
            0.0,
            chunk.duration,
            warnings,
            has_audio=False,
        )

    audio_raw = extract_audio(workdir, chunk, source, options, info, cancel_event=cancel_event)
    audio_dn = denoise_audio(
        workdir, audio_raw, options, warnings=warnings, cancel_event=cancel_event
    )
    env = rms_envelope_db(audio_dn)
    params, threshold, extra = segment_params(options, env.values)
    warnings.extend(extra)
    if log is not None:
        log.info(f"envelope: {len(env.values)} hops, threshold {threshold:.1f} dB")

    if not options.silence.enabled:
        segments = [Segment(0.0, chunk.duration)]
    else:
        if looks_all_silent(env.values, threshold):
            raise PipelineError(
                ErrorCode.NO_SPEECH,
                "No speech above the threshold was found.",
                stage="silence",
                hint="Lower threshold_db, or enable auto_threshold.",
            )
        segments = compute_keep_segments(env.values, env.hop_sec, chunk.duration, fps, params)
    if not segments:
        raise PipelineError(
            ErrorCode.NO_SPEECH,
            "Every part of the audio was treated as silence.",
            stage="silence",
            hint="Lower threshold_db, or enable auto_threshold.",
        )

    output_duration = sum(seg.duration for seg in segments)
    removed = max(0.0, chunk.duration - output_duration)
    if log is not None:
        log.info(f"segments: {len(segments)} kept, {removed:.2f}s removed")

    audio_cut = render_cut_audio(
        workdir, audio_dn, segments, target_channels(info), cancel_event=cancel_event
    )
    return AudioStageResult(
        audio_raw,
        audio_dn,
        audio_cut,
        segments,
        build_placed(segments, options),
        threshold,
        removed,
        output_duration,
        warnings,
        has_audio=True,
    )


def stage_video(
    workdir: Path,
    chunk: ChunkSpec,
    source: Path,
    audio_dn: Path | None,
    placed: list[Placed],
    options: JobOptions,
    info: MediaInfo,
    fps: int,
    ass_path: Path | None = None,
    warnings: list[str] | None = None,
    cancel_event: Any | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    return render_video(
        Path(workdir),
        chunk,
        source,
        audio_dn,
        placed,
        options,
        info,
        fps,
        target_channels(info),
        ass_path=ass_path,
        warnings=warnings,
        cancel_event=cancel_event,
        on_progress=on_progress,
    )


def run_single_chunk(
    source: Path,
    options: JobOptions,
    workdir: Path,
    info: MediaInfo | None = None,
    ass_path: Path | None = None,
    cancel_event: Any | None = None,
    on_progress: ProgressCallback | None = None,
) -> CutResult:
    """Whole-file pipeline (one chunk) - used by the CLI now and by phase 5 later."""
    source = Path(source)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    info = info or probe(source)
    validate_video_upload(info, get_settings().max_duration_sec)
    chunk = whole_file_chunk(info.duration)
    fps = playback_fps(options, info)

    audio = stage_audio(workdir, chunk, source, options, info, fps, cancel_event=cancel_event)
    output = stage_video(
        workdir,
        chunk,
        source,
        audio.audio_dn,
        audio.placed,
        options,
        info,
        fps,
        ass_path=ass_path,
        warnings=audio.warnings,
        cancel_event=cancel_event,
        on_progress=on_progress,
    )
    return CutResult(
        output=output,
        audio_cut=audio.audio_cut,
        segments=audio.segments,
        threshold_db=audio.threshold_db,
        removed_sec=audio.removed_sec,
        output_duration=audio.output_duration,
        warnings=audio.warnings,
    )
