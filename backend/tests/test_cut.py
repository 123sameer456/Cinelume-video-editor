"""End-to-end cut pipeline: silence removal, fades, A/V sync, batching (phase 2.5-2.6)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.models.chunk import ChunkSpec
from app.models.options import JobOptions
from app.pipeline.stages import run_single_chunk
from app.services.ffmpeg import run_ffprobe_json
from app.services.probe import probe
from app.services.render import Placed, playback_fps, render_video
from app.services.segments import Segment
from conftest import ffmpeg_cmd


def _stream_durations(path: Path) -> tuple[float, float | None]:
    data = run_ffprobe_json(path)
    format_duration = float(data.get("format", {}).get("duration") or 0.0)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    video_duration = float(video.get("duration") or format_duration)
    audio_duration = float(audio.get("duration") or format_duration) if audio else None
    return video_duration, audio_duration


def _fps(path: Path) -> float:
    """Numeric average frame rate ('30000/1001' or '118000/3933' style)."""
    raw = next(s for s in run_ffprobe_json(path)["streams"] if s["codec_type"] == "video").get(
        "avg_frame_rate", "0/1"
    )
    num, _, den = raw.partition("/")
    try:
        return float(num) / float(den) if den else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _luma(video: Path, t: float, out_png: Path) -> float:
    ffmpeg_cmd(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out_png)])
    return float(np.asarray(Image.open(out_png).convert("L")).mean())


def test_run_single_chunk_removes_silences_and_keeps_av_in_sync(
    tmp_path, make_talking_video
) -> None:
    video = make_talking_video()  # bursts 0-1.2, 2.4-3.6, 5-6 -> ~2.07s removable
    result = run_single_chunk(video, JobOptions(subtitles={"enabled": False}), tmp_path)

    assert result.output.exists()
    assert len(result.segments) == 3
    assert 3.5 < result.output_duration < 4.3
    assert 1.9 < result.removed_sec < 2.3

    video_dur, audio_dur = _stream_durations(result.output)
    assert audio_dur is not None
    assert abs(video_dur - audio_dur) < 1 / 30 + 0.02
    assert abs(video_dur - result.output_duration) < 0.2


def test_fades_are_visible_at_joins(tmp_path, make_talking_video) -> None:
    video = make_talking_video()
    result = run_single_chunk(video, JobOptions(subtitles={"enabled": False}), tmp_path)
    join = result.segments[0].end

    bright = _luma(result.output, join / 2, tmp_path / "mid.png")
    dark = _luma(result.output, max(0.0, join - 0.05), tmp_path / "join.png")
    assert dark < bright * 0.6, (dark, bright)


def test_changing_min_silence_changes_which_pauses_are_removed(
    tmp_path, make_talking_video
) -> None:
    # pauses: 0.4s (1.0->1.4) and 1.5s (2.4->3.9)
    video = make_talking_video(bursts=[(0.0, 1.0), (1.4, 2.4), (3.9, 4.9)], duration=4.9)

    short = run_single_chunk(
        video,
        JobOptions(silence={"min_silence_sec": 0.3}, subtitles={"enabled": False}),
        tmp_path / "short",
    )
    long = run_single_chunk(
        video,
        JobOptions(silence={"min_silence_sec": 1.0}, subtitles={"enabled": False}),
        tmp_path / "long",
    )
    assert len(short.segments) == 3  # both pauses cut
    assert len(long.segments) == 2  # only the 1.5s pause cut
    assert short.output_duration < long.output_duration


def test_video_without_audio_renders_video_only(tmp_path, make_video) -> None:
    video = make_video(duration=2.0, with_audio=False)
    result = run_single_chunk(video, JobOptions(subtitles={"enabled": False}), tmp_path)
    data = run_ffprobe_json(result.output)
    assert all(s["codec_type"] != "audio" for s in data["streams"])
    assert any("no audio" in w.lower() for w in result.warnings)


def test_batching_many_segments(tmp_path, make_video) -> None:
    # 300 realistic segments (>= a few frames each) spread over a 36s source.
    video = make_video(duration=36.0)
    info = probe(video)
    fps = playback_fps(JobOptions(), info)
    segments = [Segment(i * 0.12, i * 0.12 + 0.10) for i in range(300)]
    placed = [Placed(seg, i > 0, i < len(segments) - 1) for i, seg in enumerate(segments)]
    expected = sum(s.duration for s in segments)  # 30.0s

    options = JobOptions(performance={"max_segments_per_graph": 120})
    out = render_video(
        tmp_path,
        ChunkSpec(0, 0.0, info.duration),
        video,
        None,
        placed,
        options,
        info,
        fps,
        1,
    )
    assert out.exists()
    assert (tmp_path / "part_000.mkv").exists()  # 3 batches -> parts + concat
    video_dur, _ = _stream_durations(out)
    assert abs(video_dur - expected) < 0.5


def test_output_resolution_and_fit(tmp_path, make_talking_video) -> None:
    video = make_talking_video(size="320x240")
    options = JobOptions(
        output={"resolution": "1080x1920", "fit": "pad"}, subtitles={"enabled": False}
    )
    result = run_single_chunk(video, options, tmp_path)
    data = run_ffprobe_json(result.output)
    stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == (1080, 1920)
    assert abs(_fps(result.output) - 30.0) < 0.2


def test_vfr_source_is_normalized_to_cfr_and_stays_in_sync(tmp_path) -> None:
    vfr = tmp_path / "vfr.mp4"
    ffmpeg_cmd(
        [
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=f=220:d=4",
            "-t", "4", "-vf", "select='gt(random(0),0.4)'", "-fps_mode", "vfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(vfr),
        ]
    )
    assert probe(vfr).is_vfr is True

    # Explicit output fps so the CFR result is deterministic (default would round 19.66 -> 20).
    options = JobOptions(
        silence={"enabled": False}, subtitles={"enabled": False}, output={"fps": 30}
    )
    result = run_single_chunk(vfr, options, tmp_path / "w")
    assert abs(_fps(result.output) - 30.0) < 0.2  # VFR forced to CFR

    video_dur, audio_dur = _stream_durations(result.output)
    assert audio_dur is not None
    assert abs(video_dur - audio_dur) < 1 / 30 + 0.05
