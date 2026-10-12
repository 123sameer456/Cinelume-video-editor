"""Standalone model-download worker.

Run as a child process so the caller can cancel a download by killing the process tree;
`huggingface_hub` resumes partial files on the next attempt.
"""

from __future__ import annotations

import sys

from huggingface_hub import snapshot_download


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: _asr_download.py <repo_id> <target_dir>", file=sys.stderr)
        return 2
    snapshot_download(repo_id=argv[1], local_dir=argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
