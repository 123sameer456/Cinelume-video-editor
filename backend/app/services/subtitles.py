"""Word repair, line grouping and ASS generation (phase 3.4-3.6).

Everything here is pure text/geometry work: no ffmpeg, no model - easy to unit test.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

from ..models.options import BackgroundMode, JobOptions, Position, SubtitleMode
from ..models.subtitle import Caption, Line, Word

MIN_WORD_SEC = 0.08
PAUSE_BREAK_SEC = 0.6
MAX_WORD_REPEATS = 3
MAX_PHRASE_REPEATS = 2
PHRASE_LEN = 3
WIDTH_LIMIT_FRACTION = 0.85
_CHAR_WIDTH_FALLBACK = 0.55  # fraction of the font size when no font file is available
_BOILERPLATE = (
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subscribe to my channel",
    "bye bye",
)
_SENTENCE_ENDINGS = ".!?…。！？"


# ------------------------------------------------------------------ word repair
def _is_punctuation(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and all(unicodedata.category(ch).startswith("P") for ch in stripped)


def _ends_sentence(text: str) -> bool:
    return text.strip().endswith(tuple(_SENTENCE_ENDINGS))


def _is_boilerplate(text: str) -> bool:
    lowered = text.strip().lower()
    return any(phrase in lowered for phrase in _BOILERPLATE)


def repair_words(
    words: list[Word],
    audio_end: float,
    envelope=None,
    hop_sec: float = 0.02,
    threshold_db: float | None = None,
) -> list[Word]:
    """Clean raw Whisper output: punctuation, timings, overlaps and hallucinations."""
    merged: list[Word] = []
    for raw in words:
        text = (raw.text or "").strip()
        if not text:
            continue
        if merged and _is_punctuation(text):
            prev = merged[-1]
            merged[-1] = Word(
                prev.text + text, prev.start, max(prev.end, raw.end), prev.prob
            )
            continue
        merged.append(Word(text, float(raw.start), float(raw.end), float(raw.prob)))

    fixed: list[Word] = []
    for word in merged:
        start = max(0.0, min(word.start, audio_end))
        end = max(0.0, min(word.end, audio_end))
        if end - start < MIN_WORD_SEC:
            end = min(audio_end, start + MIN_WORD_SEC)
            start = max(0.0, end - MIN_WORD_SEC)
        if end - start < MIN_WORD_SEC:
            continue
        if fixed and start < fixed[-1].end:
            start = fixed[-1].end
            if end - start < MIN_WORD_SEC:
                continue
        fixed.append(Word(word.text, start, end, word.prob))

    return _drop_hallucinations(fixed, envelope, hop_sec, threshold_db)


def _drop_hallucinations(
    words: list[Word],
    envelope,
    hop_sec: float,
    threshold_db: float | None,
) -> list[Word]:
    result = _drop_repeated_phrases(_drop_repeated_words(words))
    kept: list[Word] = []
    for word in result:
        if word.prob < 0.05 and word.duration <= 0.1:
            continue
        if envelope is not None and threshold_db is not None:
            silent = _region_is_silent(envelope, hop_sec, word.start, word.end, threshold_db)
            if silent and (word.prob < 0.5 or _is_boilerplate(word.text)):
                continue
        kept.append(word)
    return kept


def _drop_repeated_words(words: list[Word]) -> list[Word]:
    out: list[Word] = []
    for word in words:
        key = word.text.strip().lower()
        recent = sum(1 for w in out[-MAX_WORD_REPEATS:] if w.text.strip().lower() == key)
        if recent >= MAX_WORD_REPEATS:
            continue
        out.append(word)
    return out


def _drop_repeated_phrases(words: list[Word]) -> list[Word]:
    if len(words) < PHRASE_LEN * (MAX_PHRASE_REPEATS + 1):
        return words
    keys = [w.text.strip().lower() for w in words]
    out: list[Word] = []
    i = 0
    while i < len(words):
        phrase = " ".join(keys[i : i + PHRASE_LEN])
        count = 0
        j = i
        while j + PHRASE_LEN <= len(keys) and " ".join(keys[j : j + PHRASE_LEN]) == phrase:
            count += 1
            j += PHRASE_LEN
        if phrase and count > MAX_PHRASE_REPEATS:
            out.extend(words[i : i + PHRASE_LEN * MAX_PHRASE_REPEATS])
            i = j
        else:
            out.append(words[i])
            i += 1
    return out


def _region_is_silent(
    envelope, hop_sec: float, start: float, end: float, threshold_db: float
) -> bool:
    import numpy as np

    if envelope is None or len(envelope) == 0:
        return False
    first = max(0, int(start / hop_sec))
    last = min(len(envelope), max(first + 1, int(end / hop_sec) + 1))
    window = np.asarray(envelope[first:last], dtype=float)
    if window.size == 0:
        return False
    return float(np.max(window)) < threshold_db


# ---------------------------------------------------------------- line grouping
def group_lines(
    words: list[Word],
    options: JobOptions,
    play_res: tuple[int, int],
    font_family: str | None = None,
    font_file: Path | None = None,
) -> list[Caption]:
    subs = options.subtitles
    if subs.uppercase:
        words = [Word(w.text.upper(), w.start, w.end, w.prob) for w in words]
    width_limit = WIDTH_LIMIT_FRACTION * play_res[0]
    max_group_words = max(1, subs.words_per_line * subs.max_lines)

    captions: list[Caption] = []
    for group in _split_groups(words, max_group_words, subs.words_per_line):
        remaining = list(group)
        while remaining:
            lines, scale = _wrap_group(
                remaining, subs, play_res, font_family, font_file, width_limit
            )
            if len(lines) <= subs.max_lines:
                captions.append(Caption(lines=lines, font_scale=scale))
                break
            take = sum(len(line.words) for line in lines[: subs.max_lines]) or 1
            part_lines, part_scale = _wrap_group(
                remaining[:take], subs, play_res, font_family, font_file, width_limit
            )
            captions.append(Caption(lines=part_lines, font_scale=part_scale))
            remaining = remaining[take:]
    return captions


def _split_groups(words: list[Word], max_group_words: int, words_per_line: int) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for index, word in enumerate(words):
        current.append(word)
        pause = words[index + 1].start - word.end if index + 1 < len(words) else 0.0
        if (
            len(current) >= max_group_words
            or pause > PAUSE_BREAK_SEC
            or _ends_sentence(word.text)
        ):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _wrap_group(
    words: list[Word],
    subs,
    play_res: tuple[int, int],
    font_family: str | None,
    font_file: Path | None,
    width_limit: float,
) -> tuple[list[Line], float]:
    size_px = subs.font_size_pct * play_res[1] / 100.0
    scale = 1.0
    while scale > 0.35:
        widest = max(
            (_measure(w.text, size_px * scale, font_family, font_file) for w in words),
            default=0.0,
        )
        if widest <= width_limit:
            break
        scale *= 0.9

    lines: list[Line] = []
    current: list[Word] = []
    for word in words:
        candidate = current + [word]
        if current and (
            _measure(_line_text(candidate), size_px * scale, font_family, font_file) > width_limit
            or len(_line_text(candidate)) > subs.max_chars_per_line
        ):
            lines.append(Line(current))
            current = [word]
        else:
            current = candidate
    if current:
        lines.append(Line(current))
    return lines, scale


def _line_text(words: list[Word]) -> str:
    return " ".join(w.text for w in words).strip()


def _measure(text: str, size_px: float, font_family: str | None, font_file: Path | None) -> float:
    if not text:
        return 0.0
    if font_file and Path(font_file).exists():
        width = _font_width(str(font_file), max(8, int(round(size_px))), text)
        if width is not None:
            return width
    return len(text) * size_px * _CHAR_WIDTH_FALLBACK


@lru_cache(maxsize=64)
def _font_width(path: str, size: int, text: str) -> float | None:
    try:
        from PIL import ImageFont

        return float(ImageFont.truetype(path, size).getlength(text))
    except Exception:
        return None


# ------------------------------------------------------------------ ASS output
def _ass_color(hex_color: str, opacity: int) -> str:
    """`#RRGGBB` + opacity (0-100) -> ASS `&HAABBGGRR` where 00 is opaque."""
    value = (hex_color or "#FFFFFF").lstrip("#").upper()
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha = 255 - int(round(max(0, min(100, opacity)) * 255 / 100))
    return f"&H{alpha:02X}{blue}{green}{red}"


def _inline_color(hex_color: str) -> str:
    value = (hex_color or "#FFFFFF").lstrip("#").upper()
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{blue}{green}{red}&"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _timecode(seconds: float) -> str:
    total_cs = int(round(max(0.0, seconds) * 100))
    hours, rest = divmod(total_cs, 360_000)
    minutes, rest = divmod(rest, 6_000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def karaoke_tags(words: list[Word], line_end: float) -> list[int]:
    """Cumulative centiseconds, rounded once, so the tags sum to the line duration."""
    tags: list[int] = []
    cumulative = 0.0
    previous = 0
    for index, word in enumerate(words):
        next_start = words[index + 1].start if index + 1 < len(words) else line_end
        cumulative += max(0.0, next_start - word.start)
        cents = int(round(cumulative * 100))
        tags.append(cents - previous)
        previous = cents
    return tags


def _karaoke_text(line: Line, subs) -> str:
    tags = karaoke_tags(line.words, line.end)
    pieces = [f"{{\\kf{tags[i]}}}{_escape(word.text)}" for i, word in enumerate(line.words)]
    return " ".join(pieces)


def _highlight_wrap(text: str, subs) -> str:
    highlighted = _inline_color(subs.highlight_color)
    default = _inline_color(subs.text_color)
    body = f"{{\\1c{highlighted}}}{_escape(text)}{{\\1c{default}}}"
    if subs.pop_animation:
        body = (
            "{\\t(0,120,\\fscx115\\fscy115)\\t(120,240,\\fscx100\\fscy100)}" + body
        )
    return body


def _highlight_text(caption: Caption, target_line: int, target_word: int, subs) -> str:
    rendered: list[str] = []
    offset = 0
    for line_index, line in enumerate(caption.lines):
        pieces = []
        for word_index, word in enumerate(line.words):
            if line_index == target_line and word_index == target_word:
                pieces.append(_highlight_wrap(word.text, subs))
            else:
                pieces.append(_escape(word.text))
        rendered.append(" ".join(pieces))
        offset += len(line.words)
    return "\\N".join(rendered)


def _event(start: float, end: float, text: str, font_scale: float) -> str:
    prefix = ""
    if font_scale < 0.999:
        percent = int(round(font_scale * 100))
        prefix = f"{{\\fscx{percent}\\fscy{percent}}}"
    return f"Dialogue: 0,{_timecode(start)},{_timecode(end)},Default,,0,0,0,,{prefix}{text}"


def build_ass(
    captions: list[Caption],
    options: JobOptions,
    play_res: tuple[int, int],
    font_family: str | None = None,
    font_file: Path | None = None,
) -> str:
    subs = options.subtitles
    width, height = play_res
    font_px = subs.font_size_pct * height / 100.0
    family = (font_family or subs.font_family).replace(",", "")
    mode = subs.mode

    if mode is SubtitleMode.KARAOKE_FILL:
        primary = _ass_color(subs.highlight_color, 100)
        secondary = _ass_color(subs.text_color, 100)
    else:
        primary = _ass_color(subs.text_color, 100)
        secondary = _ass_color(subs.text_color, 100)

    if subs.background_mode is BackgroundMode.BOX:
        border_style = 3
        outline_px = max(2.0, font_px * 0.12)
        outline_color = _ass_color(subs.background_color, subs.background_opacity)
        back_color = outline_color  # libass and VSFilter disagree on which one fills the box
    elif subs.background_mode is BackgroundMode.OUTLINE:
        border_style = 1
        outline_px = subs.outline_width_pct * height / 100.0
        outline_color = _ass_color(subs.background_color, subs.background_opacity)
        back_color = _ass_color("#000000", 60 if subs.shadow else 100)
    else:
        border_style = 1
        outline_px = 0.0
        outline_color = _ass_color("#000000", 100)
        back_color = _ass_color("#000000", 60 if subs.shadow else 100)

    shadow_px = max(1.0, height * 0.006) if subs.shadow else 0.0
    alignment = {Position.TOP: 8, Position.CENTER: 5, Position.BOTTOM: 2}[subs.position]
    margin_v = subs.margin_v_pct * height / 100.0

    header = (
        "[Script Info]\n"
        "; Generated by Cinelume\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{family},{font_px:.1f},{primary},{secondary},{outline_color},"
        f"{back_color},{-1 if subs.bold else 0},0,0,0,100,100,0,0,{border_style},"
        f"{outline_px:.2f},{shadow_px:.2f},{alignment},20,20,{margin_v:.0f},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events: list[str] = []
    if mode is SubtitleMode.KARAOKE_FILL:
        for caption in captions:
            for line in caption.lines:
                if not line.words:
                    continue
                events.append(
                    _event(line.start, line.end, _karaoke_text(line, subs), caption.font_scale)
                )
    elif mode is SubtitleMode.WORD_HIGHLIGHT:
        for caption in captions:
            for line_index, line in enumerate(caption.lines):
                for word_index in range(len(line.words)):
                    text = _highlight_text(caption, line_index, word_index, subs)
                    events.append(_event(caption.start, caption.end, text, caption.font_scale))
    else:
        for caption in captions:
            text = "\\N".join(_escape(line.text) for line in caption.lines if line.text)
            if text:
                events.append(_event(caption.start, caption.end, text, caption.font_scale))

    return header + "\n".join(events) + "\n"
