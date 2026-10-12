"""Cover: seeded randomness, frame scoring, aspect crop, text wrapping (phase 4.3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.core.errors import ErrorCode, PipelineError
from app.models.options import JobOptions
from app.services.cover import render_cover
from app.services.probe import probe


def _text_lines() -> list[str]:
    return ["Cinelume", "auto editor"]


def test_cover_seeded_randomness_is_reproducible(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video(duration=6.0)
    options = JobOptions(cover={"lines": _text_lines(), "seed": 7})

    first = render_cover(video, options, tmp_path / "a.png")
    second = render_cover(video, options, tmp_path / "b.png")
    assert abs(first.time_sec - second.time_sec) < 1e-6
    assert first.path.read_bytes() == second.path.read_bytes()

    rolled = render_cover(video, options, tmp_path / "c.png", seed=123)
    assert rolled.path.exists()


def test_cover_black_frames_are_rejected(tmp_path: Path) -> None:
    # a mostly-black video: every candidate is near-black, so the middle frame is used
    from conftest import ffmpeg_cmd

    black = tmp_path / "black.mp4"
    ffmpeg_cmd(
        [
            "-f", "lavfi", "-i", "color=c=black:size=320x240:rate=30",
            "-t", "6",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(black),
        ]
    )
    result = render_cover(black, JobOptions(cover={"lines": [], "seed": 3}), tmp_path / "cover.png")
    assert result.path.exists()
    image = Image.open(result.path)
    assert float(np.asarray(image.convert("L")).mean()) < 20  # stays black, no crash


def test_cover_text_wraps_and_auto_shrinks(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video(duration=4.0)
    long_lines = ["This is a very long line of cover text that cannot fit on one line", "second"]
    options = JobOptions(cover={"lines": long_lines, "seed": 5, "font_size_pct": 12})
    result = render_cover(video, options, tmp_path / "wrapped.png")
    image = Image.open(result.path)
    assert image.width > 0 and image.height > 0
    assert result.warnings == []


def test_cover_manual_mode_uses_exact_time(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video(duration=6.0)
    options = JobOptions(cover={"time_mode": "manual", "time_sec": 2.5, "lines": ["mid"]})
    result = render_cover(video, options, tmp_path / "manual.png")
    assert abs(result.time_sec - 2.5) < 1e-6


def test_cover_aspect_crop(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video(duration=4.0)
    options = JobOptions(cover={"aspect": "9:16", "lines": [], "seed": 1})
    result = render_cover(video, options, tmp_path / "portrait.png")
    image = Image.open(result.path)
    assert image.width / image.height < 0.7  # 9:16 = 0.5625


def test_cover_jpg_format(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video(duration=4.0)
    options = JobOptions(cover={"format": "jpg", "lines": ["jpg"], "seed": 2})
    result = render_cover(video, options, tmp_path / "cover.jpg")
    assert result.path.suffix == ".jpg"


def test_cover_text_changes_pixels(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video(duration=4.0)
    options = JobOptions(cover={"lines": ["Cinelume"], "seed": 9, "font_size_pct": 12})
    result = render_cover(video, options, tmp_path / "with.png")
    assert probe(video).duration > 0

    from conftest import ffmpeg_cmd

    ffmpeg_cmd(
        [
            "-ss", "1.0", "-i", str(video), "-frames:v", "1",
            "-vf", "scale=1280:-2", str(tmp_path / "plain.png"),
        ]
    )
    with_text = np.asarray(Image.open(result.path).convert("L"), dtype=np.int16)
    plain = np.asarray(Image.open(tmp_path / "plain.png").convert("L"), dtype=np.int16)
    assert with_text.shape == plain.shape
    changed = float((np.abs(with_text - plain) > 32).mean())
    assert changed > 0.005, "cover text was not drawn"


def test_cover_rejects_audio_only(tmp_path: Path, make_video) -> None:
    audio = make_video(name="song.m4a", audio_only=True, duration=2.0)
    with pytest.raises(PipelineError) as exc:
        render_cover(audio, JobOptions(cover={"lines": ["x"]}), tmp_path / "c.png")
    assert exc.value.code == ErrorCode.NO_VIDEO_STREAM
