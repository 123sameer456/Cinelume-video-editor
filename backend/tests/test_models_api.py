"""Model manager endpoints (phase 3.1)."""

from __future__ import annotations

import json
import os

import pytest

SIZES = {"tiny", "base", "small", "medium", "large-v3"}


def test_list_models(client) -> None:
    models = client.get("/api/models").json()["models"]
    sizes = {m["size"] for m in models}
    assert SIZES.issubset(sizes)
    for model in models:
        assert {"installed", "status", "progress", "approx_download_bytes"} <= set(model)


def test_unknown_model_size_is_rejected(client) -> None:
    assert client.post("/api/models/xxl/download").status_code == 404
    assert client.post("/api/models/xxl/cancel").status_code == 404


def test_cancel_is_a_noop_when_idle(client) -> None:
    resp = client.post("/api/models/tiny/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"idle", "done", "cancelled"}


@pytest.mark.skipif(not os.environ.get("CINELUME_SLOW"), reason="downloads a real model")
def test_download_tiny_model_with_sse(client) -> None:
    started = client.post("/api/models/tiny/download")
    assert started.status_code == 200
    assert started.json()["status"] == "downloading"

    snapshots = []
    with client.stream("GET", "/api/models/tiny/events") as stream:
        for line in stream.iter_lines():
            if not line.startswith("data:"):
                continue
            import json

            detail = json.loads(line[5:].strip())
            snapshots.append(detail)
            if detail["status"] in {"done", "failed", "cancelled"}:
                break
    assert snapshots[-1]["status"] == "done"
    assert snapshots[-1]["installed"] is True
    assert any(s["progress"] > 0 for s in snapshots)

    models = client.get("/api/models").json()["models"]
    assert next(m for m in models if m["size"] == "tiny")["installed"] is True


@pytest.mark.skipif(not os.environ.get("CINELUME_SLOW"), reason="downloads a real model")
def test_transcribe_with_real_tiny_model(client, make_talking_video, tmp_path) -> None:
    """@slow: real tiny model on a short synthetic clip."""
    import time

    from app.models.options import JobOptions
    from app.pipeline.stages import run_single_chunk
    from app.services.probe import probe

    detail = client.post("/api/models/tiny/download").json()
    started = time.time()
    while detail["status"] == "downloading" and time.time() - started < 600:
        time.sleep(1.0)
        models = client.get("/api/models").json()["models"]
        detail = next(m for m in models if m["size"] == "tiny")
    assert detail["status"] == "done", detail

    video = make_talking_video(duration=3.0, bursts=[(0.0, 1.0), (1.5, 3.0)])
    result = run_single_chunk(
        video,
        JobOptions(subtitles={"model_size": "tiny", "language": "en"}),
        tmp_path,
        info=probe(video),
    )
    assert result.output.exists()
    words_file = tmp_path / "chunk_000" / "words.json"
    assert words_file.exists()
    assert isinstance(json.loads(words_file.read_text(encoding="utf-8")), list)
