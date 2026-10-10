"""CLI: python -m app.cli cut ... (phase 2.7)."""

from __future__ import annotations

import json
from pathlib import Path

from app import cli
from app.services.probe import probe


def test_cli_cut_removes_silence(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video()
    out = tmp_path / "out.mp4"
    assert cli.main(["cut", str(video), "--out", str(out)]) == 0
    assert out.exists()
    assert probe(out).duration < 5.5  # 6s in, silences removed


def test_cli_min_silence_flag_changes_result(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video(bursts=[(0.0, 1.0), (1.4, 2.4), (3.9, 4.9)], duration=4.9)
    short = tmp_path / "short.mp4"
    long = tmp_path / "long.mp4"
    assert cli.main(["cut", str(video), "--out", str(short), "--min-silence", "0.3"]) == 0
    assert cli.main(["cut", str(video), "--out", str(long), "--min-silence", "1.0"]) == 0
    assert probe(short).duration < probe(long).duration


def test_cli_options_file_can_disable_silence(tmp_path: Path, make_talking_video) -> None:
    video = make_talking_video()
    options = tmp_path / "opts.json"
    options.write_text(json.dumps({"silence": {"enabled": False}}), encoding="utf-8")
    out = tmp_path / "out.mp4"
    assert cli.main(["cut", str(video), "--options", str(options), "--out", str(out)]) == 0
    assert probe(out).duration > 5.7


def test_cli_reports_no_speech(tmp_path: Path, make_talking_video) -> None:
    silent = make_talking_video(bursts=[(0.0, 0.0)], duration=2.0)
    assert cli.main(["cut", str(silent), "--out", str(tmp_path / "out.mp4")]) == 1


def test_cli_missing_input_returns_error(tmp_path: Path) -> None:
    assert cli.main(["cut", str(tmp_path / "nope.mp4")]) == 2
