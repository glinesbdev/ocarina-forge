"""Where Forge projects live.

One project is one mod. The folder is the source (manifest and scene YAML).
It stays outside the game install and outside the dump. The mod folder under
the game's mods/local is export output.
"""

from __future__ import annotations

from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.locate import setting
from ocarina_forge.modexport import mod_id, mod_slug
from ocarina_forge.project import Project


def default_projects_root() -> Path:
    return Path.home() / ".local" / "share" / "ocarina-forge" / "projects"


def default_game_root() -> Path:
    return Path.home() / "Games" / "Longshot" / "mod-test"


def projects_root(config: dict | None = None) -> Path:
    env = setting("PROJECTS")
    if env:
        return Path(env).expanduser()
    stored = (config or {}).get("projects") or ""
    if stored:
        return Path(str(stored)).expanduser()
    return default_projects_root()


def game_root(config: dict | None = None) -> Path:
    env = setting("GAME")
    if env:
        return Path(env).expanduser()
    stored = (config or {}).get("game") or ""
    if stored:
        return Path(str(stored)).expanduser()
    return default_game_root()


def install_dir(config: dict | None, slug: str, game: Path | None = None) -> Path:
    root = game if game is not None else game_root(config)
    return Path(root).expanduser() / "mods" / "local" / slug


def list_projects(config: dict | None = None) -> list[dict]:
    root = projects_root(config)
    found = []
    if root.is_dir():
        for manifest in sorted(root.glob("*/manifest.yaml")):
            try:
                project = Project(manifest.parent)
            except BancError:
                continue
            found.append(_row(project))
    return found


def create_in_library(
    name: str,
    title: str,
    author: str,
    version: str,
    description: str = "",
    config: dict | None = None,
) -> Project:
    slug_root = projects_root(config) / mod_slug(name)
    return Project.create(
        slug_root,
        name=name,
        title=title,
        author=author,
        version=version,
        description=description,
    )


def resolve_project(token: str, config: dict | None = None) -> Path:
    """A folder with manifest.yaml, or the name of one in the projects library."""
    text = (token or "").strip()
    if not text:
        raise BancError("Name a project, or give the folder that holds its manifest.yaml.", "project")
    direct = Path(text).expanduser()
    if (direct / "manifest.yaml").is_file():
        return direct.resolve()
    library = projects_root(config) / text
    if (library / "manifest.yaml").is_file():
        return library.resolve()
    lowered = projects_root(config) / text.lower()
    if (lowered / "manifest.yaml").is_file():
        return lowered.resolve()
    raise BancError(
        f"No project {text!r}. Looked at that folder and in {projects_root(config)}.",
        "project",
    )


def _row(project: Project) -> dict:
    manifest = project.manifest
    return {
        "root": str(project.root),
        "name": manifest["name"],
        "title": manifest.get("title") or manifest["name"],
        "version": manifest.get("version") or "",
        "author": manifest.get("author") or "",
        "description": manifest.get("description") or "",
        "modId": mod_id(manifest["name"]),
        "engine": manifest.get("engine") or "scenes",
    }
