"""Music library: validation, loudness measurement, pre-normalize cache (phase 4.1)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.errors import ErrorCode, PipelineError
from app.services.music import (
    add_track,
    delete_track,
    get_track,
    list_tracks,
    normalize_track,
)


def _open(path: Path):
    return open(path, "rb")


def test_add_track_measures_loudness(make_music) -> None:
    path = make_music(duration=3.0)
    track = add_track("music.wav", _open(path))
    assert track["duration"] > 2.5
    assert track["integrated_lufs"] is not None
    assert -60 < track["integrated_lufs"] < 0
    assert track["normalized"] is False


def test_normalize_track_is_cached(make_music) -> None:
    path = make_music(duration=3.0)
    track = add_track("music.wav", _open(path))
    normalized = normalize_track(track["id"])
    assert normalized.exists()
    assert normalize_track(track["id"]) == normalized  # second call is a cache hit


def test_add_track_rejects_video(make_video) -> None:
    video = make_video(duration=1.0)
    with pytest.raises(PipelineError) as exc:
        add_track("video.mp4", _open(video))
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA


def test_add_track_rejects_bad_extension(tmp_path: Path) -> None:
    bad = tmp_path / "x.xyz"
    bad.write_bytes(os.urandom(2048))
    with pytest.raises(PipelineError) as exc:
        add_track("x.xyz", _open(bad))
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA


def test_add_track_rejects_corrupt(tmp_path: Path) -> None:
    corrupt = tmp_path / "c.mp3"
    corrupt.write_bytes(os.urandom(4096))
    with pytest.raises(PipelineError) as exc:
        add_track("c.mp3", _open(corrupt))
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA


def test_add_track_rejects_empty(tmp_path: Path) -> None:
    empty = tmp_path / "e.mp3"
    empty.write_bytes(b"")
    with pytest.raises(PipelineError) as exc:
        add_track("e.mp3", _open(empty))
    assert exc.value.code == ErrorCode.BAD_REQUEST


def test_list_get_delete_roundtrip(make_music) -> None:
    path = make_music(name="roundtrip.wav", duration=2.0)
    track = add_track("roundtrip.wav", _open(path))
    assert any(t["id"] == track["id"] for t in list_tracks())
    assert get_track(track["id"])["id"] == track["id"]
    assert delete_track(track["id"]) is True
    assert delete_track(track["id"]) is False
    assert not any(t["id"] == track["id"] for t in list_tracks())


def test_unknown_track_raises() -> None:
    with pytest.raises(PipelineError) as exc:
        get_track("does-not-exist")
    assert exc.value.code == ErrorCode.NOT_FOUND


def test_silent_track_normalizes_without_crashing(make_wav) -> None:
    silent = make_wav(name="silent.wav", bursts=[(0.0, 0.0)], tone=0.0, noise=0.0005)
    track = add_track("silent.wav", _open(silent))
    normalized = normalize_track(track["id"])
    assert normalized.exists()
