"""Import After the Ocarina scene YAML into an Ocarina Forge project.

The reference repo stores one YAML file per scene under src/scenes. This
accepts that shape and a few spellings, so a story mod can be read without
being rewritten by hand:

    banc: Banc/HyruleField/HyruleField_Adult_Static.bcett.byml.zs
    actors:
      - hash: "0x1000000000000021"
        translate: [0, 0, 6]
        rotate: [0, 3.141592, 0]
        params:
          Name: Posted guard
      - op: add
        gyaml: Npc_Zora_01
        translate: [3, 0, 1]
      - hash: "0x1000000000000020"
        hide: true

`file` and `path` mean the same thing as `banc`. `edits` and `ops` mean the
same thing as `actors`. A file may also be a list of those documents, or a
mapping whose keys are banc paths.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import yaml

from ocarina_forge.actors import format_hash, parse_hash
from ocarina_forge.errors import BancError
from ocarina_forge.project import normalize_ids
from ocarina_forge.values import flatten


def import_scenes(project, source: Path) -> list[str]:
    source = Path(source).expanduser()
    if not source.exists():
        raise BancError(f"Nothing at {source} to import.", "import")
    files = [source] if source.is_file() else sorted(path for path in source.rglob("*.yaml") if path.is_file())
    files = [path for path in files if path.name != "manifest.yaml"]
    if not files:
        raise BancError(f"{source} has no YAML scene patches.", "import")
    messages = []
    for file in files:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        scenes = list(_documents(data, file))
        for banc, entries in scenes:
            diff = project.load_scene_diff(banc)
            count = _merge(diff, entries)
            project.save_scene_diff(diff)
            messages.append(f"{file.name}: {count} edit(s) -> {banc}")
    if not messages:
        raise BancError(f"{source} did not contain a scene patch.", "import")
    return messages


def _documents(data, file: Path):
    if data is None:
        return
    if isinstance(data, list):
        for item in data:
            yield from _documents(item, file)
        return
    if not isinstance(data, dict):
        raise BancError(f"{file} is not a scene patch.", "import")
    banc = data.get("banc") or data.get("file") or data.get("path")
    if banc:
        entries = data.get("actors", data.get("edits", data.get("ops", [])))
        yield str(banc), entries
        return
    if data and all(isinstance(key, str) and "bcett" in key for key in data):
        for key, body in data.items():
            if isinstance(body, list):
                yield key, body
            elif isinstance(body, dict):
                yield key, body.get("actors", body.get("edits", body.get("ops", [])))
            else:
                raise BancError(f"{file}: {key} is not a list of edits.", "import")
        return
    raise BancError(
        f"{file} needs a banc path and an actors list. See docs/project-format.md for the After the Ocarina shape.",
        "import",
    )


def _merge(diff: dict, entries) -> int:
    if isinstance(entries, dict):
        # Already an Ocarina Forge actor map (hash -> record).
        normalized = normalize_ids(entries)
        diff["actors"].update(normalized)
        return len(normalized)
    if not isinstance(entries, list):
        raise BancError("Scene edits should be a list.", "import")
    for entry in entries:
        _one(diff, entry)
    return len(entries)


def _one(diff: dict, entry: dict) -> None:
    if not isinstance(entry, dict):
        raise BancError("Each scene edit has to be a mapping.", "import")
    op = str(entry.get("op") or "").lower()
    hide = bool(entry.get("hide") or entry.get("hidden") or op in ("hide", "hidden"))
    remove = bool(entry.get("remove") or entry.get("delete") or op in ("remove", "delete"))
    gyaml = entry.get("gyaml") or entry.get("actor") or entry.get("Gyaml")
    raw_id = entry.get("id", entry.get("hash", entry.get("Hash")))
    adding = op == "add" or entry.get("add") is True or (gyaml and raw_id is None and not remove and not hide)
    actors = diff["actors"]

    if adding:
        if not gyaml:
            raise BancError("An added actor needs a gyaml (the actor name).", "import")
        ident = format_hash(parse_hash(raw_id)) if raw_id is not None else format_hash(secrets.randbits(64) | (1 << 62))
        record = {
            "state": "hidden" if hide else "added",
            "actor": str(gyaml),
            "translate": _vec(entry, "translate", "Translate", [0.0, 0.0, 0.0]),
            "rotate": _vec(entry, "rotate", "Rotate", [0.0, 0.0, 0.0]),
            "scale": _vec(entry, "scale", "Scale", [1.0, 1.0, 1.0]),
        }
        label = entry.get("name", entry.get("Name"))
        if label:
            record["name"] = label
        params = dict(entry.get("params") or {})
        if "Dynamic" in entry:
            params["Dynamic"] = entry["Dynamic"]
        if params:
            record["params"] = params
        actors[ident] = record
        return

    if raw_id is None:
        raise BancError("An edit needs a hash, or op: add with an actor name.", "import")
    ident = format_hash(parse_hash(raw_id))
    if remove:
        actors[ident] = {"state": "removed", "gyaml": gyaml}
        return

    record = dict(actors.get(ident) or {})
    if record.get("state") == "added" or "actor" in record:
        record["state"] = "hidden" if hide else "added"
        if gyaml:
            record["actor"] = str(gyaml)
        _copy_transform(entry, record)
        if "name" in entry or "Name" in entry:
            record["name"] = entry.get("name", entry.get("Name"))
        actors[ident] = record
        return

    record["state"] = "hidden" if hide else record.get("state") or "edited"
    if hide and gyaml:
        record["gyaml"] = str(gyaml)
    elif gyaml and not hide:
        record["gyaml"] = str(gyaml)
    _copy_transform(entry, record)
    if "name" in entry or "Name" in entry:
        record["name"] = entry.get("name", entry.get("Name"))
    extra = dict(entry.get("params") or {})
    if isinstance(entry.get("set"), dict):
        record.setdefault("set", {}).update(entry["set"])
    if extra:
        flat = flatten(extra)
        if "Name" in flat and "name" not in record:
            record["name"] = flat.pop("Name")
        else:
            flat.pop("Name", None)
        if flat:
            record.setdefault("set", {}).update(flat)
    actors[ident] = record


def _copy_transform(entry: dict, record: dict) -> None:
    for key, alt in (("translate", "Translate"), ("rotate", "Rotate"), ("scale", "Scale")):
        if key in entry or alt in entry:
            record[key] = _vec(entry, key, alt, None)


def _vec(entry, key, alt, default):
    value = entry[key] if key in entry else entry.get(alt, default)
    if value is None:
        return default
    if isinstance(value, dict):
        return [float(value["X"]), float(value["Y"]), float(value["Z"])]
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise BancError(f"{key} needs three numbers.", "import")
    return [float(value[0]), float(value[1]), float(value[2])]
