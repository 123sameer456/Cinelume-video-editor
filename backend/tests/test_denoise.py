"""Audio extraction and denoise (phase 2.1 / 2.2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from app.models.chunk import whole_file_chunk
from app.models.options import JobOptions
from app.services.denoise import (
    _afftdn_chain,
    denoise_audio,
    extract_audio,
    spectral_denoise_file,
    target_channels,
)
from app.services.probe import probe
from conftest import ffmpeg_cmd


def _rms_db(path: Path, start: float, end: float) -> float:
    with sf.SoundFile(str(path)) as handle:
        rate = handle.samplerate
        handle.seek(int(start * rate))
        data = handle.read(int((end - start) * rate), dtype="float32", always_2d=True)
    rms = float(np.sqrt(np.mean(data * data))) if len(data) else 0.0
    return 20.0 * np.log10(max(rms, 1e-9))


def test_afftdn_chain_maps_strength() -> None:
    low = _afftdn_chain(JobOptions(denoise={"strength": 0, "highpass_hz": 0}))
    high = _afftdn_chain(JobOptions(denoise={"strength": 100, "highpass_hz": 120}))
    assert "afftdn=nr=6.0:nf=-70.0" in low
    assert "highpass" not in low
    assert "afftdn=nr=30.0:nf=-30.0" in high
    assert "highpass=f=120" in high
    assert "alimiter" in high


def test_target_channels_mono_and_stereo(tmp_path: Path) -> None:
    mono = tmp_path / "mono.mp4"
    ffmpeg_cmd(["-f", "lavfi", "-i", "sine=f=440:d=1", "-c:a", "aac", str(mono)])
    stereo = tmp_path / "stereo.mp4"
    ffmpeg_cmd(
        [
            "-f", "lavfi", "-i", "sine=f=440:d=1",
            "-f", "lavfi", "-i", "sine=f=660:d=1",
            "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=stereo[out]",
            "-map", "[out]", "-c:a", "aac", str(stereo),
        ]
    )
    assert target_channels(probe(mono)) == 1
    assert target_channels(probe(stereo)) == 2


def test_extract_audio_is_48k_s16_and_keeps_mono(tmp_path: Path, make_video) -> None:
    video = make_video(duration=2.0)  # sine source is mono
    info = probe(video)
    options = JobOptions()
    chunk = whole_file_chunk(info.duration)
    out = extract_audio(tmp_path, chunk, video, options, info)
    meta = sf.info(str(out))
    assert meta.samplerate == 48000
    assert meta.channels == 1
    assert meta.subtype == "PCM_16"


def test_afftdn_denoise_runs_and_preserves_speech(tmp_path: Path, make_wav) -> None:
    wav = make_wav(duration=3.0, bursts=[(0.5, 2.5)], tone=0.6, noise=0.02)
    options = JobOptions(
        denoise={"enabled": True, "method": "afftdn", "strength": 100, "highpass_hz": 0}
    )
    out = denoise_audio(tmp_path, wav, options, warnings=[])
    silence_before = _rms_db(wav, 0.0, 0.4)
    silence_after = _rms_db(out, 0.0, 0.4)
    tone_before = _rms_db(wav, 1.0, 2.0)
    tone_after = _rms_db(out, 1.0, 2.0)
    # afftdn is per-band spectral subtraction: broadband white noise drops only a little,
    # but it must never make things worse, and speech must be preserved.
    assert silence_after < silence_before + 1.0
    assert abs(tone_after - tone_before) < 1.5


def test_denoise_disabled_copies_input(tmp_path: Path, make_wav) -> None:
    wav = make_wav(duration=1.0)
    options = JobOptions(denoise={"enabled": False})
    out = denoise_audio(tmp_path, wav, options, warnings=[])
    assert out.exists() and out.read_bytes() == wav.read_bytes()


def test_arnndn_without_model_falls_back_with_warning(tmp_path: Path, make_wav) -> None:
    wav = make_wav(duration=1.0)
    options = JobOptions(denoise={"enabled": True, "method": "arnndn"})
    warnings: list[str] = []
    out = denoise_audio(tmp_path, wav, options, warnings=warnings)
    assert out.exists()
    assert any("arnndn" in w for w in warnings)


def test_spectral_denoise_reduces_noise_and_keeps_speech(tmp_path: Path, make_wav) -> None:
    wav = make_wav(duration=3.0, bursts=[(0.5, 2.5)], tone=0.6, noise=0.05)
    out = tmp_path / "dn.wav"
    spectral_denoise_file(wav, out, strength=80)
    silence_before = _rms_db(wav, 0.0, 0.4)
    silence_after = _rms_db(out, 0.0, 0.4)
    tone_after = _rms_db(out, 1.0, 2.0)
    assert silence_after < silence_before - 5
    assert tone_after > -12
    # same length as the input (no dropped samples)
    assert abs(sf.info(str(out)).frames - sf.info(str(wav)).frames) <= 1
