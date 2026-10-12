"""Subtitle data model shared by transcription, repair and the ASS builder."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0


@dataclass
class Caption:
    """A group of words shown together, wrapped into up to `max_lines` lines."""

    lines: list[Line] = field(default_factory=list)
    font_scale: float = 1.0

    @property
    def text(self) -> str:
        return "\\N".join(line.text for line in self.lines if line.text)

    @property
    def start(self) -> float:
        return self.lines[0].start if self.lines else 0.0

    @property
    def end(self) -> float:
        return self.lines[-1].end if self.lines else 0.0
