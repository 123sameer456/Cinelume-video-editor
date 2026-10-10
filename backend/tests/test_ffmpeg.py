"""FFmpeg wrapper: progress, failure, timeout, cancel (process-tree kill)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import psutil
import pytest

from app.core.errors import ErrorCode, PipelineError
from app.services.ffmpeg import capabilities, ffmpeg_version, run_ffmpeg


def _encode_args(out: Path, duration: int = 2) -> list[str]:
    return [
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=128x128:rate=30",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]


def _long_args(out: Path) -> list[str]:
    return [
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30",
        "-t",
        "120",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]


def _ffmpeg_procs() -> list[psutil.Process]:
    found = []
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name.startswith("ffmpeg"):
            found.append(proc)
    return found


def test_run_ffmpeg_success_and_progress(tmp_path: Path) -> None:
    out = tmp_path / "out.mp4"
    seen: list[dict] = []
    result = run_ffmpeg(
        _encode_args(out, 2),
        cwd=tmp_path,
        timeout=60,
        on_progress=lambda p: seen.append(dict(p)),
        log_path=tmp_path / "ff.log",
    )
    assert result["returncode"] == 0
    assert out.exists() and out.stat().st_size > 0
    assert any(step.get("out_time_sec", 0) > 0 for step in seen)
    assert (tmp_path / "ff.log").read_text(encoding="utf-8").strip()


def test_run_ffmpeg_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(PipelineError) as exc:
        run_ffmpeg(
            _encode_args(tmp_path / "o.mp4"),
            cwd=tmp_path,
            timeout=10,
            ffmpeg_path="cinelume-no-such-binary",
            log_path=tmp_path / "ff.log",
        )
    assert exc.value.code == ErrorCode.FFMPEG_MISSING


def test_run_ffmpeg_failure(tmp_path: Path) -> None:
    with pytest.raises(PipelineError) as exc:
        run_ffmpeg(
            ["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "o.mp4")],
            cwd=tmp_path,
            timeout=30,
            log_path=tmp_path / "ff.log",
        )
    assert exc.value.code == ErrorCode.FFMPEG_FAILED
    assert exc.value.http_status == 500


def test_run_ffmpeg_timeout(tmp_path: Path) -> None:
    with pytest.raises(PipelineError) as exc:
        run_ffmpeg(
            _long_args(tmp_path / "o.mp4"),
            cwd=tmp_path,
            timeout=0.6,
            log_path=tmp_path / "ff.log",
        )
    assert exc.value.code == ErrorCode.TIMEOUT


def test_cancel_kills_process_tree_under_2s(tmp_path: Path) -> None:
    event = threading.Event()
    captured: dict = {}

    def worker() -> None:
        try:
            run_ffmpeg(
                _long_args(tmp_path / "o.mp4"),
                cwd=tmp_path,
                timeout=120,
                cancel_event=event,
                log_path=tmp_path / "ff.log",
            )
        except PipelineError as exc:
            captured["code"] = exc.code

    thread = threading.Thread(target=worker)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline and not _ffmpeg_procs():
        time.sleep(0.05)
    assert _ffmpeg_procs(), "ffmpeg should be running before we cancel"

    started = time.time()
    event.set()
    thread.join(timeout=6)
    elapsed = time.time() - started

    assert captured.get("code") == ErrorCode.CANCELLED
    assert elapsed < 2.0, f"cancel took {elapsed:.2f}s"

    gone_deadline = time.time() + 2
    while time.time() < gone_deadline and _ffmpeg_procs():
        time.sleep(0.05)
    assert not _ffmpeg_procs(), "ffmpeg process tree survived cancel"


def test_ffmpeg_version_and_capabilities() -> None:
    assert "ffmpeg version" in ffmpeg_version().lower()
    caps = capabilities()
    assert all(caps["filters"].values()), caps["filters"]
    assert caps["encoders"]["libx264"] and caps["encoders"]["aac"]
    assert isinstance(caps["hardware_encoders"], list)
