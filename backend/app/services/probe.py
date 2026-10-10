"""ffprobe -> MediaInfo. Records everything later phases need; never raises a traceback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.errors import ErrorCode, PipelineError
from ..models.media import AudioStreamInfo, MediaInfo
from .ffmpeg import run_ffprobe_json

_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
_IMAGE_FORMATS = {"image2", "png_pipe", "jpeg_pipe", "image2pipe", "mjpeg"}
# ffprobe happily "reads" plain text as ANSI art (a tty demuxer) - reject pseudo-media.
_PSEUDO_FORMATS = {"tty"}
_PSEUDO_VIDEO_CODECS = {"ansi"}
_BITDEPTH_RE = re.compile(r"(?:p|yuv\d*|y\d*|gbrp)(9|10|12|14|16)(?:le|be)?")


def _parse_fraction(value: Any) -> float:
    if not value or not isinstance(value, str):
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            n, d = float(num), float(den)
        except ValueError:
            return 0.0
        return n / d if d else 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "N/A"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _rotation_from_stream(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(round(_to_float(side.get("rotation"))))
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        return int(round(_to_float(tags.get("rotate"))))
    return 0


def _bit_depth(stream: dict[str, Any]) -> int:
    bits = _to_int(stream.get("bits_per_raw_sample"))
    if bits:
        return bits
    pix_fmt = stream.get("pix_fmt") or ""
    match = _BITDEPTH_RE.search(pix_fmt)
    if match:
        return int(match.group(1))
    return 8


def _is_attached_pic(stream: dict[str, Any]) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def probe(path: Path) -> MediaInfo:
    """Probe a media file. Raises PipelineError(UNSUPPORTED_MEDIA) on failure."""
    data = run_ffprobe_json(Path(path))
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    if not streams:
        # ffprobe read the file but found no streams: not media we can process.
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "No media streams found in the file.",
            stage="probe",
            hint="Upload a video file (mp4, mov, mkv, webm).",
        )

    format_names = set((fmt.get("format_name") or "").split(","))
    codec_names = {s.get("codec_name") for s in streams}
    if format_names & _PSEUDO_FORMATS or codec_names & _PSEUDO_VIDEO_CODECS:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "The file is not a video.",
            stage="probe",
            hint="Upload a video file (mp4, mov, mkv, webm).",
        )

    video_streams = [
        s for s in streams if s.get("codec_type") == "video" and not _is_attached_pic(s)
    ]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    info = MediaInfo(path=str(path))
    info.format_name = str(fmt.get("format_name") or "")
    info.size_bytes = _to_int(fmt.get("size"))
    info.duration = _to_float(fmt.get("duration"))
    info.has_video = bool(video_streams)
    info.has_audio = bool(audio_streams)

    if video_streams:
        v = video_streams[0]
        info.width = _to_int(v.get("width"))
        info.height = _to_int(v.get("height"))
        info.rotation = _rotation_from_stream(v)
        info.fps = _parse_fraction(v.get("avg_frame_rate"))
        info.r_fps = _parse_fraction(v.get("r_frame_rate"))
        info.pix_fmt = v.get("pix_fmt") or ""
        info.bit_depth = _bit_depth(v)
        transfer = (v.get("color_transfer") or "").lower()
        info.color_transfer = transfer or None
        info.is_hdr = transfer in _HDR_TRANSFERS
        info.video_start_time = _to_float(v.get("start_time"))
        if v.get("nb_frames") not in (None, "N/A"):
            info.nb_frames = _to_int(v.get("nb_frames"))

        rotated = abs(info.rotation) % 180 == 90
        info.display_width = info.height if rotated else info.width
        info.display_height = info.width if rotated else info.height

        if info.fps <= 0:
            info.fps = info.r_fps
        if info.fps > 0 and info.r_fps > 0:
            info.is_vfr = abs(info.fps - info.r_fps) / info.fps > 0.02
        elif info.fps <= 0:
            info.is_vfr = True

    if not info.duration:
        # Fall back to the longest stream duration (some containers omit format duration).
        durations = [_to_float(s.get("duration")) for s in streams]
        info.duration = max(durations, default=0.0)

    for s in audio_streams:
        info.audio_streams.append(
            AudioStreamInfo(
                index=_to_int(s.get("index")),
                codec=s.get("codec_name") or "",
                channels=_to_int(s.get("channels")),
                sample_rate=_to_int(s.get("sample_rate")),
                start_time=_to_float(s.get("start_time")),
                language=(s.get("tags") or {}).get("language"),
            )
        )
    if audio_streams:
        info.audio_start_time = _to_float(audio_streams[0].get("start_time"))

    info.warnings = _warnings(info)
    return info


def _warnings(info: MediaInfo) -> list[str]:
    warnings: list[str] = []
    if not info.has_audio:
        warnings.append(
            "This file has no audio track; denoise, silence removal and subtitles "
            "will be skipped."
        )
    if info.is_vfr:
        warnings.append(
            "Variable frame rate detected; it will be converted to a constant frame rate."
        )
    if abs(info.rotation) % 360:
        warnings.append(f"Rotation metadata ({info.rotation}deg) will be applied.")
    if info.is_hdr:
        warnings.append("HDR source detected; it will be converted to SDR (may shift colors).")
    if info.bit_depth and info.bit_depth > 8:
        warnings.append(f"{info.bit_depth}-bit source; output is 8-bit.")
    if info.display_width and info.display_width % 2:
        warnings.append("Odd width detected; output dimensions will be rounded to even.")
    if info.display_height and info.display_height % 2:
        warnings.append("Odd height detected; output dimensions will be rounded to even.")
    if len(info.audio_streams) > 1:
        warnings.append(
            f"{len(info.audio_streams)} audio tracks found; track 0 is used unless changed."
        )
    if abs(info.video_start_time) > 0.01 or abs(info.audio_start_time) > 0.01:
        warnings.append("Non-zero stream start offsets detected; they will be normalized.")
    return warnings


def looks_like_image(info: MediaInfo) -> bool:
    """True for still-image inputs that should not be treated as videos."""
    if info.format_name and info.format_name.split(",")[0] in _IMAGE_FORMATS:
        return True
    return info.has_video and info.duration <= 0 and (info.nb_frames or 0) <= 1


def validate_video_upload(info: MediaInfo, max_duration_sec: float) -> None:
    """Raise the right PipelineError for an unusable upload."""
    if not info.has_video or looks_like_image(info):
        raise PipelineError(
            ErrorCode.NO_VIDEO_STREAM,
            "No usable video stream found.",
            stage="probe",
            hint="Upload a video file (mp4, mov, mkv, webm).",
        )
    if info.duration <= 0:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "The video has zero duration.",
            stage="probe",
        )
    if max_duration_sec and info.duration > max_duration_sec:
        raise PipelineError(
            ErrorCode.LIMIT_EXCEEDED,
            f"Video is longer than the {max_duration_sec / 60:.0f} minute limit.",
            stage="probe",
            hint="Trim the video or raise MAX_DURATION_MIN in the server settings.",
        )


def require_audio(info: MediaInfo, stage: str = "probe") -> None:
    """Raise NO_AUDIO_STREAM when a stage needs audio and there is none."""
    if not info.has_audio or not info.audio_streams:
        raise PipelineError(
            ErrorCode.NO_AUDIO_STREAM,
            "This file has no audio track.",
            stage=stage,
            hint="Disable denoise, silence removal and subtitles, or upload a file with audio.",
        )
