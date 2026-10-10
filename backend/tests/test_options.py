"""Options model: valid, boundary and cross-field validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.options import (
    ChunkingMode,
    CoverOptions,
    Encoder,
    JobOptions,
    MusicOptions,
    OutputOptions,
    SilenceOptions,
)


def test_defaults_are_valid_and_complete() -> None:
    opts = JobOptions()
    assert opts.silence.min_silence_sec == 0.5
    assert opts.subtitles.model_size == "small"
    assert opts.music.enabled is False
    assert opts.cover.enabled is True
    assert opts.output.encoder is Encoder.AUTO
    assert opts.performance.chunking is ChunkingMode.AUTO
    # every schema section is present
    dumped = opts.model_dump()
    assert set(dumped) == {
        "denoise",
        "silence",
        "subtitles",
        "music",
        "cover",
        "output",
        "performance",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strength", 101),
        ("strength", -1),
        ("highpass_hz", 301),
    ],
)
def test_denoise_ranges(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        JobOptions(denoise={"enabled": True, field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_silence_sec", 0.05),
        ("min_silence_sec", 11),
        ("threshold_db", -61),
        ("threshold_db", -14),
        ("keep_padding_sec", 1.5),
        ("min_speech_sec", 3),
        ("fade_sec", 0.01),
        ("fade_sec", 0.6),
    ],
)
def test_silence_ranges(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        JobOptions(silence={field: value})


def test_silence_min_greater_than_twice_padding() -> None:
    # 0.3 > 2*0.12 ok; 0.2 <= 2*0.12 fails
    SilenceOptions(min_silence_sec=0.3, keep_padding_sec=0.12)
    with pytest.raises(ValidationError):
        JobOptions(
            silence={"min_silence_sec": 0.2, "keep_padding_sec": 0.12, "min_speech_sec": 0.5}
        )


def test_silence_fade_not_longer_than_kept_audio() -> None:
    # default: 2*0.12 = 0.24 <= 0.25 + 0.24 ok
    JobOptions()
    with pytest.raises(ValidationError):
        JobOptions(
            silence={
                "fade_sec": 0.5,
                "min_speech_sec": 0.2,
                "keep_padding_sec": 0.12,
                "min_silence_sec": 1.0,
            }
        )


def test_silence_boundary_values_valid() -> None:
    JobOptions(
        silence={
            "min_silence_sec": 0.1,
            "keep_padding_sec": 0.0,
            "fade_sec": 0.03,
            "min_speech_sec": 0.25,
        }
    )


def test_validator_message_names_the_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        JobOptions(silence={"min_silence_sec": 0.1, "keep_padding_sec": 0.5})
    assert "min_silence_sec" in str(exc.value)


@pytest.mark.parametrize("value", ["#FFF", "red", "#GGGGGG", "ffffff"])
def test_color_must_be_hex(value: str) -> None:
    with pytest.raises(ValidationError):
        JobOptions(subtitles={"text_color": value})


def test_music_enabled_requires_track() -> None:
    with pytest.raises(ValidationError):
        MusicOptions(enabled=True)
    MusicOptions(enabled=True, track_id="abc")


def test_cover_manual_requires_time() -> None:
    with pytest.raises(ValidationError):
        CoverOptions(time_mode="manual")
    CoverOptions(time_mode="manual", time_sec=3.5)


def test_cover_lines_max_four() -> None:
    with pytest.raises(ValidationError):
        CoverOptions(lines=["a", "b", "c", "d", "e"])


def test_subtitle_ranges() -> None:
    with pytest.raises(ValidationError):
        JobOptions(subtitles={"font_size_pct": 11})
    with pytest.raises(ValidationError):
        JobOptions(subtitles={"words_per_line": 9})
    with pytest.raises(ValidationError):
        JobOptions(subtitles={"vocabulary_hint": "x" * 201})


def test_output_encoder_values() -> None:
    assert OutputOptions(encoder="h264_nvenc").encoder is Encoder.NVENC
    with pytest.raises(ValidationError):
        OutputOptions(encoder="libx265")


def test_extra_keys_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        JobOptions(unknown_section={})
    with pytest.raises(ValidationError):
        JobOptions(silence={"enabled": True, "typo": 1})
