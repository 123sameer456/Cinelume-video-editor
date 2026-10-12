"""Cinelume development CLI.

    python -m app.cli cut input.mp4 --options options.json --out out.mp4
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from .core.errors import PipelineError
from .core.logging import setup_logging
from .models.options import JobOptions
from .pipeline.stages import run_single_chunk
from .services.ffmpeg import run_ffmpeg
from .services.probe import probe


def _load_options(path: str | None) -> JobOptions:
    if not path:
        return JobOptions()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return JobOptions(**data)


def _export(final: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() in (".mp4", ".mov"):
        # final.mp4 is already H.264 + AAC with faststart: no re-encode needed.
        shutil.copyfile(final, out_path)
    else:
        run_ffmpeg(
            ["-i", str(final), "-c", "copy", "-y", str(out_path)],
            cwd=out_path.parent,
            timeout=600,
            log_path=out_path.parent / "export.log",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cinelume", description="Local-first video editor")
    sub = parser.add_subparsers(dest="command", required=True)

    cut = sub.add_parser("cut", help="Denoise, cut silences and add fades.")
    cut.add_argument("input", help="Input video file.")
    cut.add_argument("--options", help="JSON file with JobOptions (partial or full).")
    cut.add_argument("--out", help="Output path (default: <input>_cut.mp4).")
    cut.add_argument("--workdir", help="Use this directory for intermediates (kept).")
    cut.add_argument("--min-silence", type=float, dest="min_silence")
    cut.add_argument("--threshold", type=float)
    cut.add_argument("--auto-threshold", action="store_true", dest="auto_threshold")
    cut.add_argument("--no-denoise", action="store_true", dest="no_denoise")
    cut.add_argument(
        "--no-subs",
        action="store_true",
        dest="no_subs",
        help="Skip subtitles (no Whisper model needed).",
    )
    cut.add_argument("--keep-intermediates", action="store_true", dest="keep_intermediates")
    cut.set_defaults(func=cmd_cut)
    return parser


def cmd_cut(args: argparse.Namespace) -> int:
    options = _load_options(args.options)
    if args.auto_threshold:
        options.silence.auto_threshold = True
    if args.min_silence is not None:
        options.silence.min_silence_sec = args.min_silence
    if args.threshold is not None:
        options.silence.threshold_db = args.threshold
    if args.no_denoise:
        options.denoise.enabled = False
    if args.no_subs:
        options.subtitles.enabled = False

    source = Path(args.input)
    if not source.exists():
        print(f"Input not found: {source}", file=sys.stderr)
        return 2

    own_workdir = args.workdir is None
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="cinelume_cut_"))
    out_path = Path(args.out) if args.out else source.with_name(f"{source.stem}_cut.mp4")

    info = probe(source)
    last = [-1.0]

    def progress(pct: float) -> None:
        if pct - last[0] >= 1.0 or pct >= 100.0:
            last[0] = pct
            print(f"\r  encoding {pct:5.1f}%", end="", flush=True)

    try:
        result = run_single_chunk(source, options, workdir, info=info, on_progress=progress)
        print()
        _export(result.output, out_path)
        cover_out = None
        if result.cover is not None:
            cover_out = out_path.with_name(f"{out_path.stem}_cover{result.cover.suffix}")
            shutil.copyfile(result.cover, cover_out)
    finally:
        if own_workdir and not args.keep_intermediates:
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"Input duration : {info.duration:.2f}s ({info.display_width}x{info.display_height})")
    print(f"Output duration: {result.output_duration:.2f}s")
    print(f"Removed        : {result.removed_sec:.2f}s across {len(result.segments)} segment(s)")
    print(f"Threshold      : {result.threshold_db:.1f} dB")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    print(f"Output         : {out_path}")
    if cover_out is not None:
        print(f"Cover          : {cover_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PipelineError as exc:
        print(f"\nERROR [{exc.code.value}] {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  hint: {exc.hint}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
