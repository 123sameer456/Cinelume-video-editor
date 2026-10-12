"""Master stage: music mix, ducking, loudness, faststart (phase 4.2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.core.errors import ErrorCode, PipelineError
from app.models.media import MediaInfo
from app.models.options import JobOptions
from app.services.ffmpeg import run_ffprobe_json
from app.services.master import _audio_graph, _ducking_params, run_master
from app.services.music import _measure_loudness, add_track
from app.services.probe import probe
from conftest import ffmpeg_cmd


def _open(path: Path):
    return open(path, "rb")


def _rms_db(path: Path, start: float, end: float, tmp: Path) -> float:
    # libsndfile cannot open MP4, so decode the range with ffmpeg first.
    wav = tmp / f"decode_{abs(hash((start, end))) % 10000}.wav"
    ffmpeg_cmd(
        [
            "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path),
            "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", "-y", str(wav),
        ]
    )
    with sf.SoundFile(str(wav)) as handle:
        data = handle.read(dtype="float32", always_2d=True)
    wav.unlink(missing_ok=True)
    rms = float(np.sqrt(np.mean(data * data))) if len(data) else 0.0
    return 20.0 * np.log10(max(rms, 1e-9))


def _silent_video(tmp_path: Path, duration: float, name: str = "silent.mp4") -> Path:
    """Video with a silent audio track, so the music is the only audio."""
    out = tmp_path / name
    ffmpeg_cmd(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
            "-f", "lavfi", "-i", f"aevalsrc='0':s=48000:d={duration}",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(out),
        ]
    )
    return out


def _master(video: Path, tmp_path: Path, options: JobOptions, name: str, workdir: str) -> Path:
    return run_master(video, options, tmp_path / name, workdir=tmp_path / workdir)


def test_master_loudness_is_near_target(tmp_path, make_video) -> None:
    out = _master(make_video(duration=3.0), tmp_path, JobOptions(), "final.mp4", "w")
    streams = {s["codec_type"]: s for s in run_ffprobe_json(out)["streams"]}
    assert streams["audio"]["codec_name"] == "aac"
    measured = _measure_loudness(out)
    assert abs(measured["input_i"] - (-14.0)) <= 1.0


def test_master_output_has_faststart(tmp_path, make_video) -> None:
    out = _master(make_video(duration=3.0), tmp_path, JobOptions(), "fast.mp4", "wf")
    data = run_ffprobe_json(out)
    assert "isom" in (data["format"].get("tags", {}).get("major_brand") or "")
    assert data["format"].get("format_name", "").startswith("mov,mp4")


def test_master_music_volume_changes_level(tmp_path, make_music) -> None:
    video = _silent_video(tmp_path, 4.0)
    track = add_track("music.wav", _open(make_music(duration=3.0)))
    options = {"enabled": True, "track_id": track["id"]}

    low = _master(
        video,
        tmp_path,
        JobOptions(music={**options, "volume": 8}, output={"loudnorm": False}),
        "v8.mp4",
        "w8",
    )
    high = _master(
        video,
        tmp_path,
        JobOptions(music={**options, "volume": 20}, output={"loudnorm": False}),
        "v20.mp4",
        "w20",
    )

    low_db = _rms_db(low, 2.0, 3.5, tmp_path)
    high_db = _rms_db(high, 2.0, 3.5, tmp_path)
    assert high_db > low_db + 5  # 20*log10(20/8) ~= 8 dB


def test_master_music_shorter_than_video_loops(tmp_path, make_music) -> None:
    video = _silent_video(tmp_path, 10.0)
    track = add_track("music.wav", _open(make_music(duration=2.0)))
    out = _master(
        video,
        tmp_path,
        JobOptions(
            music={"enabled": True, "track_id": track["id"], "volume": 40},
            output={"loudnorm": False},
        ),
        "loop.mp4",
        "wl",
    )
    decoded = _decode(out, tmp_path)
    with sf.SoundFile(str(decoded)) as handle:
        window = handle.read(dtype="float32", always_2d=True)
    jump = float(np.abs(np.diff(window, axis=0)).max())
    assert jump < 0.3, f"possible click at the loop point (jump={jump:.3f})"


def _decode(path: Path, tmp: Path) -> Path:
    """Decode a whole file to mono PCM (libsndfile cannot open MP4)."""
    wav = tmp / "loop_decode.wav"
    ffmpeg_cmd(["-i", str(path), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", "-y", str(wav)])
    return wav


def test_master_ducking_runs_and_produces_output(tmp_path, make_music) -> None:
    video = _silent_video(tmp_path, 4.0)
    track = add_track("music.wav", _open(make_music(duration=3.0)))
    out = _master(
        video,
        tmp_path,
        JobOptions(
            music={
                "enabled": True,
                "track_id": track["id"],
                "volume": 50,
                "ducking_enabled": True,
                "ducking_strength": 80,
            }
        ),
        "duck.mp4",
        "wd",
    )
    assert out.exists() and out.stat().st_size > 0


def test_master_without_audio_but_with_music(tmp_path, make_video, make_music) -> None:
    video = make_video(duration=3.0, with_audio=False)
    track = add_track("music.wav", _open(make_music(duration=2.0)))
    out = _master(
        video,
        tmp_path,
        JobOptions(music={"enabled": True, "track_id": track["id"], "volume": 30}),
        "nomus.mp4",
        "wn",
    )
    streams = {s["codec_type"] for s in run_ffprobe_json(out)["streams"]}
    assert "audio" in streams  # the music alone became the track


def test_master_without_any_audio_copies_video(tmp_path, make_video) -> None:
    out = _master(
        make_video(duration=3.0, with_audio=False), tmp_path, JobOptions(), "bare.mp4", "wv"
    )
    streams = {s["codec_type"] for s in run_ffprobe_json(out)["streams"]}
    assert "audio" not in streams


def test_master_with_missing_track_is_not_found(tmp_path, make_video) -> None:
    with pytest.raises(PipelineError) as exc:
        _master(
            make_video(duration=3.0),
            tmp_path,
            JobOptions(music={"enabled": True, "track_id": "gone", "volume": 8}),
            "gone.mp4",
            "wm",
        )
    assert exc.value.code == ErrorCode.NOT_FOUND


def test_loudnorm_is_guarded_on_silent_audio(tmp_path) -> None:
    out = _master(_silent_video(tmp_path, 3.0), tmp_path, JobOptions(), "s.mp4", "ws")
    assert out.exists()


def test_ducking_params_map_strength() -> None:
    low = _ducking_params(0)
    high = _ducking_params(100)
    # threshold is a LINEAR amplitude in [0.000976563, 1], not dB
    assert low[0] == pytest.approx(10 ** (-50 / 20), abs=1e-4)
    assert high[0] == pytest.approx(10 ** (-10 / 20), abs=1e-3)
    assert low[1] == 1.0 and high[1] == 10.0
    assert low[2] == 500.0 and high[2] == 100.0


def test_audio_graph_uses_sidechain_only_when_ducking(tmp_path, make_video, make_music) -> None:
    track = add_track("music.wav", _open(make_music(duration=2.0)))
    info = probe(make_video(duration=4.0))
    assert isinstance(info, MediaInfo)

    plain = _audio_graph(
        JobOptions(music={"enabled": True, "track_id": track["id"], "volume": 10}), info, 4.0, 1
    )
    assert "amix" in plain and "sidechaincompress" not in plain

    ducked = _audio_graph(
        JobOptions(
            music={"enabled": True, "track_id": track["id"], "volume": 10, "ducking_enabled": True}
        ),
        info,
        4.0,
        1,
    )
    assert "sidechaincompress" in ducked and "amix" in ducked
    # speech is mixed in after being used as the sidechain
    assert ducked.index("sidechaincompress") < ducked.index("amix")
