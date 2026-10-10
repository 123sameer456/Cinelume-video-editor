"""Media probe result models (produced by services.probe.probe)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AudioStreamInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    codec: str = ""
    channels: int = 0
    sample_rate: int = 0
    start_time: float = 0.0
    language: str | None = None


class MediaInfo(BaseModel):
    """Everything later phases need to know about an input file."""

    model_config = ConfigDict(extra="ignore")

    path: str = ""
    format_name: str = ""
    duration: float = 0.0
    size_bytes: int = 0

    has_video: bool = False
    has_audio: bool = False

    # video
    width: int = 0
    height: int = 0
    display_width: int = 0
    display_height: int = 0
    rotation: int = 0
    fps: float = 0.0
    r_fps: float = 0.0
    is_vfr: bool = False
    pix_fmt: str = ""
    bit_depth: int = 8
    is_hdr: bool = False
    color_transfer: str | None = None
    nb_frames: int | None = None

    # audio
    audio_streams: list[AudioStreamInfo] = Field(default_factory=list)
    video_start_time: float = 0.0
    audio_start_time: float = 0.0

    warnings: list[str] = Field(default_factory=list)

    @property
    def resolution(self) -> tuple[int, int]:
        return self.display_width or self.width, self.display_height or self.height
