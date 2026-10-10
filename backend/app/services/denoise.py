"""Audio extraction and denoise (phase 2.1 / 2.2).

- `afftdn` (default): strength -> noise reduction dB + noise floor
- `arnndn`: needs an RNNoise `.rnnn` model in data/models/rnnoise/; falls back to
  afftdn with a warning when missing
- `noisereduce`: a self-contained numpy spectral-gate denoiser, processed in
  overlapping blocks so memory stays bounded (no heavy extra dependency)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from ..core.config import get_settings
from ..core.errors import ErrorCode, PipelineError
from ..models.chunk import ChunkSpec
from ..models.media import MediaInfo
from ..models.options import DenoiseMethod, JobOptions
from .ffmpeg import run_ffmpeg
from .probe import require_audio

SAMPLE_RATE = 48000

# Spectral denoiser (the "noisereduce" method) tuning.
_STFT_WIN = 1024
_BLOCK_SEC = 20.0
_OVERLAP_SEC = 1.0
_NOISE_PROFILE_SEC = 0.5


def target_channels(info: MediaInfo) -> int:
    """Mono stays mono; stereo and 5.1+ become 2-channel (never upmix mono)."""
    if not info.audio_streams:
        return 2
    return 1 if info.audio_streams[0].channels == 1 else 2


def audio_stream_position(info: MediaInfo, requested: int) -> int:
    count = len(info.audio_streams)
    if count == 0:
        require_audio(info, stage="audio")
    if 0 <= requested < count:
        return requested
    return 0


def _audio_duration(path: Path) -> float:
    try:
        return float(sf.info(str(path)).frames) / float(sf.info(str(path)).samplerate)
    except Exception:
        return 0.0


def extract_audio(
    workdir: Path,
    chunk: ChunkSpec,
    source: Path,
    options: JobOptions,
    info: MediaInfo,
    cancel_event=None,
) -> Path:
    """Extract the selected audio track for the chunk to audio.wav (48 kHz s16)."""
    require_audio(info, stage="audio")
    workdir = Path(workdir)
    out = workdir / "audio.wav"
    channels = target_channels(info)
    position = audio_stream_position(info, options.output.audio_track_index)

    args: list[str] = []
    if chunk.src_start > 0:
        args += ["-ss", f"{chunk.src_start:.6f}"]
    args += ["-t", f"{chunk.duration:.6f}"]
    args += ["-i", str(source)]
    args += ["-map", f"0:a:{position}", "-vn"]
    args += ["-af", "aresample=async=1:first_pts=0"]
    args += [
        "-ac",
        str(channels),
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-y",
        str(out),
    ]
    run_ffmpeg(
        args,
        cwd=workdir,
        timeout=max(120.0, 6 * chunk.duration),
        cancel_event=cancel_event,
        log_path=workdir / "ffmpeg.log",
    )
    return out


def denoise_audio(
    workdir: Path,
    audio_path: Path,
    options: JobOptions,
    warnings: list[str] | None = None,
    cancel_event=None,
) -> Path:
    """Write audio_dn.wav. Never loudness-normalizes (that happens at master)."""
    warnings = warnings if warnings is not None else []
    workdir = Path(workdir)
    out = workdir / "audio_dn.wav"
    denoise = options.denoise

    if not denoise.enabled:
        shutil.copyfile(audio_path, out)
        return out

    if denoise.method is DenoiseMethod.NOISEREDUCE:
        spectral_denoise_file(audio_path, out, denoise.strength, cancel_event=cancel_event)
        return out

    chain: str | None = None
    if denoise.method is DenoiseMethod.ARNNDN:
        chain = _arnndn_chain(workdir, warnings)
    if chain is None:
        chain = _afftdn_chain(options)

    run_ffmpeg(
        [
            "-i",
            str(audio_path),
            "-af",
            chain,
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-y",
            str(out),
        ],
        cwd=workdir,
        timeout=max(120.0, 6 * _audio_duration(audio_path)),
        cancel_event=cancel_event,
        log_path=workdir / "ffmpeg.log",
    )
    return out


def _afftdn_chain(options: JobOptions) -> str:
    """Map strength 0-100 to afftdn nr/nf, with optional high-pass and a limiter guard."""
    strength = options.denoise.strength
    nr = round(6.0 + 0.24 * strength, 2)  # ~6..30 dB
    nf = round(-70.0 + 0.40 * strength, 1)  # ~-70..-30 dB
    parts: list[str] = []
    if options.denoise.highpass_hz > 0:
        parts.append(f"highpass=f={options.denoise.highpass_hz}")
    parts.append(f"afftdn=nr={nr}:nf={nf}")
    parts.append("alimiter=limit=0.98")
    return ",".join(parts)


def _arnndn_chain(workdir: Path, warnings: list[str]) -> str | None:
    model = _find_rnnoise_model()
    if model is None:
        warnings.append(
            "arnndn model not found in data/models/rnnoise/; used afftdn instead."
        )
        return None
    local = workdir / "rnnoise.rnnn"
    try:
        shutil.copyfile(model, local)
    except OSError:
        warnings.append("Could not stage the arnndn model; used afftdn instead.")
        return None
    # Relative filename + cwd avoids the Windows drive-colon problem in filter args.
    return f"arnndn=m={local.name},alimiter=limit=0.98"


def _find_rnnoise_model() -> Path | None:
    directory = get_settings().models_dir / "rnnoise"
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.rnnn")):
        return path
    return None


# --------------------------------------------------------------- spectral gate
def _frames(data: np.ndarray, win: int, hop: int, window: np.ndarray) -> np.ndarray:
    """Windowed frames of shape (F, win, channels)."""
    n, ch = data.shape
    if n < win:
        data = np.pad(data, ((0, win - n), (0, 0)))
        n = win
    view = np.lib.stride_tricks.sliding_window_view(data, win, axis=0)[::hop]
    if view.ndim == 2:
        view = view[:, :, None]
    elif view.ndim == 3 and view.shape[1] == ch and view.shape[2] == win:
        view = view.transpose(0, 2, 1)
    return np.ascontiguousarray(view * window[:, None])


def _overlap_add(frames: np.ndarray, win: int, hop: int, window: np.ndarray, total: int):
    channels = frames.shape[2]
    length = max(total, (frames.shape[0] - 1) * hop + win) if frames.shape[0] else total
    out = np.zeros((length, channels), dtype=np.float64)
    wsum = np.zeros(length, dtype=np.float64)
    w2 = window * window
    for i in range(frames.shape[0]):
        s = i * hop
        out[s : s + win] += frames[i]
        wsum[s : s + win] += w2
    return out[:total], wsum[:total]


def _mean_magnitude(data: np.ndarray, win: int, hop: int, window: np.ndarray) -> np.ndarray:
    framed = _frames(data, win, hop, window)
    if framed.shape[0] == 0:
        return np.zeros((win // 2 + 1, data.shape[1]), dtype=np.float64)
    return np.mean(np.abs(np.fft.rfft(framed, axis=1)), axis=0)


def _denoise_block(
    data: np.ndarray,
    window: np.ndarray,
    hop: int,
    noise_mag: np.ndarray,
    alpha: float,
    floor: float,
) -> np.ndarray:
    win = len(window)
    framed = _frames(data, win, hop, window)
    if framed.shape[0] == 0:
        return data.copy()
    spec = np.fft.rfft(framed, axis=1)
    mag = np.abs(spec)
    reduced = np.maximum(mag - alpha * noise_mag[None, :, :], floor * mag)
    phase = np.divide(spec, mag + 1e-12, out=np.zeros_like(spec), where=mag > 0)
    frames = np.fft.irfft(reduced * phase, n=win, axis=1) * window[:, None]
    out, wsum = _overlap_add(frames, win, hop, window, len(data))
    return (out / np.maximum(wsum, 1e-8)[:, None]).astype(np.float32)


def _estimate_noise_profile(path: Path, window: np.ndarray, hop: int, channels: int) -> np.ndarray:
    win = len(window)
    info = sf.info(str(path))
    window_frames = max(win, int(_NOISE_PROFILE_SEC * info.samplerate))
    best: np.ndarray | None = None
    best_rms: float | None = None
    with sf.SoundFile(str(path)) as handle:
        position = 0
        while position + window_frames <= info.frames:
            handle.seek(position)
            data = handle.read(window_frames, dtype="float32", always_2d=True)
            rms = float(np.sqrt(np.mean(data * data))) if len(data) else 0.0
            if best_rms is None or rms < best_rms:
                best_rms, best = rms, data
            position += window_frames
        if best is None:
            handle.seek(0)
            best = handle.read(min(window_frames, info.frames), dtype="float32", always_2d=True)
    if best is None or len(best) == 0:
        return np.zeros((win // 2 + 1, channels), dtype=np.float64)
    return _mean_magnitude(best, win, hop, window)


def spectral_denoise_file(
    src: Path, dst: Path, strength: int, cancel_event=None
) -> None:
    """Spectral-subtraction denoise in overlapping blocks (bounded memory)."""
    window = np.hanning(_STFT_WIN).astype(np.float32)
    hop = _STFT_WIN // 4
    alpha = 1.0 + 2.0 * strength / 100.0
    floor = 0.02 + 0.08 * strength / 100.0

    info = sf.info(str(src))
    block = max(_STFT_WIN, int(_BLOCK_SEC * info.samplerate))
    overlap = min(block // 2, int(_OVERLAP_SEC * info.samplerate))
    step = block - overlap

    noise_mag = _estimate_noise_profile(src, window, hop, info.channels)

    pending: np.ndarray | None = None
    position = 0
    with sf.SoundFile(str(src)) as fin, sf.SoundFile(
        str(dst), "w", samplerate=info.samplerate, channels=info.channels, subtype="PCM_16"
    ) as fout:
        while position < info.frames:
            if cancel_event is not None and cancel_event.is_set():
                raise PipelineError(ErrorCode.CANCELLED, "Cancelled.", stage="denoise")
            fin.seek(position)
            data = fin.read(block, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            denoised = _denoise_block(data, window, hop, noise_mag, alpha, floor)
            if pending is None:
                body_end = max(0, len(denoised) - overlap)
                body = denoised[:body_end]
                pending = denoised[body_end:]
            else:
                m = min(overlap, len(pending), len(denoised))
                ramp = np.linspace(0.0, 1.0, m, dtype=np.float32)[:, None]
                head = pending[:m] * (1.0 - ramp) + denoised[:m] * ramp
                body_end = max(m, len(denoised) - overlap)
                body = np.concatenate([head, denoised[m:body_end]], axis=0)
                pending = denoised[body_end:]
            fout.write(body)
            position += step
        if pending is not None and len(pending):
            fout.write(pending)
