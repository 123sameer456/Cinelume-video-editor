"""Upload API payloads."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .media import MediaInfo

PART_SIZE = 8 * 1024 * 1024  # 8 MiB recommended part size


class UploadStatus(StrEnum):
    INITIATED = "initiated"
    COMPLETE = "complete"
    FAILED = "failed"


class UploadInitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=512)
    total_size: int = Field(ge=1)


class UploadInitResponse(BaseModel):
    upload_id: str
    part_size: int
    received_parts: list[int] = Field(default_factory=list)


class UploadCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_parts: int | None = Field(default=None, ge=1)


class UploadInfo(BaseModel):
    upload_id: str
    filename: str
    size_bytes: int
    status: UploadStatus
    received_parts: list[int] = Field(default_factory=list)
    media: MediaInfo | None = None
    thumbnail_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
