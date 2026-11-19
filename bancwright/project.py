"""A mod project: a manifest plus one readable diff file per changed resource.

The project is what you commit. The built romfs, under build/ or mods/, is
generated from it and from the dump. See docs/project-format.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from bancwright.actors import format_hash, parse_hash
from bancwright.errors import BancError

FORMAT = 1
DOMAINS = ("scenes", "text", "events", "tables")
_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


class _Dumper(yaml.SafeDumper):
    """Quote hex hashes so a later load cannot turn them into integers."""


def _represent_str(dumper, data):
    style = '"' if data.startswith("0x") or (data[:1].isdigit() and data.count(".") <= 1 and " " not in data) else None
    # Only force quotes for strings that YAML would otherwise read back as numbers.
    if style and not data.startswith("0x"):
        try:
            float(data)
        except ValueError:
            style = None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _represent_str)


def dump_yaml(data) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True)


def load_yaml(text: str):
    return yaml.safe_load(text)


def normalize_ids(actors: dict) -> dict:
    """Actor map keys are canonical 0x-hashes, even if YAML read one as an int."""
    normalized = {}
    for key, value in actors.items():
        normalized[format_hash(parse_hash(key))] = value
    return normalized


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def check_mod_name(name: str) -> str:
    """Longshot ignores mod folders whose names start with '_' or '-'."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise BancError("The mod needs a name.", "project")
    if cleaned[0] in "_-":
        raise BancError(
            f"Longshot will not load a mod folder named {cleaned!r}. "
            "Folders starting with '_' or '-' are disabled. Drop the prefix.",
            "project",
        )
    if any(char not in _NAME_OK for char in cleaned) or cleaned in (".", ".."):
        raise BancError(
            "Use a mod name made of letters, numbers, dots, hyphens and underscores, "
            "and do not start it with '_' or '-'.",
            "project",
        )
    return cleaned


def scene_filename(banc: str) -> Path:
    """Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs -> scenes/KokiriForest/KokiriForest_Child_Static.yaml"""
    relative = banc.replace("\\", "/")
    suffix = ".bcett.byml.zs"
    if relative.endswith(suffix):
        relative = relative[: -len(suffix)]
    if relative.startswith("Banc/"):
        relative = relative[len("Banc/") :]
    return Path("scenes") / f"{relative}.yaml"


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> dict:
        path = self.root / "manifest.yaml"
        if not path.is_file():
            raise BancError(
                f"{self.root} has no manifest.yaml. Create a project, or open a folder that already has one.",
                "project",
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise BancError(f"{path} is not a project manifest.", "project")
        if int(data.get("format", 0)) != FORMAT:
            raise BancError(
                f"{path} uses project format {data.get('format')!r}. Bancwright writes format {FORMAT}.",
                "project",
            )
        data["name"] = check_mod_name(str(data.get("name", "")))
        data.setdefault("title", data["name"])
        data.setdefault("version", "0.1.0")
        data.setdefault("author", "")
        target = data.get("target") or {}
        data["target"] = {
            "game": target.get("game", "oot-switch2"),
            "version": str(target.get("version", "1.1.1")),
            "host": target.get("host", "longshot"),
        }
        return data

    @staticmethod
    def create(root: Path, name: str, title: str, author: str, version: str) -> "Project":
        root = Path(root).expanduser().resolve()
        if (root / "manifest.yaml").is_file():
            raise BancError(f"{root} already has a manifest.yaml. Open it instead of creating a new one.", "project")
        root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": FORMAT,
            "name": check_mod_name(name),
            "title": title.strip() or name,
            "version": version.strip() or "0.1.0",
            "author": author.strip(),
            "target": {"game": "oot-switch2", "version": "1.1.1", "host": "longshot"},
            "domains": list(DOMAINS),
        }
        atomic_write(root / "manifest.yaml", dump_yaml(manifest))
        for domain in DOMAINS:
            (root / domain).mkdir(exist_ok=True)
        return Project(root)

    def save_manifest(self) -> None:
        atomic_write(self.root / "manifest.yaml", dump_yaml(self.manifest))

    def scene_path(self, banc: str) -> Path:
        return self.root / scene_filename(banc)

    def load_scene_diff(self, banc: str) -> dict:
        path = self.scene_path(banc)
        if not path.is_file():
            return {"format": FORMAT, "banc": banc, "actors": {}}
        data = load_yaml(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise BancError(f"{path} is not a scene diff.", "project")
        recorded = str(data.get("banc") or banc)
        if recorded != banc:
            raise BancError(
                f"{path} says it patches {recorded}, but it was opened as {banc}.",
                "project",
            )
        actors = data.get("actors") or {}
        if not isinstance(actors, dict):
            raise BancError(f"{path} should list actors as a mapping of hash to edit.", "project")
        return {"format": FORMAT, "banc": banc, "actors": normalize_ids(actors)}

    def save_scene_diff(self, diff: dict) -> None:
        banc = diff["banc"]
        path = self.scene_path(banc)
        actors = diff.get("actors") or {}
        if not actors:
            if path.is_file():
                path.unlink()
            return
        payload = {"format": FORMAT, "banc": banc, "actors": normalize_ids(actors)}
        atomic_write(path, dump_yaml(payload))

    def modified_scenes(self) -> list[str]:
        folder = self.root / "scenes"
        if not folder.is_dir():
            return []
        found = []
        for file in sorted(folder.rglob("*.yaml")):
            data = load_yaml(file.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data.get("banc"):
                found.append(str(data["banc"]))
        return found

    def domain_files(self, domain: str) -> list[Path]:
        folder = self.root / domain
        if not folder.is_dir():
            return []
        return sorted(path for path in folder.rglob("*.yaml") if path.is_file())

    def summary(self) -> dict:
        return {
            "root": str(self.root),
            "manifest": self.manifest,
            "modified": self.modified_scenes(),
            "domains": {domain: len(self.domain_files(domain)) for domain in DOMAINS},
        }
