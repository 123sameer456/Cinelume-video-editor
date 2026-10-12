"""ASS generation: colors, timing, escaping, styles (phase 3.6)."""

from __future__ import annotations

from app.models.options import JobOptions
from app.models.subtitle import Caption, Line, Word
from app.services.subtitles import _ass_color, build_ass, karaoke_tags

PLAY_RES = (640, 360)


def caption_of(texts, start=0.0, step=0.3):
    words = []
    t = start
    for text in texts:
        words.append(Word(text, t, t + step * 0.8, 0.95))
        t += step
    return Caption(lines=[Line(words)])


def style_fields(ass: str) -> dict[str, str]:
    lines = ass.splitlines()
    format_index = next(i for i, line in enumerate(lines) if line.startswith("Format: Name,"))
    names = [n.strip() for n in lines[format_index][len("Format: ") :].split(",")]
    style_line = next(line for line in lines if line.startswith("Style: Default,"))
    values = style_line[len("Style: ") :].split(",")
    return dict(zip(names, values, strict=False))


def events(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_ass_color_alpha_is_inverted() -> None:
    assert _ass_color("#FFFFFF", 100) == "&H00FFFFFF"
    assert _ass_color("#FFD400", 100) == "&H0000D4FF"
    assert _ass_color("#000000", 60) == "&H66000000"
    assert _ass_color("#000000", 0) == "&HFF000000"


def test_playres_and_font_size() -> None:
    ass = build_ass([caption_of(["hello"])], JobOptions(), PLAY_RES)
    assert "PlayResX: 640" in ass
    assert "PlayResY: 360" in ass
    assert "ScaledBorderAndShadow: yes" in ass
    assert "WrapStyle: 2" in ass
    assert style_fields(ass)["Fontsize"] == f"{4.5 * 360 / 100:.1f}"


def test_marginv_and_alignment_follow_the_position() -> None:
    for position, expected in (("bottom", "2"), ("center", "5"), ("top", "8")):
        options = JobOptions(subtitles={"position": position})
        fields = style_fields(build_ass([caption_of(["hi"])], options, PLAY_RES))
        assert fields["Alignment"] == expected
        assert float(fields["MarginV"]) == 15 * 360 / 100


def test_karaoke_mode_swaps_primary_and_secondary() -> None:
    options = JobOptions(subtitles={"mode": "karaoke_fill"})
    fields = style_fields(build_ass([caption_of(["hello", "world"])], options, PLAY_RES))
    assert fields["PrimaryColour"] == _ass_color("#FFD400", 100)
    assert fields["SecondaryColour"] == _ass_color("#FFFFFF", 100)

    options = JobOptions(subtitles={"mode": "word_highlight"})
    fields = style_fields(build_ass([caption_of(["hello", "world"])], options, PLAY_RES))
    assert fields["PrimaryColour"] == _ass_color("#FFFFFF", 100)


def test_karaoke_event_timing_and_tags() -> None:
    caption = caption_of(["alpha", "beta", "gamma"], start=1.0, step=0.4)
    ass = build_ass([caption], JobOptions(subtitles={"mode": "karaoke_fill"}), PLAY_RES)
    dialogues = events(ass)
    assert len(dialogues) == 1
    line = caption.lines[0]
    tags = karaoke_tags(line.words, line.end)
    assert sum(tags) == round((line.end - line.start) * 100)
    body = dialogues[0].split(",,", 1)[1]
    for tag in tags:
        assert f"\\kf{tag}" in body


def test_word_highlight_emits_one_event_per_word() -> None:
    caption = caption_of(["alpha", "beta", "gamma"], start=1.0, step=0.4)
    ass = build_ass([caption], JobOptions(subtitles={"mode": "word_highlight"}), PLAY_RES)
    dialogues = events(ass)
    assert len(dialogues) == 3
    for dialogue in dialogues:
        start, end = dialogue.split(",")[1], dialogue.split(",")[2]
        assert start == "0:00:01.00"
        assert end == "0:00:02.12"  # the caption's own end, not the word's


def test_static_emits_one_event_per_caption() -> None:
    caption = caption_of(["alpha", "beta"], start=2.0, step=0.5)
    ass = build_ass([caption], JobOptions(subtitles={"mode": "static"}), PLAY_RES)
    assert len(events(ass)) == 1
    assert "alpha beta" in events(ass)[0]


def test_pop_animation_tag_present() -> None:
    options = JobOptions(subtitles={"mode": "word_highlight", "pop_animation": True})
    ass = build_ass([caption_of(["alpha", "beta"])], options, PLAY_RES)
    assert "\\t(0,120,\\fscx115\\fscy115)" in ass


def test_background_box_and_outline() -> None:
    box = style_fields(
        build_ass([caption_of(["hi"])], JobOptions(subtitles={"background_mode": "box"}), PLAY_RES)
    )
    assert box["BorderStyle"] == "3"
    assert box["OutlineColour"].startswith("&H66")  # opacity 60 -> alpha 0x66

    outline = style_fields(
        build_ass(
            [caption_of(["hi"])],
            JobOptions(subtitles={"background_mode": "outline"}),
            PLAY_RES,
        )
    )
    assert outline["BorderStyle"] == "1"
    assert float(outline["Outline"]) == 0.25 * 360 / 100

    none = style_fields(
        build_ass([caption_of(["hi"])], JobOptions(subtitles={"background_mode": "none"}), PLAY_RES)
    )
    assert none["BorderStyle"] == "1"
    assert float(none["Outline"]) == 0.0


def test_text_is_escaped() -> None:
    caption = caption_of([r"back\slash", "{brace}"])
    options = JobOptions(subtitles={"mode": "static"})
    ass = build_ass([caption], options, PLAY_RES)
    body = events(ass)[0]
    assert r"back\\slash" in body
    assert r"\{brace\}" in body


def test_rtl_text_is_preserved() -> None:
    caption = caption_of(["سلام", "دنیا"], start=0.0, step=0.4)
    ass = build_ass([caption], JobOptions(subtitles={"mode": "static"}), PLAY_RES)
    assert "سلام دنیا" in events(ass)[0]


def test_bold_and_font_family() -> None:
    options = JobOptions(subtitles={"bold": True, "font_family": "Poppins"})
    fields = style_fields(build_ass([caption_of(["hi"])], options, PLAY_RES))
    assert fields["Bold"] == "-1"
    assert fields["Fontname"] == "Poppins"
