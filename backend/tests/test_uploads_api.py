"""Chunked upload endpoints: happy path, resume, validation, flat memory."""

from __future__ import annotations

import psutil

from conftest import upload_file


def _init(client, name: str, size: int):
    resp = client.post("/api/uploads", json={"filename": name, "total_size": size})
    assert resp.status_code == 201, resp.text
    return resp.json()["upload_id"]


def _put(client, upload_id: str, part_no: int, data: bytes):
    return client.put(f"/api/uploads/{upload_id}/parts/{part_no}", content=data)


def test_upload_happy_path_and_probe(client, make_video) -> None:
    video = make_video(duration=2.0)
    upload_id, info = upload_file(client, video)
    assert info["status"] == "complete"
    assert info["media"]["has_video"] and info["media"]["has_audio"]
    assert info["thumbnail_url"]

    thumb = client.get(f"/api/uploads/{upload_id}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"


def test_upload_resume_out_of_order_parts(client, make_video) -> None:
    video = make_video(duration=2.0)
    data = video.read_bytes()
    part_size = 64 * 1024
    parts = [data[i : i + part_size] for i in range(0, len(data), part_size)]
    assert len(parts) >= 3

    upload_id = _init(client, "clip.mp4", len(data))
    _put(client, upload_id, 0, parts[0])
    _put(client, upload_id, 2, parts[2])

    state = client.get(f"/api/uploads/{upload_id}").json()
    assert state["received_parts"] == [0, 2]

    # re-upload a part (idempotent) then send every remaining part (any order)
    _put(client, upload_id, 0, parts[0])
    for i in range(len(parts)):
        _put(client, upload_id, i, parts[i])

    complete = client.post(
        f"/api/uploads/{upload_id}/complete", json={"total_parts": len(parts)}
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "complete"


def test_upload_missing_part_fails(client, make_video) -> None:
    video = make_video(duration=1.5)
    data = video.read_bytes()
    part_size = 64 * 1024
    parts = [data[i : i + part_size] for i in range(0, len(data), part_size)]
    upload_id = _init(client, "clip.mp4", len(data))
    _put(client, upload_id, 0, parts[0])
    if len(parts) > 2:
        _put(client, upload_id, 2, parts[2])
    resp = client.post(f"/api/uploads/{upload_id}/complete", json={"total_parts": len(parts)})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_upload_size_mismatch_fails(client, make_video) -> None:
    video = make_video(duration=1.0)
    data = video.read_bytes()
    upload_id = _init(client, "clip.mp4", len(data) + 4096)
    _put(client, upload_id, 0, data)
    resp = client.post(f"/api/uploads/{upload_id}/complete", json={"total_parts": 1})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_upload_over_limit_rejected(client) -> None:
    resp = client.post(
        "/api/uploads", json={"filename": "huge.mp4", "total_size": 11 * 1024**3}
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "LIMIT_EXCEEDED"


def test_upload_audio_only_rejected(client, make_video) -> None:
    audio = make_video(name="song.m4a", audio_only=True, duration=2.0)
    size = audio.stat().st_size
    upload_id = _init(client, "song.m4a", size)
    _put(client, upload_id, 0, audio.read_bytes())
    resp = client.post(f"/api/uploads/{upload_id}/complete", json={"total_parts": 1})
    assert resp.status_code == 400
    assert resp.json()["code"] == "NO_VIDEO_STREAM"


def test_upload_part_number_out_of_range(client) -> None:
    upload_id = _init(client, "clip.mp4", 1000)
    resp = _put(client, upload_id, 999_999, b"x" * 10)
    assert resp.status_code == 400


class _ZeroStream:
    """File-like that yields zeros without ever holding the whole payload in RAM."""

    def __init__(self, total: int) -> None:
        self.remaining = total

    def read(self, size: int) -> bytes:
        if self.remaining <= 0:
            return b""
        take = min(size, self.remaining)
        self.remaining -= take
        return b"\0" * take


def test_streaming_write_keeps_memory_flat() -> None:
    """256 MiB streamed through save_part must not grow RSS like a full buffer would."""
    from app.services import uploads as up

    size = 256 * 1024 * 1024
    part_size = 8 * 1024 * 1024
    upload_id = up.init_upload("big.bin", size)["upload_id"]

    process = psutil.Process()
    baseline = process.memory_info().rss
    peak = baseline
    n_parts = size // part_size
    for i in range(n_parts):
        up.save_part(upload_id, i, _ZeroStream(part_size))
        peak = max(peak, process.memory_info().rss)

    growth = peak - baseline
    assert growth < 64 * 1024 * 1024, f"RSS grew by {growth / 1e6:.1f} MB while streaming"
    assert up.received_parts(upload_id) == list(range(n_parts))
