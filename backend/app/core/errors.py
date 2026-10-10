"""Typed pipeline errors (G9): {code, message, stage, chunk?, hint}."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # environment / tooling
    FFMPEG_MISSING = "FFMPEG_MISSING"
    # input validation
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    NO_VIDEO_STREAM = "NO_VIDEO_STREAM"
    NO_AUDIO_STREAM = "NO_AUDIO_STREAM"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    # resources
    DISK_SPACE = "DISK_SPACE"
    # processing
    FFMPEG_FAILED = "FFMPEG_FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    MODEL_MISSING = "MODEL_MISSING"
    ASR_FAILED = "ASR_FAILED"
    NO_SPEECH = "NO_SPEECH"
    NO_SPEECH_DETECTED = "NO_SPEECH_DETECTED"
    # request level
    NOT_FOUND = "NOT_FOUND"
    BAD_REQUEST = "BAD_REQUEST"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"


# HTTP status used by the FastAPI exception handler.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.FFMPEG_MISSING: 503,
    ErrorCode.UNSUPPORTED_MEDIA: 400,
    ErrorCode.NO_VIDEO_STREAM: 400,
    ErrorCode.NO_AUDIO_STREAM: 400,
    ErrorCode.LIMIT_EXCEEDED: 413,
    ErrorCode.DISK_SPACE: 507,
    ErrorCode.FFMPEG_FAILED: 500,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.CANCELLED: 499,
    ErrorCode.MODEL_MISSING: 409,
    ErrorCode.ASR_FAILED: 500,
    ErrorCode.NO_SPEECH: 400,
    ErrorCode.NO_SPEECH_DETECTED: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.CONFLICT: 409,
    ErrorCode.INTERNAL: 500,
}


class PipelineError(Exception):
    """Structured error that the API turns into a JSON body the UI can render."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        stage: str | None = None,
        chunk: int | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.chunk = chunk
        self.hint = hint

    def __reduce__(self):
        # Exceptions cross the process boundary (ProcessPoolExecutor workers).
        # Reconstruct with every constructor argument, not just the message.
        return (
            self.__class__,
            (self.code, self.message, self.stage, self.chunk, self.hint),
        )

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "stage": self.stage,
        }
        if self.chunk is not None:
            payload["chunk"] = self.chunk
        if self.hint is not None:
            payload["hint"] = self.hint
        return payload

    def __str__(self) -> str:  # pragma: no cover - debug convenience
        parts = [f"{self.code.value}: {self.message}"]
        if self.stage:
            parts.append(f"(stage={self.stage})")
        if self.chunk is not None:
            parts.append(f"(chunk={self.chunk})")
        return " ".join(parts)
