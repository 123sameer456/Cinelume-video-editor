"""Envelope and auto-threshold (phase 2.3)."""

from __future__ import annotations

import numpy as np
import psutil

from app.services.envelope import auto_threshold, looks_all_silent, rms_envelope_db


def _db_at(envelope, seconds: float) -> float:
    index = min(len(envelope.values) - 1, int(seconds / envelope.hop_sec))
    return float(envelope.values[index])


def test_envelope_shape_and_levels(make_wav) -> None:
    path = make_wav(duration=4.0, bursts=[(0.5, 1.5)], noise=0.02)
    env = rms_envelope_db(path)
    assert env.sample_rate == 48000
    assert env.hop == 960  # 20 ms at 48 kHz
    assert env.hop_sec == 0.02
    assert abs(env.duration - 4.0) < 0.05
    assert len(env.values) == int(round(env.duration / env.hop_sec))

    tone = _db_at(env, 1.0)
    silence = _db_at(env, 3.0)
    assert tone > -12  # ~0.6 amplitude sine
    assert silence < -30
    assert tone - silence > 20


def test_auto_threshold_is_clamped_and_between_floor_and_speech(make_wav) -> None:
    path = make_wav(duration=4.0, bursts=[(0.5, 1.5)], noise=0.02)
    env = rms_envelope_db(path)
    threshold = auto_threshold(env.values)
    assert -55.0 <= threshold <= -25.0
    floor = float(np.percentile(env.values, 10))
    speech = float(np.percentile(env.values, 90))
    assert floor - 1 < threshold < speech + 1


def test_looks_all_silent(make_wav) -> None:
    quiet = rms_envelope_db(make_wav(name="quiet.wav", bursts=[(0.0, 0.0)], noise=0.001))
    assert looks_all_silent(quiet.values, -35.0) is True

    loud = rms_envelope_db(make_wav(name="loud.wav", bursts=[(0.0, 2.0)], noise=0.001))
    assert looks_all_silent(loud.values, -35.0) is False


def test_envelope_memory_stays_flat_for_long_audio(make_wav) -> None:
    # 5 minutes at a 20 ms hop -> 15000 values, and RSS must not grow with audio length.
    path = make_wav(name="long.wav", duration=300.0, bursts=[(0.0, 300.0)])
    process = psutil.Process()
    before = process.memory_info().rss
    env = rms_envelope_db(path)
    growth = process.memory_info().rss - before
    assert len(env.values) == 15000
    assert env.values.nbytes < 256_000
    assert growth < 50 * 1024 * 1024, f"RSS grew by {growth / 1e6:.1f} MB"
