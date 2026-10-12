"""Cover image from a frame of the original video with Pillow text (phase 4.3).

The frame comes from the ORIGINAL video (so no burned subtitles) and is autorotated by
ffmpeg on decode. Random mode samples candidates and keeps the sharpest, non-black one.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from ..core.errors import ErrorCode, PipelineError
from ..core.logging import get_logger
from ..models.options import CoverAspect, CoverFormat, CoverTimeMode, JobOptions, Position
from ..services.ffmpeg import run_ffmpeg
from ..services.fonts import font_path
from ..services.probe import probe

log = get_logger("cinelume.cover")

_WIDTH_LIMIT_FRACTION = 0.85
_CHAR_WIDTH_FALLBACK = 0.55
_LINE_SPACING = 1.25
_BLACK_LUMA = 16.0
_WHITE_LUMA = 240.0
_SAFE_MARGIN_FRACTION = 0.05
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_ARABIC_FONT = "Noto Naskh Arabic"


@dataclass
class CoverResult:
    time_sec: float
    path: Path
    warnings: list[str] = field(default_factory=list)


def _has_arabic(text: str) -> bool:
    return any(
        any(low <= ord(ch) <= high for low, high in _ARABIC_RANGES)
        for ch in text
    )


def _shape(text: str, raqm: bool) -> str:
    """Pillow without Raqm needs arabic-reshaper + python-bidi for Arabic script."""
    if not _has_arabic(text):
        return text
    if raqm:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:  # pragma: no cover - optional dependency
        return text


@lru_cache(maxsize=32)
def _raqm_available() -> bool:
    return bool(features.check("raqm"))


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    try:
        engine = ImageFont.Layout.RAQM if _raqm_available() else ImageFont.Layout.BASIC
        return ImageFont.truetype(path, max(6, size), layout_engine=engine)
    except Exception:
        return None


def _measure(text: str, font, size_px: float) -> float:
    if font is not None:
        try:
            if _raqm_available() and _has_arabic(text):
                return float(font.getlength(text, direction="rtl"))
            return float(font.getlength(text))
        except Exception:
            pass
    return len(text) * size_px * _CHAR_WIDTH_FALLBACK


def _extract_frame(source: Path, time_sec: float, workdir: Path, max_width: int) -> Path:
    out = workdir / f"cover_frame_{int(time.time() * 1000) % 10_000}.png"
    run_ffmpeg(
        [
            "-ss",
            f"{time_sec:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale={max_width}:-2",
            "-y",
            str(out),
        ],
        cwd=workdir,
        timeout=60,
        log_path=workdir / "cover.log",
    )
    if not out.exists():
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED, "Could not extract the cover frame.", stage="cover"
        )
    return out


def _sharpness(image: Image.Image) -> float:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return float(np.asarray(edges, dtype=np.float64).var())


def _mean_luma(image: Image.Image) -> float:
    return float(np.asarray(image.convert("L"), dtype=np.float64).mean())


def _crop_to_aspect(image: Image.Image, aspect: CoverAspect) -> Image.Image:
    if aspect is CoverAspect.SAME_AS_VIDEO:
        return image
    ratio_w, _, ratio_h = aspect.value.partition(":")
    target = float(ratio_w) / float(ratio_h)
    width, height = image.size
    current = width / height
    if abs(current - target) < 1e-3:
        return image
    if current > target:
        new_width = int(round(height * target))
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = int(round(width / target))
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def _choose_time(cover, duration: float, workdir: Path, source: Path) -> tuple[float, float]:
    """Returns (time_sec, score). Random mode keeps the sharpest non-black candidate."""
    if cover.time_mode is CoverTimeMode.MANUAL:
        if cover.time_sec is None:
            raise PipelineError(
                ErrorCode.BAD_REQUEST, "time_sec is required for manual covers.", stage="cover"
            )
        return max(0.0, min(float(cover.time_sec), max(0.0, duration - 0.1))), -1.0

    rng = random.Random(cover.seed)
    low = 1.0 if duration > 2 else duration / 2
    high = max(low + 0.01, duration - 1.0)
    best_time, best_score = duration / 2, -1.0
    for _ in range(cover.candidates):
        candidate = rng.uniform(low, high)
        frame = _extract_frame(source, candidate, workdir, max_width=480)
        try:
            image = Image.open(frame)
            luma = _mean_luma(image)
            if luma < _BLACK_LUMA or luma > _WHITE_LUMA:
                continue
            score = _sharpness(image)
            if score > best_score:
                best_time, best_score = candidate, score
        finally:
            frame.unlink(missing_ok=True)
    return best_time, best_score


def render_cover(
    source: Path, options: JobOptions, out_path: Path, seed: int | None = None
) -> CoverResult:
    info = probe(source)
    if not info.has_video:
        raise PipelineError(
            ErrorCode.NO_VIDEO_STREAM, "No usable video stream found.", stage="cover"
        )
    if info.duration <= 0:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA, "The video has zero duration.", stage="cover"
        )

    workdir = out_path.parent
    workdir.mkdir(parents=True, exist_ok=True)
    cover = options.cover
    if seed is not None:
        cover = cover.model_copy(update={"seed": seed})

    time_sec, _score = _choose_time(cover, info.duration, workdir, source)
    frame = _extract_frame(source, time_sec, workdir, max_width=1280)

    warnings: list[str] = []
    try:
        image = Image.open(frame).convert("RGB")
        image = _crop_to_aspect(image, cover.aspect)
        image = _draw_text(image, cover, warnings)
        out = out_path.with_suffix(f".{cover.format.value}")
        if cover.format is CoverFormat.PNG:
            image.save(out)
        else:
            image.save(out, quality=92)
    finally:
        frame.unlink(missing_ok=True)
    log.info(f"cover: t={time_sec:.2f}s -> {out_path}")
    return CoverResult(time_sec=time_sec, path=out, warnings=warnings)


def _draw_text(image: Image.Image, cover, warnings: list[str]) -> Image.Image:
    lines = [line.strip() for line in cover.lines if line.strip()]
    if not lines:
        return image

    family = cover.font_family
    font_file = font_path(family)
    if _has_arabic("".join(lines)) and font_file is not None and family != _ARABIC_FONT:
        # The chosen family may not carry Arabic glyphs; Noto does.
        noto = font_path(_ARABIC_FONT)
        if noto is not None:
            font_file, family = noto, _ARABIC_FONT
            warnings.append(f"Missing Arabic glyphs in {cover.font_family}; used {_ARABIC_FONT}.")
    if font_file is None:
        warnings.append(f"Font '{family}' not found; using a fallback width estimate.")

    margin = int(image.height * _SAFE_MARGIN_FRACTION)
    max_width = image.width - 2 * margin
    base_size = cover.font_size_pct * image.height / 100.0
    raqm = _raqm_available()

    scale = 1.0
    wrapped: list[str] = lines
    line_height = base_size * _LINE_SPACING
    for _ in range(12):
        font = _font(str(font_file), int(round(base_size * scale))) if font_file else None
        wrapped = _wrap_lines(lines, font, max_width, base_size * scale, family, raqm)
        line_height = base_size * scale * _LINE_SPACING
        if line_height * len(wrapped) <= image.height * 0.8:
            break
        scale *= 0.9

    font = _font(str(font_file), int(round(base_size * scale))) if font_file else None
    size_px = base_size * scale
    stroke_width = max(0, round(cover.stroke_width_pct * image.height / 100))
    total_height = int(line_height * len(wrapped))

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if cover.strip_enabled:
        strip_alpha = int(round(max(0, min(100, cover.strip_opacity)) * 255 / 100))
        strip_color = _rgba(cover.strip_color, strip_alpha)
        pad_y = int(line_height * 0.35)
        top = _text_y(cover.position, image.height, total_height, margin) - pad_y
        box = (0, top, image.width, top + total_height + 2 * pad_y)
        draw.rectangle(box, fill=strip_color)

    y = _text_y(cover.position, image.height, total_height, margin)
    for line in wrapped:
        text = _shape(line, raqm)
        width = _measure(text, font, size_px)
        x = max(margin, (image.width - width) / 2)
        kwargs = {"font": font} if font is not None else {}
        draw.text(
            (x, y),
            text,
            fill=_rgb(cover.text_color),
            stroke_width=stroke_width,
            stroke_fill=_rgb(cover.stroke_color),
            **kwargs,
        )
        y += line_height

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _text_y(position: Position, height: int, text_height: int, margin: int) -> float:
    if position is Position.TOP:
        return margin
    if position is Position.CENTER:
        return (height - text_height) / 2
    return height - margin - text_height


def _wrap_lines(
    lines: list[str], font, max_width: float, size_px: float, family: str, raqm: bool
) -> list[str]:
    out: list[str] = []
    for line in lines:
        words = line.split()
        if not words:
            out.append(line)
            continue
        current: list[str] = []
        for word in words:
            candidate = " ".join([*current, word])
            if current and _measure(candidate, font, size_px) > max_width:
                out.append(" ".join(current))
                current = [word]
            else:
                current = [*current, word]
        if current:
            out.append(" ".join(current))
    return out


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = (hex_color or "#FFFFFF").lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgba(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    return (*_rgb(hex_color), alpha)
