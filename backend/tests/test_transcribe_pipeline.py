"""Fake-Transcriber end-to-end pipeline and burned-subtitle verification (phase 3.8)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.models.options import JobOptions
from app.models.subtitle import Word
from app.pipeline.stages import run_single_chunk
from conftest import ffmpeg_cmd


class FakeTranscriber:
    """Satisfies the Transcriber protocol without any model download."""

    def __init__(self, words: list[Word], language: str = "en", fail: bool = False) -> None:
        self.words = words
        self.language = language
        self.fail = fail

    def detect_language(self, audio_path: Path) -> str:
        return self.language

    def transcribe(self, audio_path: Path, language: str | None, options) -> list[Word]:
        if self.fail:
            raise RuntimeError("simulated ASR crash")
        return self.words


def sample_words() -> list[Word]:
    return [
        Word("Cinelume", 0.15, 0.75, 0.95),
        Word("cuts", 0.80, 1.05, 0.95),
        Word("the", 1.10, 1.25, 0.95),
        Word("silences", 1.30, 1.90, 0.95),
        Word("for", 1.95, 2.10, 0.95),
        Word("you", 2.15, 2.40, 0.95),
    ]


def _clip(make_talking_video, name="talk.mp4"):
    return make_talking_video(name=name, duration=3.0, bursts=[(0.0, 1.0), (1.5, 3.0)])


def _frame(path: Path, t: float, out_png: Path) -> np.ndarray:
    ffmpeg_cmd(["-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", str(out_png)])
    return np.asarray(Image.open(out_png).convert("RGB"), dtype=np.int16)


def _changed_fraction(a: np.ndarray, b: np.ndarray, threshold: int = 32) -> float:
    """Share of pixels that clearly differ - text covers a small part of the frame."""
    return float((np.abs(a - b).max(axis=2) > threshold).mean())


def test_end_to_end_with_fake_transcriber(tmp_path, make_talking_video) -> None:
    video = _clip(make_talking_video)
    workdir = tmp_path / "w"
    result = run_single_chunk(
        video, JobOptions(), workdir, transcriber=FakeTranscriber(sample_words())
    )

    assert result.output.exists()
    subs = workdir / "chunk_000" / "subs.ass"
    assert subs.exists() and "Cinelume" in subs.read_text(encoding="utf-8")
    words_json = workdir / "chunk_000" / "words.json"
    assert words_json.exists()


def test_subtitles_actually_burn_into_the_video(tmp_path, make_talking_video) -> None:
    video = _clip(make_talking_video)

    run_single_chunk(
        video, JobOptions(), tmp_path / "w1", transcriber=FakeTranscriber(sample_words())
    )
    run_single_chunk(
        video, JobOptions(subtitles={"enabled": False}), tmp_path / "w2"
    )
    # find the rendered outputs
    with_out = next((tmp_path / "w1").glob("chunk_out.mkv"))
    without_out = next((tmp_path / "w2").glob("chunk_out.mkv"))

    a = _frame(with_out, 0.4, tmp_path / "a.png")
    b = _frame(without_out, 0.4, tmp_path / "b.png")
    assert _changed_fraction(a, b) > 0.002, "subtitles were not burned in"


def test_one_frame_per_mode_saved_to_artifacts(tmp_path, make_talking_video) -> None:
    video = _clip(make_talking_video, "modes.mp4")
    artifacts = Path(__file__).parent / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    for mode in ("karaoke_fill", "word_highlight", "static"):
        options = JobOptions(subtitles={"mode": mode, "background_mode": "box"})
        result = run_single_chunk(
            video, options, tmp_path / f"w_{mode}", transcriber=FakeTranscriber(sample_words())
        )
        out_png = artifacts / f"mode_{mode}.png"
        ffmpeg_cmd(["-ss", "0.4", "-i", str(result.output), "-frames:v", "1", str(out_png)])
        assert out_png.stat().st_size > 0


def test_rtl_sample_renders(tmp_path, make_talking_video) -> None:
    video = _clip(make_talking_video, "rtl.mp4")
    urdu = [
        Word("سلام", 0.15, 0.60, 0.95),
        Word("دنیا", 0.65, 1.10, 0.95),
        Word("خوش", 1.20, 1.60, 0.95),
    ]
    options = JobOptions(
        subtitles={"font_family": "Noto Naskh Arabic", "mode": "static", "font_size_pct": 10}
    )
    result = run_single_chunk(video, options, tmp_path / "w_rtl", transcriber=FakeTranscriber(urdu))
    plain = run_single_chunk(
        video, JobOptions(subtitles={"enabled": False}), tmp_path / "w_plain"
    )

    with_text = _frame(result.output, 0.4, tmp_path / "rtl.png")
    bare = _frame(plain.output, 0.4, tmp_path / "plain.png")
    assert _changed_fraction(with_text, bare) > 0.002
    artifacts = Path(__file__).parent / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "rtl_urdu.png").write_bytes((tmp_path / "rtl.png").read_bytes())


def test_no_speech_skips_subtitles_and_continues(tmp_path, make_talking_video) -> None:
    video = _clip(make_talking_video, "quiet.mp4")
    result = run_single_chunk(
        video, JobOptions(), tmp_path / "w", transcriber=FakeTranscriber([])
    )
    assert result.output.exists()
    assert any("No speech detected" in w for w in result.warnings)


def test_asr_failure_degrades_only_when_configured(tmp_path, make_talking_video) -> None:
    video = _clip(make_talking_video, "fail.mp4")
    result = run_single_chunk(
        video,
        JobOptions(performance={"degrade_on_asr_failure": True}),
        tmp_path / "w",
        transcriber=FakeTranscriber([], fail=True),
    )
    assert result.output.exists()
    assert any("without subtitles" in w for w in result.warnings)
