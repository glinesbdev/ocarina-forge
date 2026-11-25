"""Import the placement-set subset of a Longshot local mod into a Forge project."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.modexport import canonical_hash
from ocarina_forge.project import Project, scene_filename

_LOCAL_ID = re.compile(r"^local/([a-z0-9](?:[a-z0-9]|[._-](?![._-]))*[a-z0-9]|[a-z0-9])$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_VECTOR_PATHS = {"Translate": "translate", "Rotate": "rotate", "Scale": "scale"}
_SOURCE_DIR = ".longshot-source"


def local_slug(identifier: str) -> str:
    match = _LOCAL_ID.fullmatch(identifier or "")
    if not match:
        raise BancError("Choose a local mod with an id such as local/static-move.", "mod_import")
    slug = match.group(1)
    if len(slug) > 64:
        raise BancError("The local mod id is longer than Longshot allows.", "mod_import")
    return slug


def _target(value, patch: Path) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise BancError(f"{patch.name}: the patch target is missing or too long.", "mod_import")
    target = value.replace("\\", "/")
    parts = target.split("/")
    if (
        not target.startswith("Banc/")
        or any(part in ("", ".", "..") for part in parts)
        or any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts)
        or not target.endswith((".bcett.byml.zs", ".bcett.byml"))
    ):
        raise BancError(f"{patch.name}: {value!r} is not a safe Banc placement target.", "mod_import")
    relative = scene_filename(target)
    if relative.is_absolute() or ".." in relative.parts:
        raise BancError(f"{patch.name}: the patch target escapes the Forge project.", "mod_import")
    return target


def _read_patches(source: Path, identifier: str) -> list[tuple[str, dict[str, dict]]]:
    patches_dir = source / "patches"
    if not patches_dir.is_dir() or patches_dir.is_symlink():
        raise BancError(f"{identifier} has no patches folder to import.", "mod_import")
    files = sorted(patches_dir.rglob("*.json"))
    if not files:
        raise BancError(f"{identifier} has no placement patch JSON files to import.", "mod_import")
    if len(files) > 128:
        raise BancError(f"{identifier} has more than 128 patch files; it cannot be imported safely.", "mod_import")

    imported: list[tuple[str, dict[str, dict]]] = []
    for patch in files:
        if patch.is_symlink() or not patch.resolve().is_relative_to(source.resolve()):
            raise BancError(f"{patch.name}: symbolic links are not supported in placement patches.", "mod_import")
        if patch.stat().st_size > 2 * 1024 * 1024:
            raise BancError(f"{patch.name}: placement patch exceeds the 2 MiB import limit.", "mod_import")
        try:
            document = json.loads(patch.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BancError(f"{patch.name}: could not read placement patch JSON: {exc}", "mod_import") from exc
        if not isinstance(document, dict):
            raise BancError(f"{patch.name}: a placement patch must be a JSON object.", "mod_import")
        banc = _target(document.get("target"), patch)
        placements = document.get("placements")
        if not isinstance(placements, list) or not placements:
            raise BancError(f"{patch.name}: the patch has no placement operations.", "mod_import")
        if len(placements) > 4096:
            raise BancError(f"{patch.name}: the patch exceeds the 4096-operation import limit.", "mod_import")

        actors: dict[str, dict] = {}
        for index, operation in enumerate(placements, start=1):
            where = f"{patch.name}, operation {index}"
            if not isinstance(operation, dict) or operation.get("op") != "set":
                raise BancError(f"{where}: Forge imports placement set operations only.", "mod_import")
            path = operation.get("path")
            field = _VECTOR_PATHS.get(path)
            if field is None:
                raise BancError(f"{where}: {path!r} is not a supported placement transform.", "mod_import")
            try:
                identifier_hash = canonical_hash(operation.get("hash"))
            except BancError as exc:
                raise BancError(f"{where}: {exc.message}", "mod_import") from exc
            value = operation.get("value")
            if (
                not isinstance(value, list)
                or len(value) != 3
                or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
                       for item in value)
            ):
                raise BancError(f"{where}: {path} must be a vector of three finite numbers.", "mod_import")
            record = actors.setdefault(identifier_hash, {"state": "edited"})
            record[field] = [float(item) for item in value]
        imported.append((banc, actors))
    return imported


def _manifest(source: Path, identifier: str) -> dict:
    path = source / "mod.json"
    if path.is_symlink() or not path.is_file():
        raise BancError(f"{identifier} has no mod.json manifest.", "mod_import")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BancError(f"{identifier} has an unreadable mod.json: {exc}", "mod_import") from exc
    if not isinstance(manifest, dict) or manifest.get("id") != identifier:
        raise BancError(f"{identifier} must have a matching id in mod.json.", "mod_import")
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        raise BancError(f"{identifier} needs a non-empty name in mod.json.", "mod_import")
    version = str(manifest.get("version") or "0.1.0")
    if not _VERSION.fullmatch(version):
        raise BancError(f"{identifier} has a version that is not major.minor.patch.", "mod_import")
    return manifest


def inspect_local_mod(source: Path, identifier: str) -> dict:
    """Return parsed metadata and imported scene operations, or a supported error."""
    slug = local_slug(identifier)
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise BancError(f"Local mod folder for {identifier} was not found.", "mod_import")
    manifest = _manifest(source, identifier)
    for root, dirs, files in os.walk(source, followlinks=False):
        for name in (*dirs, *files):
            if (Path(root) / name).is_symlink():
                raise BancError(f"{identifier} contains a symbolic link; Forge will not copy it.", "mod_import")
    return {
        "slug": slug,
        "manifest": manifest,
        "patches": _read_patches(source, identifier),
    }


def import_local_mod(source: Path, project_root: Path, identifier: str) -> Project:
    """Create a library project, retaining the original mod tree for future exports."""
    project_root = Path(project_root).expanduser().resolve()
    if (project_root / "manifest.yaml").is_file():
        return Project(project_root)
    if project_root.exists():
        raise BancError(f"{project_root} already exists but is not a Forge project.", "mod_import")

    info = inspect_local_mod(source, identifier)
    source = Path(source).resolve()
    manifest = info["manifest"]
    title = str(manifest.get("name") or info["slug"])
    author = str(manifest.get("author") or "")
    version = str(manifest.get("version") or "0.1.0")
    description = str(manifest.get("description") or "")
    project = Project.create(
        project_root,
        name=info["slug"],
        title=title,
        author=author,
        version=version,
        description=description,
    )
    try:
        shutil.copytree(source, project.root / _SOURCE_DIR)
        by_banc: dict[str, dict] = {}
        for banc, actors in info["patches"]:
            diff = by_banc.setdefault(banc, {"format": 1, "banc": banc, "actors": {}})
            for actor_id, record in actors.items():
                current = diff["actors"].setdefault(actor_id, {"state": "edited"})
                current.update(record)
        for diff in by_banc.values():
            project.save_scene_diff(diff)
    except BancError:
        shutil.rmtree(project.root, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(project.root, ignore_errors=True)
        raise BancError(f"Could not copy {identifier} into the Forge project library: {exc}", "mod_import") from exc
    return project
