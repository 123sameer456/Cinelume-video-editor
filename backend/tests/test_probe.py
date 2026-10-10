"""probe() against real synthetic files and crafted ffprobe JSON."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.errors import ErrorCode, PipelineError
from app.models.media import MediaInfo
from app.services import probe as probe_mod
from app.services.probe import probe, require_audio, validate_video_upload


def _ffprobe_payload(video: dict | None = None, audios: list[dict] | None = None, duration="2.0"):
    streams = []
    if video is not None:
        v = {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 640, "height": 360,
             "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "pix_fmt": "yuv420p",
             "start_time": "0.0", "disposition": {"attached_pic": 0}}
        v.update(video)
        streams.append(v)
    for i, a in enumerate(audios or []):
        stream = {"index": 10 + i, "codec_type": "audio", "codec_name": "aac", "channels": 2,
                  "sample_rate": "48000", "start_time": "0.0"}
        stream.update(a)
        streams.append(stream)
    return {"format": {"format_name": "mov,mp4", "duration": duration, "size": "1000"},
            "streams": streams}


def _patch(monkeypatch, payload):
    monkeypatch.setattr(probe_mod, "run_ffprobe_json", lambda path, **kw: payload)


def test_probe_real_video(make_video) -> None:
    path = make_video(duration=2.0)
    info = probe(path)
    assert info.has_video and info.has_audio
    assert info.display_width == 320 and info.display_height == 240
    assert 29 < info.fps < 31
    assert info.is_vfr is False
    assert info.audio_streams[0].sample_rate == 48000


def test_probe_no_audio(make_video) -> None:
    info = probe(make_video(with_audio=False))
    assert info.has_video and not info.has_audio
    assert any("no audio" in w.lower() for w in info.warnings)
    with pytest.raises(PipelineError) as exc:
        require_audio(info)
    assert exc.value.code == ErrorCode.NO_AUDIO_STREAM


def test_probe_audio_only(make_video) -> None:
    info = probe(make_video(audio_only=True))
    assert not info.has_video
    with pytest.raises(PipelineError) as exc:
        validate_video_upload(info, 180 * 60)
    assert exc.value.code == ErrorCode.NO_VIDEO_STREAM


def test_probe_random_bytes_is_unsupported_media(tmp_path: Path) -> None:
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(os.urandom(8192))
    with pytest.raises(PipelineError) as exc:
        probe(bad)
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA


def test_probe_text_file_is_unsupported_media(tmp_path: Path) -> None:
    # ffprobe reads arbitrary text as "ANSI art" (a tty demuxer); we must reject it.
    bad = tmp_path / "notes.txt"
    bad.write_text("this is not a video, it is plain text" * 50, encoding="utf-8")
    with pytest.raises(PipelineError) as exc:
        probe(bad)
    assert exc.value.code == ErrorCode.UNSUPPORTED_MEDIA


def test_probe_duration_limit(monkeypatch) -> None:
    info = MediaInfo(has_video=True, has_audio=True, duration=10_000, width=640, height=360)
    with pytest.raises(PipelineError) as exc:
        validate_video_upload(info, 60)
    assert exc.value.code == ErrorCode.LIMIT_EXCEEDED


def test_probe_rotation_swaps_display_dimensions(monkeypatch) -> None:
    payload = _ffprobe_payload(
        video={"width": 1920, "height": 1080, "side_data_list": [{"rotation": -90}]},
        audios=[{}],
    )
    _patch(monkeypatch, payload)
    info = probe(Path("x.mp4"))
    assert info.display_width == 1080 and info.display_height == 1920
    assert any("rotation" in w.lower() for w in info.warnings)


def test_probe_vfr_flagged(monkeypatch) -> None:
    _patch(monkeypatch, _ffprobe_payload(video={"avg_frame_rate": "30/1", "r_frame_rate": "60/1"}))
    info = probe(Path("x.mp4"))
    assert info.is_vfr is True
    assert any("variable frame rate" in w.lower() for w in info.warnings)


def test_probe_odd_dimensions_warn(monkeypatch) -> None:
    _patch(monkeypatch, _ffprobe_payload(video={"width": 321, "height": 241}))
    info = probe(Path("x.mp4"))
    assert any("odd" in w.lower() for w in info.warnings)


def test_probe_hdr_and_bit_depth(monkeypatch) -> None:
    _patch(
        monkeypatch,
        _ffprobe_payload(video={"color_transfer": "smpte2084", "pix_fmt": "yuv420p10le"}),
    )
    info = probe(Path("x.mp4"))
    assert info.is_hdr is True
    assert info.bit_depth == 10
    assert any("hdr" in w.lower() for w in info.warnings)


def test_probe_multiple_audio_tracks(monkeypatch) -> None:
    _patch(monkeypatch, _ffprobe_payload(audios=[{}, {}]))
    info = probe(Path("x.mp4"))
    assert len(info.audio_streams) == 2
    assert any("audio tracks" in w for w in info.warnings)


def test_probe_attached_pic_is_not_video(monkeypatch) -> None:
    payload = _ffprobe_payload(
        video={"disposition": {"attached_pic": 1}, "width": 300, "height": 300},
        audios=[{}],
    )
    _patch(monkeypatch, payload)
    info = probe(Path("x.mp3"))
    assert info.has_video is False
