"""Paths and environment from the rename.

``OCARINA_FORGE_*`` is the name to set. ``BANCWRIGHT_*`` is still read so an
existing shell keeps pointing at the same dump, project, and cache. The old
cache directory is renamed into place when the new one is not there yet, so a
converted glb is not thrown away.
"""

from __future__ import annotations

import os
from pathlib import Path


def setting(suffix: str, default: str | None = None) -> str | None:
    """The new environment variable, then the previous name, then ``default``."""
    for name in (f"OCARINA_FORGE_{suffix}", f"BANCWRIGHT_{suffix}"):
        value = os.environ.get(name)
        if value:
            return value
    return default


def adopt(new: Path, old: Path) -> Path:
    """Return ``new``. If only ``old`` exists, rename it, or keep reading it."""
    if new.exists():
        return new
    if not old.exists():
        return new
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except OSError:
        return old
    return new


def user_cache_root() -> Path:
    """``~/.cache/ocarina-forge``, after moving ``~/.cache/bancwright`` once."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return adopt(base / "ocarina-forge", base / "bancwright")


def project_state_dir(root: Path) -> Path:
    """``.ocarina-forge`` in a project, moved from ``.bancwright`` when needed."""
    root = Path(root)
    return adopt(root / ".ocarina-forge", root / ".bancwright")
