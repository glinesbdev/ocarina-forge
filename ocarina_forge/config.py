"""Where the dump and the open project live.

Search order: $OCARINA_FORGE_CONFIG (or $BANCWRIGHT_CONFIG), then
./ocarina-forge.local.yaml, then ./bancwright.local.yaml, then
~/.config/ocarina-forge/config.yaml. A config that still lives under
~/.config/bancwright is renamed across on first use. The local file is
gitignored on purpose: it points at a dump, and a dump is not part of this
repository.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ocarina_forge.locate import adopt, setting
from ocarina_forge.project import atomic_write, dump_yaml, load_yaml

_log = logging.getLogger("ocarina_forge.config")
_CACHE_LIMIT = re.compile(r"^(\d+)\s*([kmgt]i?b?)?$", re.IGNORECASE)
_UNSET = object()
_LIMIT_SCALES = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
_last_bad_limit = object()


def parse_cache_limit(value) -> int | None:
    """Bytes, or None when ``value`` is not a limit.

    A bare number is a byte count. ``20G``, ``20GB``, and ``20GiB`` are binary
    sizes (1024). ``0`` means unlimited.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or not value.is_integer():
            return None
        return int(value)
    text = str(value).strip().replace("_", "")
    match = _CACHE_LIMIT.fullmatch(text)
    if not match:
        return None
    prefix = (match.group(2) or "").lower()[:1]
    scale = _LIMIT_SCALES.get(prefix)
    if scale is None:
        return None
    return int(match.group(1)) * scale


def _take_cache_limit(data: dict) -> int | None:
    """The saved cap, or None when the file leaves it at the default."""
    global _last_bad_limit
    if "cacheLimit" not in data:
        return None
    raw = data.get("cacheLimit")
    parsed = parse_cache_limit(raw)
    if parsed is None and raw not in (None, ""):
        if raw != _last_bad_limit:
            _log.warning("Ignoring cacheLimit value %r", raw)
            _last_bad_limit = raw
        return None
    return parsed


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
    elif not data.get("dump"):
        game = setting("GAME")
        if game:
            root = Path(game).expanduser()
            candidates = (root, root.parent) if root.name.lower() == "bin" else (root,)
            data["dump"] = next((str(candidate) for candidate in candidates
                                 if (candidate / "romfs" / "Banc").is_dir()), "")
    project = setting("PROJECT")
    if project:
        data["project"] = project
    workers = data.get("workers")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        workers = None
    return {
        "dump": data.get("dump") or "",
        "project": data.get("project") or "",
        "projects": data.get("projects") or "",
        "game": data.get("game") or "",
        "hotReload": hot_reload_settings(data),
        "workers": workers,
        "cacheLimit": _take_cache_limit(data),
        "path": str(path),
    }


def save_config(
    dump: str | None = None,
    project: str | None = None,
    hot_reload: dict | None = None,
    workers: int | None = None,
    projects: str | None = None,
    game: str | None = None,
    cache_limit=_UNSET,
) -> dict:
    current = load_config()
    if dump is not None:
        current["dump"] = dump
    if project is not None:
        current["project"] = project
    if hot_reload is not None:
        current["hotReload"] = hot_reload_settings({"hotReload": hot_reload})
    if workers is not None:
        current["workers"] = max(1, int(workers))
    if projects is not None:
        current["projects"] = projects
    if game is not None:
        current["game"] = game
    if cache_limit is not _UNSET:
        if cache_limit is None:
            current["cacheLimit"] = None
        else:
            parsed = parse_cache_limit(cache_limit)
            if parsed is None:
                raise ValueError("Cache limit must be a byte count, 0, or a size such as 20G.")
            current["cacheLimit"] = parsed
    path = Path(current["path"])
    payload = {
        "dump": current["dump"],
        "project": current["project"],
        "hotReload": current["hotReload"],
    }
    if current.get("projects"):
        payload["projects"] = current["projects"]
    if current.get("game"):
        payload["game"] = current["game"]
    if current.get("workers"):
        payload["workers"] = current["workers"]
    if isinstance(current.get("cacheLimit"), int) and not isinstance(current.get("cacheLimit"), bool):
        payload["cacheLimit"] = current["cacheLimit"]
    atomic_write(path, dump_yaml(payload))
    current.update(payload)
    return current
