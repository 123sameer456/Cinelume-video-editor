"""Font discovery for subtitles and cover text (phase 3 fills assets/fonts)."""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

_FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}


def list_fonts() -> list[dict[str, str]]:
    """Family names are read from the font files - ASS uses the family, not the filename."""
    if not FONTS_DIR.exists():
        return []
    fonts: list[dict[str, str]] = []
    for path in sorted(FONTS_DIR.rglob("*")):
        if path.suffix.lower() not in _FONT_SUFFIXES or not path.is_file():
            continue
        try:
            family = ImageFont.truetype(str(path), 12).getname()[0]
        except Exception:
            family = path.stem
        fonts.append({"family": family, "file": path.name, "path": str(path)})
    fonts.sort(key=lambda f: f["family"].lower())
    return fonts


def font_path(family: str) -> Path | None:
    for font in list_fonts():
        if font["family"].lower() == family.lower():
            return Path(font["path"])
    return None


def font_files_for_family(family: str) -> list[Path]:
    """All files belonging to a family (regular/bold/italic), for `ass fontsdir`."""
    if not family:
        return []
    wanted = family.lower()
    files = [Path(f["path"]) for f in list_fonts() if f["family"].lower() == wanted]
    if not files:
        # Bold/italic variants can report a different family name; match by stem.
        files = [p for p in FONTS_DIR.rglob("*.ttf") if p.stem.lower().startswith(wanted)]
    return files
