"""FFmpeg / ffprobe wrapper.

Rules enforced here (G2, G4):
- argument lists only, never shell=True
- per-call cwd, timeout scaled by the caller, stderr streamed to a log file
- progress parsed from `-progress pipe:1`
- cancel/timeout kills the whole child process tree with psutil
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import psutil

from ..core.config import get_settings
from ..core.errors import ErrorCode, PipelineError

ProgressCallback = Callable[[dict[str, Any]], None]


class CancelEvent(Protocol):
    def is_set(self) -> bool: ...


_FFMPEG_BASE = ["-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning"]

# Hardware encoders we test with a real 1-frame encode in capabilities().
_HW_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")
_REQUIRED_FILTERS = ("afftdn", "arnndn", "ass", "sidechaincompress", "loudnorm")


# --------------------------------------------------------------------------- kill
def kill_process_tree(proc: subprocess.Popen[Any], timeout: float = 2.0) -> None:
    """Terminate a process and all its children, escalating to kill()."""
    try:
        parent = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    procs = [parent]
    try:
        procs.extend(parent.children(recursive=True))
    except psutil.NoSuchProcess:
        pass
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    if proc.poll() is None:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def _log_tail(log_path: Path | None, lines: int = 20) -> str:
    if not log_path or not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:]).strip()


# ------------------------------------------------------------------------ ffmpeg
def run_ffmpeg(
    args: list[str],
    *,
    cwd: Path,
    timeout: float,
    on_progress: ProgressCallback | None = None,
    cancel_event: CancelEvent | None = None,
    log_path: Path | None = None,
    ffmpeg_path: str | None = None,
) -> dict[str, Any]:
    """Run ffmpeg with `args`, streaming progress and returning the last progress block.

    Raises PipelineError(FFMPEG_FAILED | TIMEOUT | CANCELLED).
    """
    if log_path is None:
        log_path = Path(tempfile.mkstemp(prefix="ffmpeg_", suffix=".log")[1])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    exe = ffmpeg_path or get_settings().ffmpeg_path
    full_args = [exe, *_FFMPEG_BASE, "-progress", "pipe:1", *args]

    last: dict[str, Any] = {}
    started = time.monotonic()

    with open(log_path, "ab") as log_fh:
        log_fh.write(f"\n$ {' '.join(full_args)}\n".encode("utf-8", errors="replace"))
        log_fh.flush()
        try:
            proc = subprocess.Popen(
                full_args,
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log_fh,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise PipelineError(
                ErrorCode.FFMPEG_MISSING,
                f"FFmpeg executable not found: {exe}",
                stage="ffmpeg",
                hint="Install FFmpeg (winget install Gyan.FFmpeg) or set FFMPEG_PATH.",
            ) from exc

        def _reader() -> None:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key == "out_time_us":
                    try:
                        us = int(value)
                    except ValueError:
                        continue
                    last["out_time_us"] = us
                    last["out_time_sec"] = us / 1_000_000
                elif key in ("frame", "fps", "speed", "progress", "total_size"):
                    last[key] = value
                if on_progress is not None:
                    on_progress(dict(last))

        reader = threading.Thread(target=_reader, name="ffmpeg-progress", daemon=True)
        reader.start()

        killed: PipelineError | None = None
        while reader.is_alive():
            if cancel_event is not None and cancel_event.is_set():
                killed = PipelineError(ErrorCode.CANCELLED, "Job cancelled.", stage="ffmpeg")
                break
            if timeout and (time.monotonic() - started) > timeout:
                killed = PipelineError(
                    ErrorCode.TIMEOUT,
                    f"FFmpeg exceeded the {timeout:.0f}s time budget.",
                    stage="ffmpeg",
                    hint="The input may be too long or the machine too slow.",
                )
                break
            reader.join(0.2)

        if killed is not None:
            kill_process_tree(proc)
            raise killed

        reader.join(5)
        returncode = proc.wait()
        last["returncode"] = returncode

    if returncode != 0:
        tail = _log_tail(log_path)
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED,
            "FFmpeg failed." + (f" Last output:\n{tail}" if tail else ""),
            stage="ffmpeg",
            hint="See the job log for the full FFmpeg output.",
        )
    return last


# ----------------------------------------------------------------------- ffprobe
def run_ffprobe_json(path: Path, timeout: float = 60.0) -> dict[str, Any]:
    exe = get_settings().ffprobe_path
    args = [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PipelineError(
            ErrorCode.FFMPEG_MISSING,
            f"ffprobe executable not found: {exe}",
            stage="probe",
            hint="Install FFmpeg (winget install Gyan.FFmpeg) or set FFPROBE_PATH.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(
            ErrorCode.TIMEOUT, "ffprobe timed out.", stage="probe"
        ) from exc

    if result.returncode != 0:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA,
            "Could not read the file with ffprobe.",
            stage="probe",
            hint=(result.stderr or "").strip()[-500:] or "The file may be corrupt.",
        )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA, "ffprobe returned invalid JSON.", stage="probe"
        ) from exc


# ------------------------------------------------------------------------ version
def ffmpeg_version() -> str:
    exe = get_settings().ffmpeg_path
    try:
        out = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PipelineError(
            ErrorCode.FFMPEG_MISSING, f"FFmpeg executable not found: {exe}"
        ) from exc
    first = (out.stdout or "").splitlines()
    return first[0].strip() if first else "unknown"


# ------------------------------------------------------------------- capabilities
def _list_ffmpeg(names_flag: str) -> set[str]:
    exe = get_settings().ffmpeg_path
    out = subprocess.run(
        [exe, "-hide_banner", names_flag],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    found: set[str] = set()
    for line in (out.stdout or "").splitlines():
        # lines look like "  TSC afftdn   A->A   Denoise..."
        parts = line.split()
        if len(parts) >= 2:
            found.add(parts[1])
    return found


def _trial_encoder(name: str) -> bool:
    """Return True only if a real 1-frame encode succeeds with this encoder."""
    exe = get_settings().ffmpeg_path
    args = [
        exe,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=128x128:rate=1",
        "-frames:v",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        name,
        "-f",
        "null",
        "-",
    ]
    try:
        res = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=25,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return res.returncode == 0


_capabilities_cache: dict[str, Any] | None = None


def capabilities(refresh: bool = False) -> dict[str, Any]:
    """FFmpeg version, required filters/encoders, and verified hardware encoders."""
    global _capabilities_cache
    if _capabilities_cache is not None and not refresh:
        return _capabilities_cache

    version = ffmpeg_version()
    filters = _list_ffmpeg("-filters")
    encoders = _list_ffmpeg("-encoders")

    hw = {name: _trial_encoder(name) for name in _HW_ENCODERS}

    _capabilities_cache = {
        "ffmpeg_version": version,
        "filters": {name: name in filters for name in _REQUIRED_FILTERS},
        "encoders": {
            "libx264": "libx264" in encoders,
            "aac": "aac" in encoders,
            **hw,
        },
        "hardware_encoders": [name for name, ok in hw.items() if ok],
    }
    return _capabilities_cache


def reset_capabilities_cache() -> None:
    global _capabilities_cache
    _capabilities_cache = None


_filters_cache: set[str] | None = None


def available_filters(refresh: bool = False) -> set[str]:
    global _filters_cache
    if _filters_cache is None or refresh:
        _filters_cache = _list_ffmpeg("-filters")
    return _filters_cache


def has_filter(name: str) -> bool:
    return name in available_filters()
