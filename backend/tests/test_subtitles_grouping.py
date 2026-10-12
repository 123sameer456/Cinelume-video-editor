"""Line grouping with measured text widths (phase 3.5)."""

from __future__ import annotations

from pathlib import Path

from app.models.options import JobOptions
from app.models.subtitle import Word
from app.services.fonts import font_path
from app.services.subtitles import group_lines

PLAY_RES = (640, 360)
FONT = font_path("Montserrat")


def words_of(texts, start=0.0, step=0.3, prob=0.95):
    out = []
    t = start
    for text in texts:
        out.append(Word(text, t, t + step * 0.8, prob))
        t += step
    return out


def test_grouping_respects_words_per_line_and_max_lines() -> None:
    words = words_of(["one", "two", "three", "four", "five", "six", "seven", "eight"])
    captions = group_lines(words, JobOptions(), PLAY_RES, "Montserrat", FONT)
    assert len(captions) == 1
    assert sum(len(line.words) for line in captions[0].lines) == 8
    assert len(captions[0].lines) <= 2


def test_long_pause_starts_a_new_caption() -> None:
    words = words_of(["alpha", "beta"])
    words[1].start = words[0].end + 0.9  # 0.9s pause > 0.6s
    words[1].end = words[1].start + 0.2
    captions = group_lines(words, JobOptions(), PLAY_RES, "Montserrat", FONT)
    assert len(captions) == 2


def test_sentence_punctuation_starts_a_new_caption() -> None:
    words = words_of(["Stop.", "and", "then", "go", "on", "here", "now", "yes"])
    captions = group_lines(words, JobOptions(), PLAY_RES, "Montserrat", FONT)
    assert len(captions) >= 2


def test_measured_width_wraps_lines() -> None:
    long_words = words_of(["supercalifragilistic", "expialidocious", "and", "short"])
    captions = group_lines(long_words, JobOptions(), PLAY_RES, "Montserrat", FONT)
    assert captions
    assert all(line.text for caption in captions for line in caption.lines)


def test_single_long_word_shrinks_the_font() -> None:
    huge = words_of(["W" * 60])
    captions = group_lines(huge, JobOptions(), PLAY_RES, "Montserrat", FONT)
    assert len(captions) == 1
    assert captions[0].font_scale < 1.0


def test_max_chars_per_line_is_respected() -> None:
    words = words_of(["aaaa", "bbbb", "cccc", "dddd", "eeee", "ffff", "gggg", "hhhh"])
    options = JobOptions(subtitles={"max_chars_per_line": 10, "words_per_line": 2, "max_lines": 3})
    captions = group_lines(words, options, PLAY_RES, "Montserrat", FONT)
    assert captions
    for caption in captions:
        for line in caption.lines:
            assert len(line.text) <= 10 or len(line.words) == 1


def test_uppercase_option_is_applied() -> None:
    words = words_of(["hello", "World"])
    options = JobOptions(subtitles={"uppercase": True})
    captions = group_lines(words, options, PLAY_RES, "Montserrat", FONT)
    assert captions[0].lines[0].text == "HELLO WORLD"


def test_font_file_can_be_missing_for_measurement() -> None:
    words = words_of(["hello", "world"])
    captions = group_lines(words, JobOptions(), PLAY_RES, "Montserrat", Path("missing.ttf"))
    assert len(captions) == 1
