"""Where the dump and the open project live.

Search order: $OCARINA_FORGE_CONFIG (or $BANCWRIGHT_CONFIG), then
./ocarina-forge.local.yaml, then ./bancwright.local.yaml, then
~/.config/ocarina-forge/config.yaml. A config that still lives under
~/.config/bancwright is renamed across on first use. The local file is
gitignored on purpose: it points at a dump, and a dump is not part of this
repository.
"""

from __future__ import annotations

from pathlib import Path

from ocarina_forge.locate import adopt, setting
from ocarina_forge.project import atomic_write, dump_yaml, load_yaml


def config_path() -> Path:
    override = setting("CONFIG")
    if override:
        return Path(override).expanduser()
    local = Path.cwd() / "ocarina-forge.local.yaml"
    legacy_local = Path.cwd() / "bancwright.local.yaml"
    if local.is_file():
        chosen_local = local
    elif legacy_local.is_file():
        chosen_local = legacy_local
    else:
        chosen_local = None
    home = adopt(
        Path.home() / ".config" / "ocarina-forge" / "config.yaml",
        Path.home() / ".config" / "bancwright" / "config.yaml",
    )
    if chosen_local is not None or not home.is_file():
        return chosen_local or local
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
    dump = setting("DUMP")
    if dump:
        data["dump"] = dump
    project = setting("PROJECT")
    if project:
        data["project"] = project
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
