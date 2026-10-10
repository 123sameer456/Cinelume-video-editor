"""Structured logging setup plus per-job log file helpers (G2, phase 7.3)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Minimal structured formatter: one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("job_id", "stage", "chunk"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def job_logger(log_path: Path, name: str = "cinelume.job") -> logging.Logger:
    """Return a logger that appends structured lines to a per-job log file."""
    job_log_path = Path(log_path)
    job_log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"{name}.{job_log_path.parent.name}.{job_log_path.stem}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    # Avoid stacking duplicate handlers if called repeatedly for the same job.
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    file_handler = logging.FileHandler(job_log_path, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)
    return logger
