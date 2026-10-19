"""Stage cache keys, resume markers, and chunked-vs-single equivalence (phase 5.5)."""

from __future__ import annotations

from pathlib import Path

from app.models.options import JobOptions
from app.pipeline.cache import read_done_marker, stage_key, write_done_marker
from conftest import upload_file, wait_for_status


def test_cache_keys_change_only_for_affected_stages() -> None:
    base = JobOptions()
    inputs = ["input.mp4"]

    audio = stage_key("audio", base, inputs)
    transcribe = stage_key("transcribe", base, inputs)
    video = stage_key("video", base, inputs)
    master = stage_key("master", base, inputs)

    # a subtitle colour only invalidates the video pass
    colour = JobOptions(subtitles={"text_color": "#FF0000"})
    assert stage_key("audio", colour, inputs) == audio
    assert stage_key("transcribe", colour, inputs) == transcribe
    assert stage_key("video", colour, inputs) != video
    assert stage_key("master", colour, inputs) == master

    # silence options invalidate audio (and therefore B and C)
    silence = JobOptions(silence={"min_silence_sec": 1.0})
    assert stage_key("audio", silence, inputs) != audio
    assert stage_key("video", silence, inputs) != video

    # music/loudness only invalidate the master
    music = JobOptions(music={"enabled": True, "track_id": "t", "volume": 30})
    assert stage_key("audio", music, inputs) == audio
    assert stage_key("master", music, inputs) != master

    # an input change invalidates everything
    assert stage_key("audio", base, ["other.mp4"]) != audio
    assert transcribe != video != master  # distinct keys per stage


def test_done_markers_roundtrip(tmp_path: Path) -> None:
    write_done_marker(tmp_path, "audio", "abc123")
    assert read_done_marker(tmp_path, "audio") == "abc123"
    assert read_done_marker(tmp_path, "video") is None


def test_stage_audio_entry_resumes_from_the_marker(tmp_path, make_talking_video) -> None:
    import json

    from app.pipeline.stage_entries import stage_audio_entry
    from app.services.probe import probe

    video = make_talking_video(duration=3.0)
    info = probe(video)
    options = JobOptions(subtitles={"enabled": False})
    options_json = json.dumps(options.model_dump(mode="json"))
    info_json = json.dumps(info.model_dump())
    workdir = tmp_path / "w"

    first = stage_audio_entry(
        0, 0.0, info.duration, False, str(video), str(workdir), options_json, info_json,
        None, None, str(workdir / "job.log"),
    )
    assert first["cached"] is False
    marker = workdir / "chunk_000" / "audio.done"
    assert marker.exists()

    # a second run must skip the work (same cache key)
    second = stage_audio_entry(
        0, 0.0, info.duration, False, str(video), str(workdir), options_json, info_json,
        None, None, str(workdir / "job.log"),
    )
    assert second["cached"] is True
    assert second["segments"] == first["segments"]


def test_chunked_and_single_chunk_give_the_same_duration(
    client, make_talking_video, tmp_path
) -> None:
    upload_id, _ = upload_file(client, make_talking_video(duration=120.0))
    no_subtitles = {"performance": {"chunking": "off"}, "subtitles": {"enabled": False}}

    single = client.post("/api/jobs", json={"upload_id": upload_id, "options": no_subtitles}).json()
    single_job = wait_for_status(client, single["id"], {"completed", "failed"}, timeout=600)
    assert single_job["status"] == "completed", (
        single_job["status"],
        single_job.get("error"),
        single_job.get("message"),
        single_job.get("stage"),
    )

    chunked_options = {
        "performance": {"chunking": "on", "chunk_threshold_sec": 60, "target_chunk_sec": 30},
        "subtitles": {"enabled": False},
    }
    chunked = client.post(
        "/api/jobs", json={"upload_id": upload_id, "options": chunked_options}
    ).json()
    chunked_job = wait_for_status(client, chunked["id"], {"completed", "failed"}, timeout=600)
    assert chunked_job["status"] == "completed", (
        chunked_job["status"],
        chunked_job.get("error"),
        chunked_job.get("message"),
        chunked_job.get("stage"),
    )

    # chunk states were tracked per chunk; chunks without speech are skipped
    assert len(chunked_job["chunks"]) >= 2
    assert all(c["status"] in ("done", "skipped") for c in chunked_job["chunks"])

    single_dur = _video_duration(client, single["id"], tmp_path)
    chunked_dur = _video_duration(client, chunked["id"], tmp_path)
    assert single_dur > 0 and chunked_dur > 0
    # 1 chunk vs 4 chunks: the removed silences are the same, so the durations match
    assert abs(single_dur - chunked_dur) < 0.3


def _video_duration(client, job_id: str, tmp: Path) -> float:
    from app.services.probe import probe

    resp = client.get(f"/api/jobs/{job_id}/files/video")
    path = tmp / f"{job_id}.mp4"
    path.write_bytes(resp.content)
    return float(probe(path).duration)
