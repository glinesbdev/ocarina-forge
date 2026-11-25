"""Write a Longshot mod folder from a Forge project.

The project YAML stays the source. New patch-only projects emit minimal game
files; imported projects also retain their original runtime tree and manifest
fields. A value equal to the base is dropped. Editor state stays in the project.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ocarina_forge.actors import format_hash, parse_hash, read_vec
from ocarina_forge.errors import BancError
from ocarina_forge.project import check_mod_name
from ocarina_forge.scene import _base_map, _close

# editor-mod docs/modding/manifest.md: each half of an id.
_HALF = re.compile(r"^[a-z0-9](?:[a-z0-9]|[._-](?![._-]))*[a-z0-9]$|^[a-z0-9]$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def canonical_hash(value) -> str:
    """One internal form: ``0x`` and 16 lower-case hex digits.

    ``content.change`` writes the same id with no ``0x`` prefix. A placements
    patch writes this form. A 16-digit hex string, with or without ``0x``, is
    that id. A longer digit string is the decimal form of the same integer.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        body = text[2:] if text.startswith("0x") else text
        if body and len(body) <= 16 and all(char in "0123456789abcdef" for char in body):
            if text.startswith("0x") or any(char in "abcdef" for char in body) or len(body) == 16:
                return format_hash(int(body, 16))
    return format_hash(parse_hash(value))


def content_placement_id(value) -> str:
    """The placement id ``content.change`` documents, without a ``0x`` prefix."""
    return canonical_hash(value)[2:]


def mod_slug(name: str) -> str:
    slug = check_mod_name(name).lower()
    if not _HALF.fullmatch(slug) or len(slug) > 64:
        raise BancError(
            f"{name!r} cannot be a mod id. Use 1–64 lower-case letters, digits, dots, "
            "underscores and hyphens. Start and end with a letter or a digit, and do not "
            "put two of . _ - in a row.",
            "project",
        )
    return slug


def mod_id(name: str) -> str:
    return f"local/{mod_slug(name)}"


def _nums(values: list[float]) -> list[float]:
    return [round(float(item), 6) for item in values]


def _patch_name(banc: str) -> str:
    leaf = banc.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if leaf.endswith(suffix):
            return leaf[: -len(suffix)] + ".json"
    return leaf + ".json"


def placement_ops(document: dict, diff: dict, imported_ids: set[str] | None = None) -> tuple[list[dict], list[str]]:
    """Ops that still differ from the dump, plus warnings for edits with no op yet."""
    warnings: list[str] = []
    ops: list[dict] = []
    base = _base_map(document)
    records = diff.get("actors") or {}
    for ident, record in records.items():
        if not isinstance(record, dict):
            continue
        try:
            hashed = canonical_hash(ident)
        except BancError as exc:
            warnings.append(exc.message)
            continue
        state = record.get("state")
        actor = base.get(hashed)
        if state in ("hidden", "removed"):
            label = record.get("gyaml") or (actor or {}).get("Gyaml") or hashed
            warnings.append(
                f"{label} ({hashed}) is {state} in the project. "
                "It stays visible in Forge and is not written. "
                "The placements patch format has a remove op; this export does not emit it yet."
            )
            continue
        if actor is None and hashed in (imported_ids or set()) and state != "added" and "actor" not in record:
            for field, path in (("translate", "Translate"), ("rotate", "Rotate"), ("scale", "Scale")):
                if record.get(field) is not None:
                    ops.append({"op": "set", "hash": hashed, "path": path, "value": _nums(record[field])})
            continue
        if actor is None or state == "added" or "actor" in record:
            label = record.get("actor") or record.get("gyaml") or hashed
            warnings.append(
                f"{label} ({hashed}) is a new actor. Adding a placement is a later milestone, so it was not written."
            )
            continue
        label = actor.get("Gyaml") or hashed
        if record.get("gyaml") and record["gyaml"] != actor.get("Gyaml"):
            warnings.append(f"{label} ({hashed}): a Gyaml change is not a placements set in this export.")
        if "name" in record:
            warnings.append(f"{label} ({hashed}): the instance name is not written in this export.")
        if record.get("set"):
            warnings.append(
                f"{label} ({hashed}): parameter edits stay in the project. "
                "content.change is a later milestone."
            )
        _pose(ops, hashed, label, actor, record, "translate", "Translate", [0.0, 0.0, 0.0], warnings)
        _pose(ops, hashed, label, actor, record, "rotate", "Rotate", [0.0, 0.0, 0.0], warnings)
        _pose(ops, hashed, label, actor, record, "scale", "Scale", [1.0, 1.0, 1.0], warnings)
    return ops, warnings


def _pose(ops, hashed, label, actor, record, field, path, default, warnings) -> None:
    if record.get(field) is None:
        return
    new = _nums(record[field])
    old = _nums(read_vec(actor, path, default))
    if _close(new, old):
        return
    present = path in actor
    if path == "Scale" and not present:
        op = "add"
    else:
        op = "set"
    ops.append({"op": op, "hash": hashed, "path": path, "value": new})
    if op == "add":
        warnings.append(
            f"{label} ({hashed}): Scale is new on this actor, so the patch grows the file and waits for the next game start."
        )


def _manifest_json(project, original: dict | None = None) -> dict:
    manifest = project.manifest
    version = str(manifest.get("version") or "0.1.0")
    if not _VERSION.fullmatch(version):
        raise BancError(
            f"Mod version {version!r} is not major.minor.patch. Longshot refuses any other shape.",
            "project",
        )
    body = dict(original or {})
    body.update({
        "id": mod_id(manifest["name"]),
        "name": str(manifest.get("title") or manifest["name"])[:128],
        "version": version,
    })
    author = str(manifest.get("author") or "").strip()
    if author:
        body["author"] = author[:128]
    else:
        body.pop("author", None)
    license_name = str(manifest.get("license") or "").strip()
    if license_name:
        body["license"] = license_name[:128]
    elif original is None:
        body.pop("license", None)
    return body


def _preserve_imported_patches(project, out: Path, source_info: dict, written: list[str],
                               generated: set[tuple[str, str, str]], log: list[dict],
                               warnings: list[str]) -> None:
    fields = {"translate": "Translate", "rotate": "Rotate", "scale": "Scale"}
    fallback: dict[str, list[dict]] = {}
    for banc, actors in source_info["patches"]:
        diff = project.load_scene_diff(banc)["actors"]
        for ident in actors:
            record = diff.get(ident)
            if not isinstance(record, dict):
                continue
            for field, path in fields.items():
                if field not in record or (banc, ident, path) in generated:
                    continue
                fallback.setdefault(banc, []).append({
                    "op": "set",
                    "hash": ident,
                    "path": path,
                    "value": _nums(record[field]),
                })
                if record.get("state") in ("hidden", "removed"):
                    note = (
                        f"{banc}: {ident} is {record['state']}; its imported {path} patch stays because "
                        "placement removal is not supported."
                    )
                    warnings.append(note)
                    log.append({"level": "warn", "message": note})

    for banc, ops in fallback.items():
        destination = out / "patches" / _patch_name(banc)
        if destination.is_file():
            payload = json.loads(destination.read_text(encoding="utf-8"))
        else:
            payload = {"target": banc, "description": _describe(ops), "placements": []}
            destination.parent.mkdir(parents=True, exist_ok=True)
            written.append(destination.relative_to(out).as_posix())
        payload["placements"].extend(ops)
        payload["description"] = _describe(payload["placements"])
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        log.append({
            "level": "info",
            "message": f"Preserved {len(ops)} imported placement operation(s) for {banc}.",
        })


def export_mod(project, dump, *, install: bool = False, game: Path | None = None) -> dict:
    """Write ``<project>/build/mod``. Install copies it to ``<game>/mods/local/<slug>``.

    An unchanged new project writes no files. Imported mods retain their source
    files even when their placement patches have been removed.
    """
    from ocarina_forge.projects import game_root, install_dir

    out = project.root / "build" / "mod"
    source_mod = project.root / ".longshot-source"
    source_info = None
    if source_mod.is_dir():
        from ocarina_forge.importers.longshot_mod import inspect_local_mod

        try:
            original_manifest = json.loads((source_mod / "mod.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BancError(f"Imported mod manifest is unreadable: {exc}", "mod_export") from exc
        if not isinstance(original_manifest, dict) or not isinstance(original_manifest.get("id"), str):
            raise BancError("Imported mod manifest has no string id.", "mod_export")
        source_info = inspect_local_mod(source_mod, original_manifest["id"])
    if out.exists():
        shutil.rmtree(out)
    if source_mod.is_dir():
        shutil.copytree(source_mod, out)
        old_patches = out / "patches"
        if old_patches.exists():
            shutil.rmtree(old_patches)

    log: list[dict] = []
    written: list[str] = []
    warnings: list[str] = []
    generated: set[tuple[str, str, str]] = set()
    slug = mod_slug(project.manifest["name"])
    imported_ids: dict[str, set[str]] = {}
    if source_info:
        for banc, actors in source_info["patches"]:
            imported_ids.setdefault(banc, set()).update(actors)

    for banc in project.modified_scenes():
        diff = project.load_scene_diff(banc)
        try:
            document, _dict_id, _raw = dump.read_byml(banc)
        except BancError as exc:
            raise BancError(f"{banc}: {exc.message}", exc.code) from exc
        ops, notes = placement_ops(document, diff, imported_ids.get(banc, set()))
        for note in notes:
            warnings.append(f"{banc}: {note}")
            log.append({"level": "warn", "message": f"{banc}: {note}"})
        if not ops:
            if banc not in imported_ids:
                log.append({"level": "info", "message": f"{banc}: no placement op differs from the dump, so no patch was written."})
            continue
        generated.update((banc, op["hash"], op["path"]) for op in ops)
        destination = out / "patches" / _patch_name(banc)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "target": banc,
            "description": _describe(ops),
            "placements": ops,
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written.append(destination.relative_to(out).as_posix())
        log.append({"level": "info", "message": f"Wrote {destination.name} with {len(ops)} op{'s' if len(ops) != 1 else ''}."})

    if source_info is not None:
        _preserve_imported_patches(project, out, source_info, written, generated, log, warnings)

    if written or source_mod.is_dir():
        (out / "mod.json").write_text(
            json.dumps(_manifest_json(project, source_info["manifest"] if source_info else None), indent=2) + "\n",
            encoding="utf-8",
        )
        if written:
            (out / "romfs").mkdir(parents=True, exist_ok=True)
        written.insert(0, "mod.json")
        if source_mod.is_dir():
            log.append({
                "level": "info",
                "message": "Preserved the imported mod's runtime files and original manifest fields.",
            })
        elif written:
            (out / "romfs").mkdir(parents=True, exist_ok=True)
            log.append({
                "level": "info",
                "message": (
                    "romfs/ is an empty directory. The game indexes a manifest mod's patches only when that "
                    "directory exists. romfs/.keep is not written: the loader would serve it as a RomFS file, "
                    "and longshot mod check does not require the file. No src/main.ts: a patch-only mod has no runtime."
                ),
            })
        if written:
            log.append({
                "level": "info",
                "message": (
                    "The first save in an area can need a full game restart. The size table is read once at "
                    "process start, and a shared Translate container grows the banc."
                ),
            })
    else:
        log.append({"level": "info", "message": "No patch files. The mod folder was not written."})

    installed = None
    if install:
        config_game = None if game is not None else _config()
        dest = install_dir(config_game, slug, game=game if game is not None else game_root(config_game))
        if dest.exists():
            shutil.rmtree(dest)
        if written or source_mod.is_dir():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(out, dest)
            installed = str(dest)
            log.append({"level": "info", "message": f"Installed to {dest}."})
        else:
            log.append({
                "level": "info",
                "message": f"Removed {dest} if it was there, so the game does not keep a mod with no effect.",
            })

    return {
        "ok": True,
        "modId": mod_id(project.manifest["name"]),
        "slug": slug,
        "romfs": str(out) if written else "",
        "files": _files(out) if written else [],
        "installed": installed,
        "warnings": warnings,
        "log": log,
    }


def _describe(ops: list[dict]) -> str:
    paths = ", ".join(dict.fromkeys(op["path"] for op in ops))
    return f"{len(ops)} placement {paths} op{'s' if len(ops) != 1 else ''}."


def _files(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _config() -> dict:
    from ocarina_forge.config import load_config

    return load_config()
