"""Where the dump and the open project live.

Search order: $BANCWRIGHT_CONFIG, then ./bancwright.local.yaml, then
~/.config/bancwright/config.yaml. The local file is gitignored on purpose:
it points at a dump, and a dump is not part of this repository.
"""

from __future__ import annotations

import os
from pathlib import Path

from bancwright.project import atomic_write, dump_yaml, load_yaml


def config_path() -> Path:
    override = os.environ.get("BANCWRIGHT_CONFIG")
    if override:
        return Path(override).expanduser()
    local = Path.cwd() / "bancwright.local.yaml"
    home = Path.home() / ".config" / "bancwright" / "config.yaml"
    if local.is_file() or not home.is_file():
        return local
    return home


def hot_reload_settings(data: dict | None) -> dict:
    raw = data.get("hotReload") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    return {"enabled": bool(raw.get("enabled")), "path": str(raw.get("path") or "")}


def load_config() -> dict:
    path = config_path()
    data: dict = {}
    if path.is_file():
        loaded = load_yaml(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    if os.environ.get("BANCWRIGHT_DUMP"):
        data["dump"] = os.environ["BANCWRIGHT_DUMP"]
    if os.environ.get("BANCWRIGHT_PROJECT"):
        data["project"] = os.environ["BANCWRIGHT_PROJECT"]
    return {
        "dump": data.get("dump") or "",
        "project": data.get("project") or "",
        "hotReload": hot_reload_settings(data),
        "path": str(path),
    }


def save_config(dump: str | None = None, project: str | None = None, hot_reload: dict | None = None) -> dict:
    current = load_config()
    if dump is not None:
        current["dump"] = dump
    if project is not None:
        current["project"] = project
    if hot_reload is not None:
        current["hotReload"] = hot_reload_settings({"hotReload": hot_reload})
    path = Path(current["path"])
    payload = {
        "dump": current["dump"],
        "project": current["project"],
        "hotReload": current["hotReload"],
    }
    atomic_write(path, dump_yaml(payload))
    current.update(payload)
    return current
