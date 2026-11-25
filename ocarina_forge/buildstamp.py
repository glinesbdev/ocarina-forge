"""Git revision of this checkout, compared with the page's Vite stamp."""

from __future__ import annotations

import subprocess
from pathlib import Path


def read_build(root: Path | None = None) -> dict:
    """`revision` is HEAD. `committedAt` is its unix timestamp. Unknown when git is missing."""
    if root is None:
        root = Path(__file__).resolve().parents[1]
    try:
        revision = _git(root, "rev-parse", "HEAD")
        committed = int(_git(root, "log", "-1", "--format=%ct", "HEAD"))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {"revision": "unknown", "committedAt": 0}
    if not revision:
        return {"revision": "unknown", "committedAt": 0}
    return {"revision": revision, "committedAt": committed}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
