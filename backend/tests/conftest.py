"""Shared fixtures. Isolates DATA_DIR to a temp dir before app modules import."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

_TMP_DATA = Path(tempfile.mkdtemp(prefix="cinelume_data_"))
os.environ["DATA_DIR"] = str(_TMP_DATA)
os.environ["MAX_UPLOAD_GB"] = "10"
os.environ.pop("ACCESS_TOKEN", None)

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

PART_SIZE = 8 * 1024 * 1024


def ffmpeg_cmd(args: list[str], timeout: int = 180) -> None:
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        timeout=timeout,
    )
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_TMP_DATA, ignore_errors=True)


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return _TMP_DATA


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_video(tmp_path):
    """Factory that builds small synthetic videos with ffmpeg lavfi."""

    def _make(
        name: str = "clip.mp4",
        duration: float = 2.0,
        with_audio: bool = True,
        size: str = "320x240",
        fps: int = 30,
        audio_only: bool = False,
        blocky: bool = False,
    ) -> Path:
        out = tmp_path / name
        if audio_only:
            ffmpeg_cmd(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:duration={duration}",
                    "-c:a",
                    "aac",
                    str(out),
                ]
            )
            return out
        args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}"]
        if with_audio:
            args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
        args += ["-t", str(duration)]
        if blocky:
            args += ["-c:v", "libx264", "-preset", "ultrafast", "-g", "1"]
        else:
            args += ["-c:v", "libx264", "-preset", "ultrafast"]
        args += ["-pix_fmt", "yuv420p"]
        if with_audio:
            args += ["-c:a", "aac", "-shortest"]
        args += [str(out)]
        ffmpeg_cmd(args)
        return out

    return _make


@pytest.fixture
def make_talking_video(tmp_path):
    """Video whose audio is tone bursts separated by digital silence + low noise."""

    def _make(
        name: str = "talk.mp4",
        bursts: list[tuple[float, float]] | None = None,
        duration: float = 6.0,
        noise: float = 0.02,
        size: str = "320x240",
        fps: int = 30,
    ) -> Path:
        bursts = bursts or [(0.0, 1.2), (2.4, 3.6), (5.0, 6.0)]
        out = tmp_path / name
        terms = "+".join(f"between(t,{s},{e})" for s, e in bursts)
        expr = f"0.6*sin(2*PI*220*t)*({terms})+{noise}*random(0)"
        ffmpeg_cmd(
            [
                "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
                "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={duration}",
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest",
                str(out),
            ]
        )
        return out

    return _make


@pytest.fixture
def make_wav(tmp_path):
    """WAV with tone bursts and low-level noise (for envelope/denoise tests)."""

    def _make(
        name: str = "audio.wav",
        bursts: list[tuple[float, float]] | None = None,
        duration: float = 4.0,
        noise: float = 0.02,
        tone: float = 0.6,
    ) -> Path:
        out = tmp_path / name
        terms = "+".join(f"between(t,{s},{e})" for s, e in (bursts or [(0.0, duration)]))
        expr = f"{tone}*sin(2*PI*220*t)*({terms})+{noise}*random(0)"
        ffmpeg_cmd(
            [
                "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={duration}",
                "-c:a", "pcm_s16le",
                str(out),
            ]
        )
        return out

    return _make


def upload_file(client, path: Path, part_size: int = PART_SIZE) -> tuple[str, dict]:
    size = path.stat().st_size
    resp = client.post("/api/uploads", json={"filename": path.name, "total_size": size})
    assert resp.status_code == 201, resp.text
    upload_id = resp.json()["upload_id"]
    n = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(part_size)
            if not block:
                break
            part = client.put(f"/api/uploads/{upload_id}/parts/{n}", content=block)
            assert part.status_code == 204, part.text
            n += 1
    complete = client.post(f"/api/uploads/{upload_id}/complete", json={"total_parts": n})
    assert complete.status_code == 200, complete.text
    return upload_id, complete.json()


def wait_for_status(client, job_id: str, statuses: set[str], timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if last["status"] in statuses:
            return last
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {statuses}; last={last}")
