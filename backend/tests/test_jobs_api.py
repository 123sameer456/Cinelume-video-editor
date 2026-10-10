"""Job endpoints: system, defaults, SSE progress + reconnect, cancel, resume, files."""

from __future__ import annotations

import json
import time

from conftest import upload_file, wait_for_status

TERMINAL = {"completed", "failed", "cancelled"}


def _create_job(client, upload_id: str, chunking: str = "off") -> str:
    resp = client.post(
        "/api/jobs",
        json={"upload_id": upload_id, "options": {"performance": {"chunking": chunking}}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _run_simple_job(client, make_video, duration: float = 2.0) -> str:
    upload_id, _ = upload_file(client, make_video(duration=duration))
    job_id = _create_job(client, upload_id, chunking="off")
    job = wait_for_status(client, job_id, TERMINAL, timeout=90)
    assert job["status"] == "completed", job
    return job_id


def test_system_and_defaults(client) -> None:
    system = client.get("/api/system").json()
    assert "ffmpeg version" in system["ffmpeg"]["ffmpeg_version"].lower()
    assert system["cpu"]["count"] >= 1
    assert system["ram"]["total"] > 0
    assert "workers" in system
    assert system["limits"]["max_upload_gb"] == 10
    assert "fonts" in system

    defaults = client.get("/api/options/defaults").json()
    assert defaults["silence"]["min_silence_sec"] == 0.5
    assert defaults["output"]["encoder"] == "auto"


def test_create_job_requires_complete_upload(client) -> None:
    resp = client.post("/api/jobs", json={"upload_id": "does-not-exist"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_job_streams_progress_and_completes(client, make_video) -> None:
    upload_id, _ = upload_file(client, make_video(duration=3.0))
    job_id = _create_job(client, upload_id, chunking="off")

    percentages: list[float] = []
    terminal = None
    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        assert stream.status_code == 200
        for line in stream.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            snap = json.loads(line[5:].strip())
            percentages.append(snap["overall_pct"])
            assert len(snap["chunks"]) >= 1
            if snap["status"] in TERMINAL:
                terminal = snap
                break

    assert terminal is not None and terminal["status"] == "completed", terminal
    assert terminal["overall_pct"] == 100.0
    assert max(percentages) > 0, percentages
    assert terminal["chunks"][0]["status"] == "done"


def test_events_snapshot_on_reconnect(client, make_video) -> None:
    job_id = _run_simple_job(client, make_video)

    # Reconnecting to a finished job must immediately yield a terminal snapshot.
    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        first = next(line for line in stream.iter_lines() if line.startswith("data:"))
        snap = json.loads(first[5:].strip())
    assert snap["job_id"] == job_id
    assert snap["status"] == "completed"
    assert snap["overall_pct"] == 100.0


def test_cancel_running_job_is_fast(client, make_video) -> None:
    upload_id, _ = upload_file(client, make_video(duration=8.0))
    job_id = _create_job(client, upload_id, chunking="on")
    wait_for_status(client, job_id, {"running"}, timeout=30)

    started = time.time()
    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200, resp.text
    job = wait_for_status(client, job_id, TERMINAL, timeout=10)
    elapsed = time.time() - started

    assert job["status"] == "cancelled", job
    assert elapsed < 2.0, f"cancel took {elapsed:.2f}s"


def test_resume_after_cancel_completes(client, make_video) -> None:
    upload_id, _ = upload_file(client, make_video(duration=2.0))
    job_id = _create_job(client, upload_id, chunking="off")
    client.post(f"/api/jobs/{job_id}/cancel")
    wait_for_status(client, job_id, TERMINAL, timeout=10)

    resp = client.post(f"/api/jobs/{job_id}/resume")
    assert resp.status_code == 200, resp.text
    job = wait_for_status(client, job_id, TERMINAL, timeout=90)
    assert job["status"] == "completed", job


def test_job_files_and_range_support(client, make_video) -> None:
    job_id = _run_simple_job(client, make_video)

    for kind, expected in (("transcript", "application/json"), ("log", "text/plain")):
        resp = client.get(f"/api/jobs/{job_id}/files/{kind}")
        assert resp.status_code == 200, (kind, resp.text)
        assert expected.split(";")[0] in resp.headers["content-type"]

    video = client.get(f"/api/jobs/{job_id}/files/video")
    assert video.status_code == 200
    size = int(video.headers["content-length"])
    assert size > 0

    ranged = client.get(
        f"/api/jobs/{job_id}/files/video", headers={"Range": "bytes=0-99"}
    )
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == f"bytes 0-99/{size}"
    assert len(ranged.content) == 100

    missing = client.get(f"/api/jobs/{job_id}/files/nope")
    assert missing.status_code == 404


def test_delete_job(client, make_video) -> None:
    job_id = _run_simple_job(client, make_video)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_list_jobs_contains_created(client, make_video) -> None:
    job_id = _run_simple_job(client, make_video)
    ids = [job["id"] for job in client.get("/api/jobs").json()]
    assert job_id in ids
