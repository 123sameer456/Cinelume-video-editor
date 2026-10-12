"""faster-whisper transcription, model management and device selection (phase 3.1-3.3).

Loading is fully offline after a one-time download (`local_files_only=True`). Downloads
run in a child process so cancel can kill them for real; progress is observed by polling
the model directory size.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from ..core.config import get_settings
from ..core.errors import ErrorCode, PipelineError
from ..core.logging import get_logger
from ..models.options import JobOptions
from ..models.subtitle import Word
from .ffmpeg import run_ffmpeg
from .uploads import require_free_space

log = get_logger("cinelume.asr")

MODEL_REPO = "Systran/faster-whisper-{size}"
# Approximate on-disk size of the CTranslate2 conversions (for disk checks + UI).
APPROX_MODEL_GB: dict[str, float] = {
    "tiny": 0.15,
    "base": 0.25,
    "small": 0.75,
    "medium": 1.8,
    "large-v3": 3.1,
}
_DOWNLOAD_TIMEOUT_SEC = 3600.0


def models_root() -> Path:
    return get_settings().models_dir / "whisper"


def model_dir(size: str) -> Path:
    return models_root() / str(size)


def is_installed(size: str) -> bool:
    return (model_dir(size) / "model.bin").exists()


def directory_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def list_models() -> list[dict[str, Any]]:
    manager = get_model_manager()
    result = []
    for size in APPROX_MODEL_GB:
        target = model_dir(size)
        result.append(
            {
                "size": size,
                "installed": is_installed(size),
                "disk_bytes": directory_size(target) if target.exists() else 0,
                "approx_download_bytes": int(APPROX_MODEL_GB[size] * 1e9),
                "status": manager.status(size),
                "progress": manager.progress(size),
            }
        )
    return result


# ------------------------------------------------------------------ Transcriber
class Transcriber(Protocol):
    """Protocol so tests can inject a fake."""

    def detect_language(self, audio_path: Path) -> str: ...

    def transcribe(
        self, audio_path: Path, language: str | None, options: JobOptions
    ) -> list[Word]: ...


@dataclass(frozen=True)
class _Candidate:
    device: str
    compute_type: str


def _candidates(requested: str) -> list[_Candidate]:
    if requested == "cpu":
        return [_Candidate("cpu", "int8")]
    # auto and cuda both try GPU first, then smaller compute types, then CPU.
    return [
        _Candidate("cuda", "float16"),
        _Candidate("cuda", "int8_float16"),
        _Candidate("cpu", "int8"),
    ]


def _is_out_of_memory(exc: Exception) -> bool:
    lowered = str(exc).lower()
    return "out of memory" in lowered or "oom" in lowered


# A failed CUDA init can make the NEXT attempt in this process hang, so remember it.
_cuda_broken = False


def _cuda_usable(warnings: list[str]) -> bool:
    global _cuda_broken
    if _cuda_broken:
        return False
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() == 0:
            return False
    except Exception as exc:  # pragma: no cover - depends on drivers
        warnings.append(f"CUDA not usable ({exc}); using CPU.")
        return False
    return True


class FasterWhisperTranscriber:
    """Loads the model once per worker process and reuses it for every chunk."""

    def __init__(self, size: str, requested_device: str, warnings: list[str] | None = None) -> None:
        if not is_installed(size):
            raise PipelineError(
                ErrorCode.MODEL_MISSING,
                f"Whisper model '{size}' is not downloaded.",
                stage="transcribe",
                hint="Download it from the UI (POST /api/models/{size}/download).",
            )
        self.size = size
        self.warnings = warnings if warnings is not None else []
        self.model = None
        self.device = "cpu"
        self.compute_type = "int8"
        self._load_with_fallback(requested_device)

    def _load_with_fallback(self, requested: str) -> None:
        from faster_whisper import WhisperModel

        last_error: Exception | None = None
        for candidate in _candidates(requested):
            if candidate.device == "cuda":
                if not _cuda_usable(self.warnings):
                    continue
            try:
                self.model = WhisperModel(
                    str(model_dir(self.size)),
                    device=candidate.device,
                    compute_type=candidate.compute_type,
                    local_files_only=True,
                    cpu_threads=max(1, (os.cpu_count() or 4) // 2),
                )
                self._smoke_test()
                self.device, self.compute_type = candidate.device, candidate.compute_type
                return
            except Exception as exc:
                last_error = exc
                self.model = None
                self.warnings.append(
                    f"{candidate.device}/{candidate.compute_type} failed ({exc}); "
                    "trying the next option."
                )
                if candidate.device == "cuda" and not _is_out_of_memory(exc):
                    # A broken CUDA runtime hangs on later attempts; fall back to CPU.
                    global _cuda_broken
                    _cuda_broken = True
        raise PipelineError(
            ErrorCode.ASR_FAILED,
            f"Could not initialise the Whisper model '{self.size}'.",
            stage="transcribe",
            hint=str(last_error) if last_error else None,
        )

    def _smoke_test(self) -> None:
        """A 1-second transcription to prove the device really works."""
        import numpy as np
        import soundfile as sf

        probe_path = models_root() / "device_probe.wav"
        sf.write(str(probe_path), np.zeros(16000, dtype="float32"), 16000, subtype="PCM_16")
        try:
            segments, _info = self.model.transcribe(str(probe_path), language="en", beam_size=1)
            for _ in segments:
                pass
        finally:
            probe_path.unlink(missing_ok=True)

    def beam_size(self) -> int:
        return 5 if self.device == "cuda" else 2

    def detect_language(self, audio_path: Path) -> str:
        """Detect on the first ~30 s of speech, then the language is locked for all chunks."""
        workdir = audio_path.parent
        sample = workdir / "language_probe.wav"
        try:
            run_ffmpeg(
                ["-i", audio_path.name, "-t", "30", "-ac", "1", "-ar", "16000", "-y", sample.name],
                cwd=workdir,
                timeout=120,
                log_path=workdir / "ffmpeg.log",
            )
            segments, info = self.model.transcribe(str(sample), beam_size=1)
            for _ in segments:
                pass
            language = info.language or "en"
        finally:
            sample.unlink(missing_ok=True)
        log.info(f"detected language: {language}")
        return language

    def transcribe(self, audio_path: Path, language: str | None, options: JobOptions) -> list[Word]:
        segments, _info = self.model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            beam_size=self.beam_size(),
            initial_prompt=options.subtitles.vocabulary_hint or None,
        )
        words: list[Word] = []
        for segment in segments:
            for token in segment.words or []:
                words.append(
                    Word(
                        text=str(token.word),
                        start=float(token.start),
                        end=float(token.end),
                        prob=float(token.probability),
                    )
                )
        return words


# --------------------------------------------------------------- model downloads
class ModelManager:
    """One download at a time per model size, cancellable, resumable, offline-safe."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._state: dict[str, dict[str, Any]] = {}

    def _lock(self, size: str) -> threading.Lock:
        with self._guard:
            if size not in self._locks:
                self._locks[size] = threading.Lock()
            return self._locks[size]

    def status(self, size: str) -> str:
        return self._state.get(size, {}).get("status", "idle")

    def progress(self, size: str) -> float:
        return float(self._state.get(size, {}).get("progress", 0.0))

    def detail(self, size: str) -> dict[str, Any]:
        return {
            "size": size,
            "status": self.status(size),
            "progress": self.progress(size),
            "message": self._state.get(size, {}).get("message"),
            "installed": is_installed(size),
        }

    def start(self, size: str) -> dict[str, Any]:
        if size not in APPROX_MODEL_GB:
            raise PipelineError(
                ErrorCode.NOT_FOUND, f"Unknown model size '{size}'.", stage="models"
            )
        lock = self._lock(size)
        if not lock.acquire(blocking=False):
            raise PipelineError(
                ErrorCode.CONFLICT, f"A download of '{size}' is already running.", stage="models"
            )
        with self._guard:
            self._state[size] = {"status": "downloading", "progress": 0.0, "message": None}
        thread = threading.Thread(
            target=self._run, name=f"dl-{size}", args=(size, lock), daemon=True
        )
        thread.start()
        return self.detail(size)

    def cancel(self, size: str) -> dict[str, Any]:
        event = self._cancel.get(size)
        if event is not None:
            event.set()
        proc = self._processes.get(size)
        if proc is not None:
            _kill_tree(proc)
        return self.detail(size)

    def _run(self, size: str, lock: threading.Lock) -> None:
        try:
            self._download(size)
        except PipelineError as exc:
            with self._guard:
                self._state[size] = {
                    "status": "cancelled" if exc.code == ErrorCode.CANCELLED else "failed",
                    "progress": 0.0,
                    "message": exc.message,
                }
            _remove_partial(size)
        except Exception as exc:  # pragma: no cover - network/OS errors
            with self._guard:
                self._state[size] = {"status": "failed", "progress": 0.0, "message": str(exc)}
            _remove_partial(size)
        finally:
            lock.release()
            self._cancel.pop(size, None)
            self._processes.pop(size, None)

    def _download(self, size: str) -> None:
        if is_installed(size):
            with self._guard:
                self._state[size] = {"status": "done", "progress": 100.0, "message": None}
            return
        cancel = threading.Event()
        self._cancel[size] = cancel
        require_free_space(
            models_root(), int(APPROX_MODEL_GB[size] * 1e9 * 1.4), "model-download"
        )
        models_root().mkdir(parents=True, exist_ok=True)
        target = model_dir(size)
        worker = Path(__file__).resolve().parent.parent / "_asr_download.py"
        # stderr goes to a file: piping it would deadlock once tqdm fills the buffer.
        log_file = models_root() / f"download-{size}.log"
        with open(log_file, "ab") as log_handle:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(worker),
                    MODEL_REPO.format(size=size),
                    str(target),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
            )
            self._processes[size] = proc
            expected = int(APPROX_MODEL_GB[size] * 1e9)
            started = time.monotonic()
            while proc.poll() is None:
                if cancel.is_set():
                    _kill_tree(proc)
                    raise PipelineError(
                        ErrorCode.CANCELLED, "Model download cancelled.", stage="models"
                    )
                if time.monotonic() - started > _DOWNLOAD_TIMEOUT_SEC:
                    _kill_tree(proc)
                    raise PipelineError(
                        ErrorCode.TIMEOUT, "Model download timed out.", stage="models"
                    )
                got = directory_size(target) if target.exists() else 0
                with self._guard:
                    self._state[size]["progress"] = min(99.0, 100.0 * got / max(expected, 1))
                time.sleep(0.4)

        if proc.returncode != 0:
            tail = ""
            try:
                tail = log_file.read_text(encoding="utf-8", errors="replace")[-500:]
            except OSError:
                pass
            raise PipelineError(
                ErrorCode.INTERNAL,
                f"Model download failed (exit {proc.returncode}).",
                stage="models",
                hint=tail or None,
            )
        if not is_installed(size):
            raise PipelineError(
                ErrorCode.MODEL_MISSING,
                "The download finished but the model is incomplete.",
                stage="models",
                hint="Try again; partial files were removed.",
            )
        with self._guard:
            self._state[size] = {"status": "done", "progress": 100.0, "message": None}


def _kill_tree(proc: subprocess.Popen) -> None:
    import psutil

    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        return
    for p in [*children, parent]:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs([*children, parent], timeout=3)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


def _remove_partial(size: str) -> None:
    target = model_dir(size)
    if target.exists() and not is_installed(size):
        shutil.rmtree(target, ignore_errors=True)


_manager: ModelManager | None = None
_manager_lock = threading.Lock()


def get_model_manager() -> ModelManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ModelManager()
        return _manager


def save_words(words: list[Word], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(w) for w in words], ensure_ascii=False, indent=1), encoding="utf-8"
    )
