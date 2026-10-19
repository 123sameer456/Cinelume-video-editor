"""Static security checks enforced by the test suite (phase 7.2).

These are deliberately simple greps over the source so they fail loudly if someone
introduces a shell injection path or a raw user-path join.
"""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def app_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in APP.rglob("*.py")
    )


def test_no_shell_true_anywhere() -> None:
    # Docstrings mention "never shell=True"; the check looks at code, so strip them.
    import ast

    code = "\n".join(
        ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
        for path in APP.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Assign, ast.Call, ast.Lambda))
    )
    assert "shell=True" not in code


def test_no_os_system_or_popen() -> None:
    source = app_source()
    assert "os.system(" not in source
    assert "os.popen(" not in source


def test_no_fstring_in_ffmpeg_args() -> None:
    """FFmpeg args must be built from literal lists, never a single shell string."""
    source = app_source()
    assert "-filter_complex " not in source.replace("-filter_complex_script", "")


def test_no_shell_module() -> None:
    source = app_source()
    assert "import shell" not in source
    assert "from shell" not in source
