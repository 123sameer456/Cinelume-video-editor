"""Per-stage cache keys and `.done` markers (G3, architecture rule 9).

Each stage hashes only the options that affect it plus its input hashes, so changing a
subtitle colour does not redo denoise or transcription, and changing music/loudness only
redoes the master.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..models.options import JobOptions

_AFFECTS: dict[str, list[str]] = {
    "audio": ["denoise", "silence", "output.audio_track_index"],
    "transcribe": [
        "subtitles.model_size",
        "subtitles.language",
        "subtitles.device",
        "subtitles.vocabulary_hint",
    ],
    "video": [
        "silence",
        "subtitles",
        "output.resolution",
        "output.fit",
        "output.fps",
        "output.crf",
        "output.preset",
        "output.encoder",
        "performance.max_segments_per_graph",
    ],
    "master": [
        "music",
        "output.loudnorm",
        "output.target_lufs",
        "output.audio_bitrate_kbps",
        "output.audio_track_index",
    ],
}


def _relevant(stage: str, options: JobOptions) -> dict[str, Any]:
    keys = _AFFECTS.get(stage, [])
    relevant: dict[str, Any] = {}
    for key in keys:
        section, _, field = key.partition(".")
        value = getattr(options, section, None)
        if field:
            value = getattr(value, field, None)
        else:
            value = value.model_dump(mode="json") if value is not None else None
        relevant[key] = value
    return relevant


def stage_key(stage: str, options: JobOptions, inputs: list[str]) -> str:
    """Stable hash of the stage, the options that affect it, and its inputs."""
    payload = {
        "stage": stage,
        "options": _relevant(stage, options),
        "inputs": [str(path) for path in inputs],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()[:24]


def read_done_marker(directory: Path, stage: str) -> str | None:
    path = Path(directory) / f"{stage}.done"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def write_done_marker(directory: Path, stage: str, key: str) -> None:
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = Path(directory) / f"{stage}.done"
    tmp = path.with_suffix(".done.partial")
    tmp.write_text(key, encoding="utf-8")
    tmp.replace(path)


def remove_partial(directory: Path, stage: str) -> None:
    for suffix in (".partial", ".done.partial"):
        path = Path(directory) / f"{stage}{suffix}"
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
