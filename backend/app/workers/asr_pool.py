"""Persistent ASR worker processes (phase 5 concurrency design).

The Whisper model is loaded ONCE per worker process by the pool initializer and reused
for every chunk - never re-loaded per chunk.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

_transcriber = None  # set once per worker process


def init_asr_worker(model_size: str, device: str, warnings: list[str] | None = None) -> None:
    """Pool initializer: load the model once for this worker process."""
    global _transcriber
    from ..services.transcribe import FasterWhisperTranscriber

    if _transcriber is not None:
        return
    collected = warnings if warnings is not None else []
    _transcriber = FasterWhisperTranscriber(model_size, device, warnings=collected)


def _require_model():
    if _transcriber is None:
        raise RuntimeError("ASR worker was not initialized with a model.")
    return _transcriber


def detect_language_chunk(audio_path: str) -> str:
    return _require_model().detect_language(Path(audio_path))


def transcribe_chunk(
    audio_path: str,
    language: str | None,
    options_json: str,
    chunk_dir: str,
    chunk_index: int,
) -> dict[str, Any]:
    """Runs in a persistent ASR worker; the model is already loaded."""
    from ..models.options import JobOptions
    from ..services.transcribe import save_words

    options = JobOptions(**json.loads(options_json))
    transcriber = _require_model()
    words = transcriber.transcribe(Path(audio_path), language, options)
    save_words(words, Path(chunk_dir) / "words.json")
    return {
        "words": [asdict(word) for word in words],
        "device": transcriber.device,
        "compute_type": transcriber.compute_type,
        "chunk_index": chunk_index,
    }


def asr_worker_info() -> dict[str, str]:
    transcriber = _require_model()
    return {"device": transcriber.device, "compute_type": transcriber.compute_type}
