"""RMS envelope in dBFS, computed in blocks so memory is proportional to hop count."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

DEFAULT_HOP_SEC = 0.02
_FLOOR_DB = -140.0


@dataclass
class Envelope:
    values: np.ndarray  # dBFS per hop
    hop: int  # samples per envelope step
    sample_rate: int
    duration: float  # seconds of audio actually read

    @property
    def hop_sec(self) -> float:
        return self.hop / self.sample_rate


def rms_envelope_db(audio_path: Path, hop_sec: float = DEFAULT_HOP_SEC) -> Envelope:
    """Read a WAV in blocks and return one RMS(dBFS) value per `hop_sec`."""
    with sf.SoundFile(str(audio_path)) as handle:
        sample_rate = handle.samplerate
        channels = handle.channels
        hop = max(1, int(round(hop_sec * sample_rate)))
        block_frames = hop * 4096

        chunks: list[np.ndarray] = []
        carry = np.zeros(0, dtype=np.float64)
        total_frames = 0

        while True:
            data = handle.read(block_frames, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            total_frames += len(data)
            mono = data.mean(axis=1).astype(np.float64)
            buffer = np.concatenate([carry, mono]) if carry.size else mono
            full = len(buffer) // hop
            if full:
                frames = buffer[: full * hop].reshape(full, hop)
                chunks.append(np.sqrt(np.mean(frames * frames, axis=1)))
            carry = buffer[full * hop :]

        if carry.size:
            chunks.append(np.array([np.sqrt(np.mean(carry * carry))]))

    rms = np.concatenate(chunks) if chunks else np.zeros(1)
    if channels == 0:
        channels = 1
    duration = total_frames / sample_rate if sample_rate else 0.0
    values = 20.0 * np.log10(np.maximum(rms, 10 ** (_FLOOR_DB / 20.0)))
    return Envelope(values=values, hop=hop, sample_rate=sample_rate, duration=duration)


def auto_threshold(env_db: np.ndarray) -> float:
    """Noise floor from the 10th percentile, speech from the 90th; clamped to [-55,-25]."""
    if env_db.size == 0:
        return -35.0
    floor = float(np.percentile(env_db, 10))
    speech = float(np.percentile(env_db, 90))
    threshold = floor + 0.35 * (speech - floor)
    return float(min(-25.0, max(-55.0, threshold)))


def looks_all_silent(env_db: np.ndarray, threshold_db: float) -> bool:
    """True when essentially the whole file sits below the threshold."""
    if env_db.size == 0:
        return True
    return float(np.percentile(env_db, 99)) < threshold_db
