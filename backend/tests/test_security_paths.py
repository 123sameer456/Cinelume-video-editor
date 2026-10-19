"""Path-traversal and access-token checks (phase 7.2)."""

from __future__ import annotations

import pytest

from app.core.errors import ErrorCode, PipelineError
from app.core.paths import safe_child


def test_safe_child_rejects_traversal(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PipelineError) as exc:
        safe_child(root, "..\\..\\secrets.txt")
    assert exc.value.code == ErrorCode.BAD_REQUEST


def test_safe_child_rejects_absolute_escape(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PipelineError):
        safe_child(root, "C:\\Windows\\system32\\config.sys")


def test_safe_child_allows_normal_names(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    child = safe_child(root, "source.mp4")
    assert child.parent == root.resolve()


def test_access_token_required_when_set(client, monkeypatch) -> None:
    from app.deps import get_manager

    manager = get_manager()
    original = manager.settings.access_token
    monkeypatch.setattr(manager.settings, "access_token", "sekrit")
    try:
        denied = client.get("/api/jobs")
        assert denied.status_code == 401
        client.headers.update({"Authorization": "Bearer sekrit"})
        allowed = client.get("/api/jobs")
        assert allowed.status_code == 200
    finally:
        client.headers.pop("Authorization", None)
        monkeypatch.setattr(manager.settings, "access_token", original)


def test_diagnostics_endpoint(client, make_video) -> None:
    from conftest import upload_file, wait_for_status

    upload_id, _ = upload_file(client, make_video(duration=2.0))
    job = client.post(
        "/api/jobs",
        json={
            "upload_id": upload_id,
            "options": {
                "subtitles": {"enabled": False},
                "performance": {"chunking": "off"},
            },
        },
    ).json()
    final = wait_for_status(client, job["id"], {"completed", "failed"}, timeout=180)

    resp = client.get(f"/api/jobs/{final['id']}/diagnostics")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert len(resp.content) > 100
