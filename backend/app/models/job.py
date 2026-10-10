"""Job state models shared by the DB layer, the API and the UI."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .options import JobOptions


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ChunkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChunkProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    status: ChunkStatus = ChunkStatus.PENDING
    stage: str | None = None
    stage_pct: float = 0.0
    src_start: float = 0.0
    src_end: float = 0.0
    hard_boundary: bool = False
    cut_duration: float = 0.0
    attempts: int = 0


class ProgressSnapshot(BaseModel):
    """Payload pushed over SSE and returned on connect so the UI can recover."""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    status: JobStatus
    stage: str | None = None
    overall_pct: float = 0.0
    stage_pct: float = 0.0
    eta_sec: float | None = None
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    chunks: list[ChunkProgress] = Field(default_factory=list)


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str
    options: JobOptions = Field(default_factory=JobOptions)


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    upload_id: str
    status: JobStatus
    options: JobOptions
    created_at: float = 0.0
    updated_at: float = 0.0
    stage: str | None = None
    overall_pct: float = 0.0
    message: str | None = None
    error: dict | None = None
    warnings: list[str] = Field(default_factory=list)
    chunks: list[ChunkProgress] = Field(default_factory=list)
