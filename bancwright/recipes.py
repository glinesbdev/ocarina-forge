"""Actors an After the Ocarina build would add.

Scene YAML that is not an actor diff can still place something. Display
stones name a gyaml, a banc, and a translate. The viewport draws those with
the same gizmo as any other actor and writes the new translate, rotate,
scale, and params back into the recipe file, so the next build uses them.
The vanilla banc is unchanged until the project is built.

The first edit of a file is remembered under ``.bancwright/recipe-baseline.json``.
Changes and Revert compare against that snapshot. Undo sends the previous
rows for one banc and leaves every other banc in the file alone.
"""

from __future__ import annotations

import copy
import json
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

from bancwright.actors import category, is_region, is_volume
from bancwright.errors import BancError
from bancwright.project import load_yaml
from bancwright.values import get_path, set_path, tag, to_yaml, untag

_SKIP_LISTS = {"stones", "actors"}


def recipe_actors(project, banc: str) -> list[dict]:
    """Placements in this project that the build adds to ``banc``."""
    found: list[dict] = []
    for path, relative, data in _documents(project):
        if isinstance(data.get("actors"), dict) and data.get("banc"):
            continue
        used: set[str] = set()
        for key, index, row in _iter_rows(data):
            placed = _placement(data, row, banc, relative, key, index, used)
            if placed:
                found.append(placed)
    return found


def apply_recipe_command(project, banc: str, command: dict) -> str | None:
    """Write one edit into the recipe file. Returns the id to keep selected."""
    name = str(command.get("command") or "")
    ident = str(command.get("id") or "")
    if name == "recipe_restore":
        replace_recipes(project, banc, command.get("recipes") or [])
        return ident or None
    located = _locate(project, ident)
    _ensure_baseline(project, located.relative, located.data)
    if name in ("remove", "hide"):
        _delete_at(located)
        _save(located)
        return None
    if name == "duplicate":
        return _duplicate(located, banc)
    if name == "transform":
        _apply_transform(located.row, command)
        _save(located)
        return ident
    if name == "set_name":
        new_name = str(command.get("name") or "").strip()
        if not new_name:
            raise BancError("An instance name cannot be empty.", "edit")
        located.row["name"] = new_name
        _save(located)
        return _id_for(located.relative, new_name, located.row.get("gyaml"), located.index)
    if name == "set":
        _set_param(located.row, str(command.get("path") or ""), command.get("value"))
        _save(located)
        return ident
    if name == "revert":
        return _revert_row(project, located)
    if name == "revert_part":
        return _revert_part(project, located, command, banc)
    raise BancError(f"Recipe actors have no {name or 'edit'} command.", "edit")


def replace_recipes(project, banc: str, records: list) -> None:
    """Replace this banc's recipe rows with ``records``. Other bancs stay."""
    wanted: dict[str, list] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        recipe = record.get("recipe") or {}
        relative = str(recipe.get("file") or "")
        if relative:
            wanted.setdefault(relative, []).append(record)
    files = set(wanted)
    for path, relative, data in _documents(project):
        if _file_has_banc(data, banc):
            files.add(relative)
    for relative in sorted(files):
        path = project.root / relative
        if not path.is_file():
            continue
        located_yaml, data = _load_roundtrip(path)
        _sync_banc(data, banc, wanted.get(relative) or [])
        _dump(located_yaml, data, path)


def recipe_change_entries(project) -> list[tuple[str, dict]]:
    """Field diffs between the first-edit snapshot and the recipe files now."""
    baseline = _baseline(project).get("files") or {}
    if not baseline:
        return []
    current: dict[str, list[dict]] = {}
    for _path, relative, data in _documents(project):
        current[relative] = [_plain_row(key, data, row) for key, _index, row in _iter_rows(data)]
    entries: list[tuple[str, dict]] = []
    for relative, saved_rows in baseline.items():
        if not isinstance(saved_rows, list):
            continue
        now = current.get(relative) or []
        used: set[int] = set()
        for saved in saved_rows:
            if not isinstance(saved, dict):
                continue
            match = _take_plain(now, saved, used)
            banc = str(saved.get("banc") or "")
            if not banc:
                continue
            ident = _id_for(relative, saved.get("name"), saved.get("gyaml"), 0)
            if match is None:
                entries.append((banc, _change_row("removed", ident, saved, relative, "removed", "In the recipe", "Removed from the recipe")))
                continue
            entries.extend(_field_diffs(banc, ident, relative, saved, match))
        for index, row in enumerate(now):
            if index in used:
                continue
            banc = str(row.get("banc") or "")
            if not banc:
                continue
            ident = _id_for(relative, row.get("name"), row.get("gyaml"), index)
            place = _world_text(row)
            after = f"{row.get('gyaml') or 'actor'} at {place}" if place else str(row.get("gyaml") or "actor")
            entries.append((banc, _change_row("added", ident, row, relative, "added", "Not in this recipe", after)))
    return entries


def _documents(project):
    folder = project.root / "scenes"
    if not folder.is_dir():
        return
    for path in sorted(folder.rglob("*.yaml")):
        if not path.is_file():
            continue
        try:
            data = load_yaml(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if isinstance(data, dict):
            yield path, path.relative_to(project.root).as_posix(), data


def _iter_rows(data: dict):
    stones = data.get("stones")
    if isinstance(stones, list):
        for index, row in enumerate(stones):
            if isinstance(row, dict):
                yield "stones", index, row
    for key, value in data.items():
        if key in _SKIP_LISTS or not isinstance(value, list):
            continue
        for index, row in enumerate(value):
            if isinstance(row, dict) and "gyaml" in row and "translate" in row:
                yield key, index, row
    if data.get("place") is not False and "gyaml" in data and "translate" in data:
        yield "", 0, data


def _placement(data, row, banc, relative, key, index, used: set[str]) -> dict | None:
    target = row.get("banc") or data.get("banc")
    if target != banc:
        return None
    gyaml = row.get("gyaml")
    if not isinstance(gyaml, str) or not gyaml.strip():
        return None
    translate = _xyz(row.get("translate"))
    if translate is None:
        return None
    offset = _number(row.get("y_offset"), 0.0)
    translate = [translate[0], translate[1] + offset, translate[2]]
    rotate = _xyz(row.get("rotate")) or [0.0, 0.0, 0.0]
    scale = _xyz(row.get("scale")) or [1.0, 1.0, 1.0]
    flag = row.get("flag") if isinstance(row.get("flag"), str) else data.get("flag")
    flag = flag.strip() if isinstance(flag, str) else ""
    name = row.get("name") if isinstance(row.get("name"), str) and row.get("name").strip() else None
    note = "added by build, flag-gated" if flag else "added by build"
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    ident = _make_id(relative, name, gyaml.strip(), index, used)
    vanilla = row.get("vanilla_entry")
    return {
        "id": ident,
        "gyaml": gyaml.strip(),
        "name": name,
        "category": category(gyaml.strip()),
        "translate": translate,
        "rotate": rotate,
        "scale": scale,
        "volume": is_volume(gyaml.strip(), scale),
        "region": is_region(gyaml.strip()),
        "srtHash": None,
        "status": "recipe",
        "params": tag(_plain(params)),
        "recipe": {
            "file": relative,
            "list": key or "stones",
            "flag": flag,
            "note": note,
            "yOffset": offset,
            "vanillaEntry": vanilla if isinstance(vanilla, int) and not isinstance(vanilla, bool) else None,
        },
    }


def _make_id(relative: str, name, gyaml: str, index: int, used: set[str]) -> str:
    base = f"recipe:{relative}:{name}" if name else f"recipe:{relative}:{gyaml}:{index}"
    ident = base if base not in used else f"{base}:{index}"
    used.add(ident)
    return ident


def _id_for(relative: str, name, gyaml, index: int) -> str:
    if isinstance(name, str) and name.strip():
        return f"recipe:{relative}:{name.strip()}"
    return f"recipe:{relative}:{gyaml}:{index}"


class _Located:
    def __init__(self, path: Path, relative: str, yaml, data, key: str, index: int, row):
        self.path = path
        self.relative = relative
        self.yaml = yaml
        self.data = data
        self.key = key
        self.index = index
        self.row = row


def _locate(project, ident: str) -> _Located:
    if not ident.startswith("recipe:"):
        raise BancError("That is not a recipe actor.", "edit")
    folder = project.root / "scenes"
    if folder.is_dir():
        paths = sorted(folder.rglob("*.yaml"))
    else:
        paths = []
    for path in paths:
        relative = path.relative_to(project.root).as_posix()
        prefix = f"recipe:{relative}:"
        if not ident.startswith(prefix):
            continue
        yaml, data = _load_roundtrip(path)
        if not isinstance(data, dict):
            continue
        used: set[str] = set()
        for key, index, row in _iter_rows(data):
            gyaml = row.get("gyaml") if isinstance(row.get("gyaml"), str) else ""
            name = row.get("name") if isinstance(row.get("name"), str) and row.get("name").strip() else None
            if _make_id(relative, name, gyaml.strip(), index, used) == ident:
                return _Located(path, relative, yaml, data, key, index, row)
    raise BancError("That recipe actor is no longer in the project.", "edit")


def _apply_transform(row, command: dict) -> None:
    translate = _xyz(command.get("translate"))
    if translate is not None:
        offset = _number(row.get("y_offset"), 0.0)
        _write_vec(row, "translate", [translate[0], translate[1] - offset, translate[2]], always=True)
    rotate = _xyz(command.get("rotate"))
    if rotate is not None:
        _write_vec(row, "rotate", rotate, always=False)
    scale = _xyz(command.get("scale"))
    if scale is not None:
        _write_vec(row, "scale", scale, always=False, default=(1.0, 1.0, 1.0))


def _write_vec(row, key: str, values: list[float], always: bool, default: tuple | None = None) -> None:
    if not always and key not in row and default is not None and _near(values, default):
        return
    if not always and key not in row and default is None and _near(values, (0.0, 0.0, 0.0)):
        return
    if key in row and isinstance(row[key], list) and len(row[key]) >= 3:
        for index, value in enumerate(values[:3]):
            row[key][index] = _same_number(row[key][index], value)
        return
    row[key] = [float(value) for value in values[:3]]


def _same_number(old, new: float):
    if isinstance(old, int) and not isinstance(old, bool) and float(new).is_integer():
        return int(round(float(new)))
    return float(new)


def _set_param(row, path: str, value) -> None:
    if not path:
        raise BancError("Which parameter?", "edit")
    params = row.get("params")
    if not isinstance(params, dict):
        row["params"] = {}
        params = row["params"]
    set_path(params, path, untag(value))


def _duplicate(located: _Located, banc: str) -> str:
    if not located.key:
        raise BancError("This recipe is a single actor, not a list, so it cannot be duplicated here.", "edit")
    seq = located.data.get(located.key)
    names = set()
    for row in seq:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            names.add(row["name"])
    original = located.row.get("name") if isinstance(located.row.get("name"), str) else located.row.get("gyaml")
    name = _fresh_name(names, str(original or "Actor"))
    clone = copy.deepcopy(located.row)
    clone["name"] = name
    translate = _xyz(clone.get("translate")) or [0.0, 0.0, 0.0]
    _write_vec(clone, "translate", [translate[0] + 1.0, translate[1], translate[2]], always=True)
    if "banc" not in clone and not located.data.get("banc"):
        clone["banc"] = banc
    seq.insert(located.index + 1, clone)
    _save(located)
    return _id_for(located.relative, name, clone.get("gyaml"), located.index + 1)


def _fresh_name(taken: set[str], base: str) -> str:
    number = 2
    while True:
        candidate = f"{base}_{number}"
        if candidate not in taken:
            return candidate
        number += 1


def _delete_at(located: _Located) -> None:
    if not located.key:
        raise BancError("This recipe is the whole file. Remove the file in the project instead.", "edit")
    seq = located.data.get(located.key)
    if not isinstance(seq, list) or located.index >= len(seq):
        raise BancError("That recipe entry is already gone.", "edit")
    del seq[located.index]


def _revert_row(project, located: _Located) -> str | None:
    saved = _saved_row(project, located)
    if saved is None:
        _delete_at(located)
        _save(located)
        return None
    _copy_fields(located.row, saved)
    _save(located)
    return _id_for(located.relative, saved.get("name"), saved.get("gyaml"), located.index)


def _revert_part(project, located: _Located, command: dict, banc: str) -> str | None:
    part = str(command.get("part") or "")
    if part == "added":
        _delete_at(located)
        _save(located)
        return None
    if part == "removed":
        saved = _saved_by_id(project, banc, str(command.get("id") or ""))
        if saved is None:
            raise BancError("There is no earlier copy of that recipe actor to put back.", "edit")
        _insert_plain(project, saved)
        return _id_for(saved["file"], saved.get("name"), saved.get("gyaml"), 0)
    saved = _saved_row(project, located)
    if saved is None:
        _delete_at(located)
        _save(located)
        return None
    if part == "translate":
        _write_vec(located.row, "translate", list(saved.get("translate") or [0, 0, 0]), always=True)
    elif part == "rotate":
        _restore_optional_vec(located.row, "rotate", saved.get("rotate"))
    elif part == "scale":
        _restore_optional_vec(located.row, "scale", saved.get("scale"))
    elif part == "name":
        if saved.get("name"):
            located.row["name"] = saved["name"]
    elif part == "param":
        _restore_param(located.row, saved.get("params") or {}, str(command.get("path") or ""))
    else:
        raise BancError(f"Nothing on a recipe actor is called {part or 'that'}.", "edit")
    _save(located)
    return _id_for(located.relative, located.row.get("name"), located.row.get("gyaml"), located.index)


def _restore_optional_vec(row, key: str, value) -> None:
    numbers = _xyz(value)
    if numbers is None:
        if key in row:
            del row[key]
        return
    _write_vec(row, key, numbers, always=True)


def _restore_param(row, params: dict, path: str) -> None:
    if not path:
        if params:
            row["params"] = copy.deepcopy(params)
        elif "params" in row:
            del row["params"]
        return
    try:
        value = get_path(params, path)
    except (KeyError, TypeError, IndexError):
        _drop_path(row.get("params"), path)
        if isinstance(row.get("params"), dict) and not row["params"]:
            del row["params"]
        return
    if not isinstance(row.get("params"), dict):
        row["params"] = {}
    set_path(row["params"], path, copy.deepcopy(value))


def _drop_path(root, path: str) -> None:
    if not isinstance(root, dict):
        return
    parts = [part for part in path.split(".") if part]
    current = root
    for part in parts[:-1]:
        current = current.get(part) if isinstance(current, dict) else None
        if not isinstance(current, dict):
            return
    if parts and isinstance(current, dict):
        current.pop(parts[-1], None)


def _sync_banc(data, banc: str, records: list) -> None:
    if not isinstance(data, dict):
        return
    used: set[int] = set()
    keys = [key for key, _index, _row in _iter_rows(data)]
    seen_keys: list[str] = []
    for key in keys:
        if key and key not in seen_keys:
            seen_keys.append(key)
    if not seen_keys and records:
        data["stones"] = []
        seen_keys = ["stones"]
    for key in seen_keys:
        seq = data.get(key)
        if not isinstance(seq, list):
            continue
        index = 0
        while index < len(seq):
            row = seq[index]
            if not isinstance(row, dict) or _row_banc(data, row) != banc:
                index += 1
                continue
            match = _take_record(records, row, used)
            if match is None:
                del seq[index]
                continue
            _apply_record(row, match)
            index += 1
    for index, record in enumerate(records):
        if index in used:
            continue
        recipe = record.get("recipe") or {}
        key = str(recipe.get("list") or "stones")
        if key not in data or not isinstance(data.get(key), list):
            data[key] = []
        data[key].append(_record_row(record, banc))


def _apply_record(row, record: dict) -> None:
    recipe = record.get("recipe") or {}
    offset = _number(recipe.get("yOffset"), _number(row.get("y_offset"), 0.0))
    world = _xyz(record.get("translate"))
    if world is not None:
        _write_vec(row, "translate", [world[0], world[1] - offset, world[2]], always=True)
    rotate = _xyz(record.get("rotate"))
    if rotate is not None:
        _write_vec(row, "rotate", rotate, always=False)
    scale = _xyz(record.get("scale"))
    if scale is not None:
        _write_vec(row, "scale", scale, always=False, default=(1.0, 1.0, 1.0))
    if isinstance(record.get("name"), str) and record["name"].strip():
        row["name"] = record["name"].strip()
    if isinstance(record.get("gyaml"), str) and record["gyaml"].strip():
        row["gyaml"] = record["gyaml"].strip()
    params = _params_from_view(record.get("params"))
    if params:
        row["params"] = params
    elif "params" in row:
        del row["params"]


def _record_row(record: dict, banc: str) -> dict:
    recipe = record.get("recipe") or {}
    offset = _number(recipe.get("yOffset"), 0.0)
    world = _xyz(record.get("translate")) or [0.0, 0.0, 0.0]
    row = {
        "gyaml": str(record.get("gyaml") or ""),
        "name": str(record.get("name") or record.get("gyaml") or "Actor"),
        "banc": banc,
        "translate": [float(world[0]), float(world[1] - offset), float(world[2])],
        "y_offset": offset,
    }
    rotate = _xyz(record.get("rotate"))
    if rotate is not None and not _near(rotate, (0.0, 0.0, 0.0)):
        row["rotate"] = [float(value) for value in rotate]
    scale = _xyz(record.get("scale"))
    if scale is not None and not _near(scale, (1.0, 1.0, 1.0)):
        row["scale"] = [float(value) for value in scale]
    vanilla = recipe.get("vanillaEntry")
    if isinstance(vanilla, int) and not isinstance(vanilla, bool):
        row["vanilla_entry"] = vanilla
    params = _params_from_view(record.get("params"))
    if params:
        row["params"] = params
    return row


def _params_from_view(value) -> dict:
    if value is None:
        return {}
    try:
        plain = untag(value)
    except BancError:
        plain = value
    if not isinstance(plain, dict):
        return {}
    encoded = to_yaml(plain)
    return encoded if isinstance(encoded, dict) else {}


def _file_has_banc(data: dict, banc: str) -> bool:
    return any(_row_banc(data, row) == banc for _key, _index, row in _iter_rows(data))


def _row_banc(data, row) -> str:
    target = row.get("banc") if isinstance(row, dict) else None
    if not isinstance(target, str) or not target:
        target = data.get("banc") if isinstance(data, dict) else None
    return target if isinstance(target, str) else ""


def _take_record(records: list, row, used: set[int]):
    name = row.get("name") if isinstance(row, dict) else None
    gyaml = row.get("gyaml") if isinstance(row, dict) else None
    for index, record in enumerate(records):
        if index in used or not isinstance(record, dict):
            continue
        if name and record.get("name") == name:
            used.add(index)
            return record
        if not name and record.get("gyaml") == gyaml:
            used.add(index)
            return record
    gyaml_hits = [
        index
        for index, record in enumerate(records)
        if index not in used and isinstance(record, dict) and record.get("gyaml") == gyaml
    ]
    if len(gyaml_hits) == 1:
        used.add(gyaml_hits[0])
        return records[gyaml_hits[0]]
    return None


def _take_plain(rows: list[dict], saved: dict, used: set[int]):
    for index, row in enumerate(rows):
        if index in used:
            continue
        if saved.get("name") and row.get("name") == saved.get("name") and row.get("banc") == saved.get("banc"):
            used.add(index)
            return row
        if not saved.get("name") and row.get("gyaml") == saved.get("gyaml") and row.get("banc") == saved.get("banc"):
            used.add(index)
            return row
    gyaml_hits = [
        index
        for index, row in enumerate(rows)
        if index not in used and row.get("gyaml") == saved.get("gyaml") and row.get("banc") == saved.get("banc")
    ]
    if len(gyaml_hits) == 1:
        used.add(gyaml_hits[0])
        return rows[gyaml_hits[0]]
    return None


def _plain_row(key: str, data: dict, row: dict) -> dict:
    return {
        "list": key or "stones",
        "gyaml": row.get("gyaml") if isinstance(row.get("gyaml"), str) else "",
        "name": row.get("name") if isinstance(row.get("name"), str) else "",
        "banc": _row_banc(data, row),
        "translate": _xyz(row.get("translate")) or [0.0, 0.0, 0.0],
        "rotate": _xyz(row.get("rotate")),
        "scale": _xyz(row.get("scale")),
        "y_offset": _number(row.get("y_offset"), 0.0),
        "params": _plain(row.get("params")) if isinstance(row.get("params"), dict) else {},
        "vanilla_entry": row.get("vanilla_entry") if isinstance(row.get("vanilla_entry"), int) else None,
    }


def _copy_fields(row, saved: dict) -> None:
    if saved.get("gyaml"):
        row["gyaml"] = saved["gyaml"]
    if saved.get("name"):
        row["name"] = saved["name"]
    _write_vec(row, "translate", list(saved.get("translate") or [0, 0, 0]), always=True)
    _restore_optional_vec(row, "rotate", saved.get("rotate"))
    _restore_optional_vec(row, "scale", saved.get("scale"))
    params = saved.get("params") or {}
    if params:
        row["params"] = copy.deepcopy(params)
    elif "params" in row:
        del row["params"]


def _saved_row(project, located: _Located):
    rows = (_baseline(project).get("files") or {}).get(located.relative) or []
    name = located.row.get("name") if isinstance(located.row.get("name"), str) else ""
    gyaml = located.row.get("gyaml") if isinstance(located.row.get("gyaml"), str) else ""
    banc = _row_banc(located.data, located.row)
    same = [saved for saved in rows if isinstance(saved, dict) and saved.get("banc") == banc]
    for saved in same:
        if name and saved.get("name") == name:
            return saved
        if not name and saved.get("gyaml") == gyaml:
            return saved
    gyaml_hits = [saved for saved in same if saved.get("gyaml") == gyaml]
    if len(gyaml_hits) == 1:
        return gyaml_hits[0]
    return None


def _saved_by_id(project, banc: str, ident: str):
    files = _baseline(project).get("files") or {}
    for relative, rows in files.items():
        if not isinstance(rows, list):
            continue
        for saved in rows:
            if not isinstance(saved, dict) or saved.get("banc") != banc:
                continue
            if _id_for(relative, saved.get("name"), saved.get("gyaml"), 0) == ident:
                found = dict(saved)
                found["file"] = relative
                return found
    return None


def _insert_plain(project, saved: dict) -> None:
    relative = saved["file"]
    path = project.root / relative
    yaml, data = _load_roundtrip(path)
    key = saved.get("list") or "stones"
    if not isinstance(data.get(key), list):
        data[key] = []
    row = {
        "gyaml": saved.get("gyaml") or "",
        "name": saved.get("name") or saved.get("gyaml") or "Actor",
        "banc": saved.get("banc"),
        "translate": [float(value) for value in (saved.get("translate") or [0, 0, 0])],
        "y_offset": _number(saved.get("y_offset"), 0.0),
    }
    if saved.get("rotate"):
        row["rotate"] = [float(value) for value in saved["rotate"]]
    if saved.get("scale"):
        row["scale"] = [float(value) for value in saved["scale"]]
    if saved.get("vanilla_entry"):
        row["vanilla_entry"] = saved["vanilla_entry"]
    if saved.get("params"):
        row["params"] = copy.deepcopy(saved["params"])
    data[key].append(row)
    _dump(yaml, data, path)


def _field_diffs(banc: str, ident: str, relative: str, before: dict, after: dict) -> list[tuple[str, dict]]:
    rows = []
    if not _near(before.get("translate") or [], after.get("translate") or []):
        rows.append((banc, _change_row("moved", ident, after, relative, "translate", _world_text(before), _world_text(after), "Position")))
    if before.get("rotate") != after.get("rotate") and not _near(before.get("rotate") or [0, 0, 0], after.get("rotate") or [0, 0, 0]):
        rows.append((banc, _change_row("turned", ident, after, relative, "rotate", _deg_text(before.get("rotate")), _deg_text(after.get("rotate")), "Rotation")))
    elif (before.get("rotate") is None) != (after.get("rotate") is None):
        rows.append((banc, _change_row("turned", ident, after, relative, "rotate", _deg_text(before.get("rotate")), _deg_text(after.get("rotate")), "Rotation")))
    if (before.get("scale") is None) != (after.get("scale") is None) or not _near(before.get("scale") or [1, 1, 1], after.get("scale") or [1, 1, 1]):
        rows.append((banc, _change_row("turned", ident, after, relative, "scale", _vec_text(before.get("scale"), "not set"), _vec_text(after.get("scale"), "not set"), "Scale")))
    if (before.get("name") or "") != (after.get("name") or ""):
        rows.append((banc, _change_row("params", ident, after, relative, "name", before.get("name") or "no name", after.get("name") or "no name", "Instance name")))
    rows.extend(_param_diffs(banc, ident, relative, after, before.get("params") or {}, after.get("params") or {}))
    return rows


def _param_diffs(banc, ident, relative, actor, before: dict, after: dict, prefix: str = "") -> list[tuple[str, dict]]:
    rows = []
    keys = list(dict.fromkeys([*before.keys(), *after.keys()]))
    for key in keys:
        path = f"{prefix}.{key}" if prefix else str(key)
        old = before.get(key)
        new = after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            rows.extend(_param_diffs(banc, ident, relative, actor, old, new, path))
            continue
        if old != new:
            rows.append((banc, _change_row("params", ident, actor, relative, "param", _short(old), _short(new), path, path)))
    return rows


def _change_row(kind, ident, actor, relative, part, before, after, title: str | None = None, path: str = "") -> dict:
    label = actor.get("name") or actor.get("gyaml") or "Recipe actor"
    return {
        "id": f"{kind}:{ident}:{part}:{path}",
        "kind": kind,
        "actorId": ident,
        "gyaml": actor.get("gyaml") or "",
        "label": label,
        "title": title or label,
        "before": before,
        "after": after,
        "source": "recipe",
        "sourceLabel": "Build recipe",
        "file": relative,
        "part": part,
        "path": path,
        "revertable": True,
    }


def _world_text(row: dict) -> str:
    translate = row.get("translate") or [0, 0, 0]
    offset = _number(row.get("y_offset"), 0.0)
    return _vec_text([translate[0], translate[1] + offset, translate[2]], "")


def _vec_text(values, empty: str) -> str:
    numbers = _xyz(values)
    if numbers is None:
        return empty
    return ", ".join(_trim(value) for value in numbers)


def _deg_text(values) -> str:
    numbers = _xyz(values)
    if numbers is None:
        return "donor"
    import math

    return ", ".join(f"{_trim(value * 180 / math.pi, 2)}°" for value in numbers)


def _trim(value, places: int = 4) -> str:
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _short(value) -> str:
    if value is None:
        return "not set"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _ensure_baseline(project, relative: str, data) -> None:
    baseline = _baseline(project)
    files = baseline.setdefault("files", {})
    if relative in files:
        return
    files[relative] = [_plain_row(key, data, row) for key, _index, row in _iter_rows(data)]
    _write_baseline(project, baseline)


def _baseline_path(project) -> Path:
    return project.root / ".bancwright" / "recipe-baseline.json"


def _baseline(project) -> dict:
    path = _baseline_path(project)
    if not path.is_file():
        return {"files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": {}}
    return data if isinstance(data, dict) else {"files": {}}


def _write_baseline(project, data: dict) -> None:
    path = _baseline_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_roundtrip(path: Path):
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(path.read_text(encoding="utf-8"))
    return yaml, data


def _save(located: _Located) -> None:
    _dump(located.yaml, located.data, located.path)


def _dump(yaml, data, path: Path) -> None:
    buffer = StringIO()
    yaml.dump(data, buffer)
    text = buffer.getvalue()
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _xyz(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    numbers = []
    for item in value[:3]:
        if isinstance(item, bool):
            return None
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            return None
    return numbers


def _number(value, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _near(left, right, eps: float = 1e-4) -> bool:
    a = list(left or [])
    b = list(right or [])
    if len(a) < 3 or len(b) < 3:
        return False
    return all(abs(float(a[index]) - float(b[index])) <= eps for index in range(3))


def _plain(value):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value
