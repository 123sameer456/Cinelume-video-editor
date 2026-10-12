"""Word repair (phase 3.4)."""

from __future__ import annotations

import numpy as np

from app.models.subtitle import Word
from app.services.subtitles import (
    karaoke_tags,
    repair_words,
)


def test_merges_standalone_punctuation() -> None:
    words = [Word("Hello", 0.0, 0.3, 0.9), Word(",", 0.3, 0.35, 0.9), Word("world", 0.4, 0.7, 0.9)]
    out = repair_words(words, 1.0)
    assert [w.text for w in out] == ["Hello,", "world"]
    assert out[0].start == 0.0 and abs(out[0].end - 0.35) < 1e-9


def test_strips_whitespace_and_skips_empty() -> None:
    words = [Word("  hi  ", 0.0, 0.2, 0.9), Word("   ", 0.3, 0.4, 0.9)]
    assert [w.text for w in repair_words(words, 1.0)] == ["hi"]


def test_zero_duration_becomes_80ms() -> None:
    out = repair_words([Word("pop", 0.5, 0.5, 0.9)], 1.0)
    assert len(out) == 1
    assert 0.079 <= out[0].duration <= 0.081


def test_word_past_audio_end_is_clamped() -> None:
    out = repair_words([Word("late", 0.9, 1.4, 0.9)], 1.0)
    assert out[0].end <= 1.0


def test_overlapping_words_are_shifted() -> None:
    words = [Word("one", 0.0, 0.4, 0.9), Word("two", 0.2, 0.8, 0.9)]
    out = repair_words(words, 1.0)
    assert out[1].start >= out[0].end - 1e-9


def test_repeated_word_loop_is_trimmed() -> None:
    words = [Word("the", 0.1 * i, 0.1 * i + 0.08, 0.9) for i in range(8)]
    out = repair_words(words, 1.0)
    assert len(out) == 3
    assert all(w.text == "the" for w in out)


def test_repeated_phrase_loop_is_trimmed() -> None:
    words = []
    t = 0.0
    for _ in range(5):
        for token in ("hello", "world", "again"):
            words.append(Word(token, t, t + 0.1, 0.9))
            t += 0.12
    out = repair_words(words, t + 1)
    assert len(out) <= 3 * 2  # at most two occurrences of the 3-word phrase


def test_low_probability_short_word_is_dropped() -> None:
    out = repair_words([Word("noise", 0.0, 0.05, 0.02), Word("real", 0.2, 0.6, 0.9)], 1.0)
    assert [w.text for w in out] == ["real"]


def test_boilerplate_over_silence_is_dropped() -> None:
    # 3s envelope: quiet everywhere except 0.2-0.8s of speech
    envelope = np.full(150, -60.0)
    envelope[10:40] = -8.0
    words = [
        Word("real", 0.3, 0.5, 0.9),
        Word("thanks for watching", 1.5, 2.0, 0.95),
    ]
    out = repair_words(words, 3.0, envelope=envelope, hop_sec=0.02, threshold_db=-35)
    assert [w.text for w in out] == ["real"]


def test_no_words_returns_empty() -> None:
    assert repair_words([], 1.0) == []


def test_karaoke_tags_sum_to_line_duration() -> None:
    words = [Word("a", 0.0, 0.3, 0.9), Word("b", 0.5, 0.8, 0.9)]
    tags = karaoke_tags(words, line_end=0.8)
    assert sum(tags) == round(0.8 * 100)
    assert tags[0] == 50 and tags[1] == 30


def test_karaoke_tags_include_the_gap_and_final_word_end() -> None:
    words = [Word("a", 1.0, 1.2, 0.9), Word("b", 1.6, 2.1, 0.9)]
    tags = karaoke_tags(words, line_end=2.1)
    assert sum(tags) == round((2.1 - 1.0) * 100)
