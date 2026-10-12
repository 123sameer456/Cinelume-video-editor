"""Cover and music API endpoints (phase 4.1 / 4.3)."""

from __future__ import annotations

from conftest import upload_file


def test_music_upload_list_delete(client, make_music) -> None:
    track = make_music(duration=2.0)
    created = client.post(
        "/api/music",
        content=track.read_bytes(),
        headers={"x-filename": "music.wav", "content-type": "audio/wav"},
    )
    assert created.status_code == 201, created.text
    track_id = created.json()["id"]
    assert created.json()["duration"] > 1.5

    listed = client.get("/api/music").json()["tracks"]
    assert any(t["id"] == track_id for t in listed)

    preview = client.get(f"/api/music/{track_id}/file")
    assert preview.status_code == 200

    assert client.delete(f"/api/music/{track_id}").status_code == 204
    assert not any(t["id"] == track_id for t in client.get("/api/music").json()["tracks"])


def test_music_upload_rejects_video(client, make_video) -> None:
    video = make_video(duration=1.0)
    resp = client.post(
        "/api/music",
        content=video.read_bytes(),
        headers={"x-filename": "clip.mp4", "content-type": "video/mp4"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNSUPPORTED_MEDIA"


def test_cover_endpoint_renders_and_re_rolls(client, make_talking_video) -> None:
    upload_id, _ = upload_file(client, make_talking_video(duration=6.0))
    options = {"lines": ["Cinelume", "auto editor"], "seed": 11}

    first = client.post("/api/cover", json={"upload_id": upload_id, "options": options})
    assert first.status_code == 200, first.text
    body = first.json()
    assert "time_sec" in body and body["image_url"]
    assert body["warnings"] == []

    image = client.get(body["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/")

    # re-roll with a new seed, without re-running a job
    second = client.post(
        "/api/cover", json={"upload_id": upload_id, "options": {**options, "seed": 22}}
    )
    assert second.status_code == 200
    assert second.json()["image_url"] == body["image_url"]


def test_cover_for_deleted_upload_is_404(client, make_talking_video) -> None:
    upload_id, _ = upload_file(client, make_talking_video(duration=3.0))
    client.delete(f"/api/uploads/{upload_id}")
    resp = client.post("/api/cover", json={"upload_id": upload_id, "options": {"lines": ["x"]}})
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_music_enabled_without_track_is_rejected(client) -> None:
    resp = client.post(
        "/api/jobs", json={"upload_id": "nope", "options": {"music": {"enabled": True}}}
    )
    assert resp.status_code == 422  # the options model validates before the job starts
