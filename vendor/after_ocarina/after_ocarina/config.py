"""Load config.toml and src/mod.toml."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from after_ocarina.errors import ModError
from after_ocarina.paths import MOD_TOML

_MOD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ModMeta:
    id: str
    name: str


@dataclass(frozen=True)
class UserConfig:
    romfs: Path
    longshot_mods: Path
    mod_id: str
    mod_name: str
    # Clean RomFS used instead of ``romfs`` when the dump's packs were overwritten.
    pristine_romfs: Path | None = None


def load_mod_meta(path: Path | None = None) -> ModMeta:
    path = path or MOD_TOML
    if not path.is_file():
        raise ModError(f"Missing mod metadata: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    mod_id = _require_mod_id(data.get("id"), path)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ModError(f"{path}: name must be a non-empty string")
    return ModMeta(id=mod_id, name=name.strip())


def load_user_config(path: Path, mod_toml: Path | None = None) -> UserConfig:
    if not path.is_file():
        raise ModError(
            f"Missing {path.name}. Copy config.example.toml to config.toml and set romfs and longshot_mods."
        )
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    meta = load_mod_meta(mod_toml)
    romfs = _require_path(data.get("romfs"), "romfs", path)
    mods = _require_path(data.get("longshot_mods"), "longshot_mods", path)
    mod_id = meta.id
    if "mod_id" in data and data["mod_id"] not in (None, ""):
        mod_id = _require_mod_id(data.get("mod_id"), path)
    pristine = None
    if "pristine_romfs" in data and data["pristine_romfs"] not in (None, ""):
        pristine = _require_path(data.get("pristine_romfs"), "pristine_romfs", path)
    return UserConfig(
        romfs=romfs,
        longshot_mods=mods,
        mod_id=mod_id,
        mod_name=meta.name,
        pristine_romfs=pristine,
    )


def _require_path(value: object, key: str, source: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ModError(f"{source}: set {key} to a directory on this PC")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = (source.parent / path).resolve()
    else:
        path = path.resolve()
    return path


def _require_mod_id(value: object, source: Path) -> str:
    if not isinstance(value, str) or _MOD_ID.fullmatch(value) is None:
        raise ModError(
            f"{source}: mod id must start with a letter or digit and contain only "
            "letters, digits, dots, underscores and hyphens. "
            "Longshot skips a folder whose name starts with '_' or '-'."
        )
    return value
