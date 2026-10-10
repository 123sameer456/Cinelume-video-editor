"""JobOptions - the single source of truth for every tunable value.

Mirrors 00-options-schema.md exactly. The server is the validator; the UI renders
field errors next to the control. Colors are #RRGGBB strings; sizes are percent of
video height so styling stays resolution independent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

_HEX = r"^#[0-9A-Fa-f]{6}$"

Color = Annotated[str, StringConstraints(pattern=_HEX)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------- enums
class DenoiseMethod(StrEnum):
    AFFTDN = "afftdn"
    ARNNDN = "arnndn"
    NOISEREDUCE = "noisereduce"


class WhisperModelSize(StrEnum):
    TINY = "tiny"
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_V3 = "large-v3"


class Device(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"


class SubtitleMode(StrEnum):
    KARAOKE_FILL = "karaoke_fill"
    WORD_HIGHLIGHT = "word_highlight"
    STATIC = "static"


class BackgroundMode(StrEnum):
    NONE = "none"
    BOX = "box"
    OUTLINE = "outline"


class Position(StrEnum):
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


class CoverTimeMode(StrEnum):
    RANDOM = "random"
    MANUAL = "manual"


class CoverAspect(StrEnum):
    SAME_AS_VIDEO = "same_as_video"
    PORTRAIT_9_16 = "9:16"
    SQUARE_1_1 = "1:1"
    LANDSCAPE_16_9 = "16:9"
    PORTRAIT_4_5 = "4:5"


class CoverFormat(StrEnum):
    PNG = "png"
    JPG = "jpg"


class Resolution(StrEnum):
    ORIGINAL = "original"
    PORTRAIT_1080 = "1080x1920"
    LANDSCAPE_1080 = "1920x1080"
    SQUARE_1080 = "1080x1080"
    PORTRAIT_720 = "720x1280"


class Fit(StrEnum):
    CROP = "crop"
    PAD = "pad"


class Preset(StrEnum):
    ULTRAFAST = "ultrafast"
    SUPERFAST = "superfast"
    VERYFAST = "veryfast"
    FASTER = "faster"
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


class Encoder(StrEnum):
    AUTO = "auto"
    LIBX264 = "libx264"
    NVENC = "h264_nvenc"
    QSV = "h264_qsv"
    AMF = "h264_amf"


class ChunkingMode(StrEnum):
    AUTO = "auto"
    OFF = "off"
    ON = "on"


# ----------------------------------------------------------------------- denoise
class DenoiseOptions(_Model):
    enabled: bool = True
    method: DenoiseMethod = DenoiseMethod.AFFTDN
    strength: int = Field(default=50, ge=0, le=100)
    highpass_hz: int = Field(default=80, ge=0, le=300)


# ----------------------------------------------------------------------- silence
class SilenceOptions(_Model):
    enabled: bool = True
    min_silence_sec: float = Field(default=0.5, ge=0.1, le=10)
    threshold_db: float = Field(default=-35, ge=-60, le=-15)
    auto_threshold: bool = False
    keep_padding_sec: float = Field(default=0.12, ge=0, le=1)
    min_speech_sec: float = Field(default=0.25, ge=0, le=2)
    fade_enabled: bool = True
    fade_sec: float = Field(default=0.12, ge=0.03, le=0.5)
    fade_video: bool = True
    fade_audio: bool = True
    fade_color: Color = "#000000"
    fade_first_last: bool = False

    @model_validator(mode="after")
    def _cross_field(self) -> SilenceOptions:
        if self.min_silence_sec <= 2 * self.keep_padding_sec:
            raise ValueError("min_silence_sec must be greater than 2 * keep_padding_sec")
        if self.fade_sec * 2 > self.min_speech_sec + 2 * self.keep_padding_sec:
            raise ValueError(
                "fade_sec is too long: 2 * fade_sec must be <= "
                "min_speech_sec + 2 * keep_padding_sec"
            )
        return self


# --------------------------------------------------------------------- subtitles
class SubtitleOptions(_Model):
    enabled: bool = True
    model_size: WhisperModelSize = WhisperModelSize.SMALL
    language: str = "auto"
    device: Device = Device.AUTO
    vocabulary_hint: str = Field(default="", max_length=200)
    mode: SubtitleMode = SubtitleMode.KARAOKE_FILL
    font_family: str = "Montserrat"
    font_size_pct: float = Field(default=4.5, ge=2, le=10)
    bold: bool = True
    text_color: Color = "#FFFFFF"
    highlight_color: Color = "#FFD400"
    background_mode: BackgroundMode = BackgroundMode.NONE
    background_color: Color = "#000000"
    background_opacity: int = Field(default=60, ge=0, le=100)
    outline_width_pct: float = Field(default=0.25, ge=0, le=1)
    shadow: bool = True
    position: Position = Position.BOTTOM
    margin_v_pct: float = Field(default=15, ge=0, le=40)
    words_per_line: int = Field(default=4, ge=1, le=8)
    max_chars_per_line: int = Field(default=28, ge=10, le=60)
    max_lines: int = Field(default=2, ge=1, le=3)
    uppercase: bool = False
    pop_animation: bool = False


# -------------------------------------------------------------------------- music
class MusicOptions(_Model):
    enabled: bool = False
    track_id: str | None = None
    volume: int = Field(default=8, ge=0, le=100)
    loop: bool = True
    fade_in_sec: float = Field(default=1.5, ge=0, le=10)
    fade_out_sec: float = Field(default=2.0, ge=0, le=10)
    start_offset_sec: float = Field(default=0, ge=0, le=600)
    ducking_enabled: bool = False
    ducking_strength: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def _cross_field(self) -> MusicOptions:
        if self.enabled and not self.track_id:
            raise ValueError("track_id is required when music is enabled")
        return self


# -------------------------------------------------------------------------- cover
class CoverOptions(_Model):
    enabled: bool = True
    time_mode: CoverTimeMode = CoverTimeMode.RANDOM
    time_sec: float | None = None
    seed: int | None = None
    candidates: int = Field(default=5, ge=1, le=12)
    lines: list[str] = Field(default_factory=list, max_length=4)
    font_family: str = "Montserrat"
    font_size_pct: float = Field(default=7, ge=2, le=20)
    text_color: Color = "#FFFFFF"
    stroke_color: Color = "#000000"
    stroke_width_pct: float = Field(default=0.4, ge=0, le=2)
    strip_enabled: bool = False
    strip_color: Color = "#000000"
    strip_opacity: int = Field(default=55, ge=0, le=100)
    position: Position = Position.BOTTOM
    aspect: CoverAspect = CoverAspect.SAME_AS_VIDEO
    format: CoverFormat = CoverFormat.PNG

    @model_validator(mode="after")
    def _cross_field(self) -> CoverOptions:
        if self.time_mode is CoverTimeMode.MANUAL and self.time_sec is None:
            raise ValueError("time_sec is required when time_mode is 'manual'")
        return self


# ------------------------------------------------------------------------- output
class OutputOptions(_Model):
    resolution: Resolution = Resolution.ORIGINAL
    fit: Fit = Fit.CROP
    fps: int | None = Field(default=None, ge=15, le=60)
    crf: int = Field(default=23, ge=14, le=35)
    preset: Preset = Preset.VERYFAST
    encoder: Encoder = Encoder.AUTO
    audio_bitrate_kbps: int = Field(default=192, ge=96, le=320)
    loudnorm: bool = True
    target_lufs: float = Field(default=-14, ge=-24, le=-9)
    audio_track_index: int = Field(default=0, ge=0)


# -------------------------------------------------------------------- performance
class PerformanceOptions(_Model):
    chunking: ChunkingMode = ChunkingMode.AUTO
    chunk_threshold_sec: int = Field(default=240, ge=60, le=3600)
    target_chunk_sec: int = Field(default=120, ge=30, le=600)
    boundary_window_sec: int = Field(default=20, ge=5, le=60)
    workers: int | None = Field(default=None, ge=1, le=16)
    asr_workers: int = Field(default=1, ge=1, le=4)
    max_segments_per_graph: int = Field(default=120, ge=20, le=400)
    degrade_on_asr_failure: bool = False
    keep_intermediates: bool = False


# ---------------------------------------------------------------------- job root
class JobOptions(_Model):
    denoise: DenoiseOptions = Field(default_factory=DenoiseOptions)
    silence: SilenceOptions = Field(default_factory=SilenceOptions)
    subtitles: SubtitleOptions = Field(default_factory=SubtitleOptions)
    music: MusicOptions = Field(default_factory=MusicOptions)
    cover: CoverOptions = Field(default_factory=CoverOptions)
    output: OutputOptions = Field(default_factory=OutputOptions)
    performance: PerformanceOptions = Field(default_factory=PerformanceOptions)
