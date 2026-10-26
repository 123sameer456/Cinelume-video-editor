"""Shorts/clips endpoints: export ranges from a finished job's video, validation."""

from __future__ import annotations

from conftest import upload_file, wait_for_status

TERMINAL = {"completed", "failed", "cancelled"}


def _run_simple_job(client, make_video) -> str:
    upload_id, _ = upload_file(client, make_video(duration=3.0))
    resp = client.post(
        "/api/jobs",
        json={"upload_id": upload_id, "options": {"subtitles": {"enabled": False}}},
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    job = wait_for_status(client, job_id, TERMINAL, timeout=90)
    assert job["status"] == "completed", job
    return job_id


def test_export_and_download_clip(client, make_video) -> None:
    job_id = _run_simple_job(client, make_video)

    resp = client.post(
        f"/api/jobs/{job_id}/clips",
        json={"clips": [{"name": "Intro short", "start": 0.2, "end": 1.4}]},
    )
    assert resp.status_code == 200, resp.text
    clips = resp.json()["clips"]
    assert len(clips) == 1
    assert clips[0]["name"] == "Intro short"
    assert abs(clips[0]["duration"] - 1.2) < 0.2
    assert clips[0]["file_url"].endswith("/files/clip/0")

    download = client.get(f"/api/jobs/{job_id}/files/clip/0")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("video/mp4")
    assert len(download.content) > 0

    from pathlib import Path
    import tempfile

    from app.services.ffmpeg import run_ffprobe_json

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clip.mp4"
        path.write_bytes(download.content)
        data = run_ffprobe_json(path)
    probed = float(data["format"]["duration"])
    assert abs(probed - 1.2) < 0.3, probed


def test_clip_range_validation(client, make_video) -> None:
    job_id = _run_simple_job(client, make_video)

    resp = client.post(
        f"/api/jobs/{job_id}/clips",
        json={"clips": [{"name": "bad", "start": 2.0, "end": 1.0}]},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_clips_require_known_job(client) -> None:
    resp = client.post(
        "/api/jobs/deadbeef/clips",
        json={"clips": [{"name": "x", "start": 0.0, "end": 1.0}]},
    )
    assert resp.status_code == 404


def test_clip_download_before_export_is_404(client, make_video) -> None:
    job_id = _run_simple_job(client, make_video)
    resp = client.get(f"/api/jobs/{job_id}/files/clip/0")
    assert resp.status_code == 404
