"""Copy an After the Ocarina checkout into a Bancwright project.

Only Bradyn's sources are copied: YAML and mod.toml. Game files, models and
build output are refused.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

from bancwright.errors import BancError
from bancwright.project import check_mod_name

_FOLDERS = ("text", "events", "scenes", "rsdb", "gamedata", "ui")
_ALLOWED = {".yaml", ".yml", ".toml", ".md"}


def _commit(ato: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ato), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _copy_tree(src: Path, dest: Path) -> list[str]:
    written: list[str] = []
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _ALLOWED:
            raise BancError(
                f"Refusing {path.relative_to(src)}. The project only stores YAML and text, never game files.",
                "project",
            )
        relative = path.relative_to(src)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        written.append(relative.as_posix())
    return written


def import_ato_project(ato_root: Path, dest: Path, *, commit: str = "") -> dict:
    """Write ``dest`` as a format 2 project from an After the Ocarina tree."""
    ato_root = Path(ato_root).expanduser().resolve()
    src = ato_root / "src" if (ato_root / "src" / "mod.toml").is_file() else ato_root
    if not (src / "mod.toml").is_file():
        raise BancError(
            f"{ato_root} has no src/mod.toml. Point this at an After the Ocarina checkout "
            "(the folder that contains src/ and after_ocarina/).",
            "project",
        )
    meta = _mod_meta(src / "mod.toml")
    dest = Path(dest).expanduser().resolve()
    if dest == ato_root or dest == src:
        raise BancError("Refusing to import a project on top of the checkout it reads.", "project")
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for folder in _FOLDERS:
        origin = src / folder
        if not origin.is_dir():
            continue
        copied.extend(f"{folder}/{name}" for name in _copy_tree(origin, dest / folder))
    shutil.copy2(src / "mod.toml", dest / "mod.toml")
    copied.append("mod.toml")
    head = commit or _commit(ato_root)
    manifest = {
        "format": 2,
        "name": check_mod_name(meta["id"]),
        "title": meta["name"],
        "version": "0.1.0",
        "author": "Bradyn",
        "engine": "after-ocarina",
        "target": {"game": "oot-switch2", "version": "1.1.1", "host": "longshot"},
        "domains": ["scenes", "text", "events", "rsdb", "gamedata", "ui"],
        "source": {
            "repo": "glinesbdev/after-the-ocarina",
            "commit": head,
            "layout": "The project root is the pipeline src directory.",
        },
    }
    (dest / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {"root": str(dest), "files": copied, "manifest": manifest}


def _mod_meta(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    mod_id = ""
    name = ""
    for line in text.splitlines():
        if line.startswith("id ") or line.startswith("id="):
            mod_id = line.split("=", 1)[-1].strip().strip('"').strip("'")
        elif line.startswith("name ") or line.startswith("name="):
            name = line.split("=", 1)[-1].strip().strip('"').strip("'")
    if not mod_id or not name:
        raise BancError(f"{path} needs id and name.", "project")
    return {"id": mod_id, "name": name}
