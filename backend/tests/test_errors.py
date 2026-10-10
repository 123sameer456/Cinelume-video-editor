"""Error taxonomy, DB restart recovery, and progress math."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import HTTP_STATUS, ErrorCode, PipelineError
from app.pipeline.progress import ProgressTracker
from app.services.uploads import require_free_space
from app.storage.jobs_db import JobsDB


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_code_has_status_and_serializes(code: ErrorCode) -> None:
    exc = PipelineError(code, "boom", stage="unit", chunk=2, hint="try again")
    assert exc.http_status == HTTP_STATUS[code]
    payload = exc.to_dict()
    assert payload["code"] == code.value
    assert payload["message"] == "boom"
    assert payload["stage"] == "unit"
    assert payload["chunk"] == 2
    assert payload["hint"] == "try again"


def test_error_without_optional_fields() -> None:
    payload = PipelineError(ErrorCode.INTERNAL, "x").to_dict()
    assert "chunk" not in payload and "hint" not in payload


def test_disk_space_error(tmp_path: Path) -> None:
    with pytest.raises(PipelineError) as exc:
        require_free_space(tmp_path, 10**18, "upload")
    assert exc.value.code == ErrorCode.DISK_SPACE
    assert exc.value.http_status == 507


def test_not_found_status_two_tier() -> None:
    assert PipelineError(ErrorCode.NOT_FOUND, "x").http_status == 404
    assert PipelineError(ErrorCode.CONFLICT, "x").http_status == 409
    assert PipelineError(ErrorCode.TIMEOUT, "x").http_status == 504


def test_restart_marks_running_jobs_interrupted(tmp_path: Path) -> None:
    db = JobsDB(tmp_path / "jobs.db")
    db.create_job({"id": "a", "upload_id": "u1", "status": "running", "options": {}})
    db.create_job({"id": "b", "upload_id": "u2", "status": "queued", "options": {}})
    db.create_job({"id": "c", "upload_id": "u3", "status": "completed", "options": {}})

    count = db.mark_running_interrupted()
    assert count == 1
    assert db.get_job("a")["status"] == "interrupted"
    assert db.get_job("b")["status"] == "queued"
    assert db.get_job("c")["status"] == "completed"
    db.close()


def test_jobs_db_roundtrip(tmp_path: Path) -> None:
    db = JobsDB(tmp_path / "jobs.db")
    db.create_job(
        {"id": "j1", "upload_id": "u", "status": "queued", "options": {"a": 1}, "warnings": ["w"]}
    )
    db.set_chunks("j1", [{"index": 0, "src_start": 0, "src_end": 5}])
    db.update_chunk("j1", 0, status="running", stage="audio", stage_pct=0.5)
    db.update_job("j1", overall_pct=42.5, error={"code": "X"})

    job = db.get_job("j1")
    assert job["overall_pct"] == 42.5
    assert job["warnings"] == ["w"]
    assert job["error"] == {"code": "X"}
    chunks = db.get_chunks("j1")
    assert chunks[0]["stage"] == "audio" and chunks[0]["stage_pct"] == 0.5

    # cascade delete removes chunks
    assert db.delete_job is not None
    db.delete_job("j1")
    assert db.get_job("j1") is None
    assert db.get_chunks("j1") == []
    db.close()


def test_progress_is_duration_weighted() -> None:
    tracker = ProgressTracker(
        [
            {"index": 0, "duration": 100.0},
            {"index": 1, "duration": 100.0},
        ]
    )
    assert tracker.overall_pct() == 0.0
    # finish chunk 0 completely -> half the media portion (0.9) = 45%
    tracker.set_status(0, "done")
    assert 40 < tracker.overall_pct() < 50
    # tail completes -> 100
    tracker.set_status(1, "done")
    tracker.set_tail(1.0)
    assert tracker.overall_pct() == pytest.approx(100.0)


def test_progress_stage_weights() -> None:
    tracker = ProgressTracker([{"index": 0, "duration": 10.0}])
    tracker.set_stage(0, "audio", 1.0)  # 0.15 of the 0.9 media band
    pct = tracker.overall_pct()
    assert pct == pytest.approx(0.15 * 0.9 * 100, rel=1e-3)
