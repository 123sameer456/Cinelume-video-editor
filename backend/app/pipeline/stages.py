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
from ..models.subtitle import Word
from ..services.cover import render_cover
from ..services.denoise import denoise_audio, extract_audio, target_channels
from ..services.envelope import auto_threshold, looks_all_silent, rms_envelope_db
from ..services.fonts import font_path
from ..services.master import run_master
from ..services.probe import probe, validate_video_upload
from ..services.render import (
    Placed,
    build_placed,
    output_size,
    playback_fps,
    render_cut_audio,
    render_video,
)
from ..services.segments import Segment, SegmentParams, compute_keep_segments
from ..services.subtitles import build_ass, group_lines, repair_words
from ..services.transcribe import FasterWhisperTranscriber, Transcriber, save_words

ProgressCallback = Callable[[float], None]
_log = logging.getLogger("cinelume.stages")


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
    video_raw: Path
    cover: Path | None
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
    if on_progress is not None:
        on_progress(40.0)
    audio_dn = denoise_audio(
        workdir, audio_raw, options, warnings=warnings, cancel_event=cancel_event
    )
    if on_progress is not None:
        on_progress(70.0)
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
    if on_progress is not None:
        on_progress(100.0)
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


@dataclass
class TranscribeResult:
    words: list[Word]
    language: str | None
    warnings: list[str] = field(default_factory=list)


def stage_transcribe(
    workdir: Path,
    chunk: ChunkSpec,
    audio_cut: Path,
    options: JobOptions,
    transcriber: Transcriber | None = None,
    language: str | None = None,
) -> TranscribeResult:
    """Transcribe the already-cut audio: timestamps land on the OUTPUT timeline."""
    workdir = Path(workdir)
    chunk_dir = workdir / f"chunk_{chunk.index:03d}"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if transcriber is None:
        transcriber = FasterWhisperTranscriber(
            options.subtitles.model_size.value,
            options.subtitles.device.value,
            warnings=warnings,
        )

    detected: str | None = language
    if language in (None, "auto"):
        try:
            detected = transcriber.detect_language(audio_cut)
        except Exception as exc:  # language detection must never kill the job
            warnings.append(f"Language detection failed ({exc}); falling back to 'en'.")
            detected = "en"
    _log.info(f"chunk {chunk.index}: language={detected}")

    words = transcriber.transcribe(
        audio_cut, None if detected in (None, "auto") else detected, options
    )
    save_words(words, chunk_dir / "words.json")
    return TranscribeResult(words=words, language=detected, warnings=warnings)


def stage_subtitles(
    workdir: Path,
    chunk: ChunkSpec,
    words: list[Word],
    options: JobOptions,
    info: MediaInfo,
    audio_end: float,
    envelope_audio: Path | None = None,
    warnings: list[str] | None = None,
) -> Path | None:
    """Repair, group and build the ASS file for a chunk. Returns None to skip."""
    warnings = warnings if warnings is not None else []
    if not words:
        warnings.append("No speech detected: subtitles are skipped for this chunk.")
        return None

    subs = options.subtitles
    play_res = output_size(options, info)
    font_file = font_path(subs.font_family)

    envelope = None
    hop_sec = 0.02
    threshold = None
    if envelope_audio is not None and Path(envelope_audio).exists() and options.silence.enabled:
        measured = rms_envelope_db(Path(envelope_audio))
        envelope = measured.values
        hop_sec = measured.hop_sec
        threshold = options.silence.threshold_db

    cleaned = repair_words(words, audio_end, envelope, hop_sec, threshold)
    if not cleaned:
        warnings.append("No speech detected: subtitles are skipped for this chunk.")
        return None

    captions = group_lines(cleaned, options, play_res, subs.font_family, font_file)
    if not captions:
        warnings.append("No speech detected: subtitles are skipped for this chunk.")
        return None

    ass_text = build_ass(captions, options, play_res, subs.font_family, font_file)
    out = Path(workdir) / f"chunk_{chunk.index:03d}" / "subs.ass"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ass_text, encoding="utf-8")
    return out


def _transcribe_and_build_ass(
    workdir: Path,
    chunk: ChunkSpec,
    audio: AudioStageResult,
    options: JobOptions,
    info: MediaInfo,
    transcriber: Transcriber | None,
    cancel_event: Any | None,
) -> Path | None:
    try:
        transcribed = stage_transcribe(
            workdir, chunk, audio.audio_cut, options, transcriber=transcriber
        )
        audio.warnings.extend(transcribed.warnings)
        _log.info(
            f"chunk {chunk.index}: {len(transcribed.words)} words, "
            f"language={transcribed.language}"
        )
        return stage_subtitles(
            workdir,
            chunk,
            transcribed.words,
            options,
            info,
            audio.output_duration,
            envelope_audio=audio.audio_cut,
            warnings=audio.warnings,
        )
    except PipelineError as exc:
        if options.performance.degrade_on_asr_failure:
            audio.warnings.append(
                f"Transcription failed ({exc.message}); this chunk is rendered without subtitles."
            )
            return None
        raise
    except Exception as exc:
        # ASR can fail in unstructured ways (OOM, CUDA, decoder); degrade or wrap it.
        if options.performance.degrade_on_asr_failure:
            audio.warnings.append(
                f"Transcription failed ({exc}); this chunk is rendered without subtitles."
            )
            return None
        raise PipelineError(
            ErrorCode.ASR_FAILED, f"Transcription failed: {exc}", stage="transcribe"
        ) from exc


def run_single_chunk(
    source: Path,
    options: JobOptions,
    workdir: Path,
    info: MediaInfo | None = None,
    ass_path: Path | None = None,
    transcriber: Transcriber | None = None,
    cancel_event: Any | None = None,
    on_progress: ProgressCallback | None = None,
) -> CutResult:
    """Whole-file pipeline (one chunk) - used by the CLI now and by phase 5 later.

    Order per chunk: audio stage -> transcribe the cut audio -> build ASS -> video pass
    with `ass_path`, so subtitle timestamps already match the output timeline.
    """
    source = Path(source)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    info = info or probe(source)
    validate_video_upload(info, get_settings().max_duration_sec)
    chunk = whole_file_chunk(info.duration)
    fps = playback_fps(options, info)

    audio = stage_audio(workdir, chunk, source, options, info, fps, cancel_event=cancel_event)

    if ass_path is None and options.subtitles.enabled:
        if audio.audio_cut is None:
            audio.warnings.append("No audio: subtitles are skipped.")
        else:
            ass_path = _transcribe_and_build_ass(
                workdir, chunk, audio, options, info, transcriber, cancel_event
            )

    output_raw = stage_video(
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

    # Master: music mix + loudness normalize + AAC -> the only MP4 we produce.
    final = run_master(
        output_raw, options, workdir / "final.mp4", workdir=workdir, cancel_event=cancel_event
    )

    cover_path: Path | None = None
    if options.cover.enabled:
        try:
            cover_path = render_cover(source, options, workdir / "cover.png").path
        except PipelineError as exc:
            audio.warnings.append(f"Cover generation failed: {exc.message}")

    return CutResult(
        output=final,
        video_raw=output_raw,
        cover=cover_path,
        audio_cut=audio.audio_cut,
        segments=audio.segments,
        threshold_db=audio.threshold_db,
        removed_sec=audio.removed_sec,
        output_duration=audio.output_duration,
        warnings=audio.warnings,
    )
