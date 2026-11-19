"""Apply a project diff to a banc document, and turn editor commands into diffs.

The dump document is never modified in place. Export drops hidden and removed
actors. The editor view keeps hidden ones so they can be put back.
"""

from __future__ import annotations

import secrets
from copy import deepcopy

from bancwright.actors import (
    HEADER_KEYS,
    actor_id,
    format_hash,
    make_actor,
    parse_hash,
    read_vec,
    view_actor,
    write_vec,
)
from bancwright.errors import BancError
from bancwright.formats.byml import U64
from bancwright.values import flatten, from_yaml, set_path, to_yaml, untag

_GROUP_KEYS = ("AiGroups", "FarDeleteGroups", "SimultaneousGroups", "PlgGdTable")


def require_banc(document: dict, banc: str) -> None:
    actors = document.get("Actors") if isinstance(document, dict) else None
    if not isinstance(actors, list):
        raise BancError(
            f"{banc} has no Actors array. Bancwright edits placement files "
            "(*.bcett.byml), which are the files that list a scene's actors.",
            "bad_byml",
        )


def _close(left, right) -> bool:
    return len(left) == len(right) and all(abs(float(a) - float(b)) < 1e-5 for a, b in zip(left, right))


def _is_added(record: dict | None) -> bool:
    return bool(record) and (record.get("state") == "added" or "actor" in record)


def _base_map(document: dict) -> dict[str, dict]:
    found = {}
    for actor in document.get("Actors") or []:
        if isinstance(actor, dict):
            ident = actor_id(actor)
            if ident:
                found[ident] = actor
    return found


def _apply_record(actor: dict, record: dict) -> None:
    if record.get("gyaml"):
        actor["Gyaml"] = record["gyaml"]
    if "name" in record:
        if record["name"]:
            actor["Name"] = record["name"]
        else:
            actor.pop("Name", None)
    if record.get("translate") is not None:
        write_vec(actor, "Translate", record["translate"])
    if record.get("rotate") is not None:
        write_vec(actor, "Rotate", record["rotate"])
    if record.get("scale") is not None:
        write_vec(actor, "Scale", record["scale"])
    for path, value in (record.get("set") or {}).items():
        set_path(actor, path, from_yaml(value))


def _from_added(ident: str, record: dict) -> dict:
    if not record.get("actor"):
        raise BancError(f"Added actor {ident} has no actor name.", "project")
    params = from_yaml(record.get("params") or {})
    return make_actor(
        record["actor"],
        parse_hash(ident),
        record.get("translate") or [0.0, 0.0, 0.0],
        record.get("rotate"),
        record.get("scale"),
        record.get("name"),
        params if isinstance(params, dict) else {},
    )


def _hashes_in(value, found: set[int]) -> None:
    if isinstance(value, U64):
        found.add(int(value))
    elif isinstance(value, dict):
        for item in value.values():
            _hashes_in(item, found)
    elif isinstance(value, list):
        for item in value:
            _hashes_in(item, found)


def reference_warnings(document: dict, gone: set[int]) -> list[str]:
    if not gone:
        return []
    warnings = []
    blobs = [(key, document.get(key)) for key in _GROUP_KEYS if document.get(key)]
    header = document.get("PhiveHeader")
    if isinstance(header, dict) and header.get("PlacementGroups"):
        blobs.append(("PhiveHeader.PlacementGroups", header["PlacementGroups"]))
    for actor in document.get("Actors") or []:
        if isinstance(actor, dict) and actor.get("Links"):
            blobs.append((f"{actor.get('Gyaml', 'actor')} Links", actor["Links"]))
    for label, blob in blobs:
        found: set[int] = set()
        _hashes_in(blob, found)
        hit = sorted(found & gone)
        if hit:
            shown = ", ".join(format_hash(value) for value in hit[:4])
            warnings.append(
                f"{label} still points at {len(hit)} removed or hidden actor"
                f"{'s' if len(hit) != 1 else ''} ({shown}). "
                "Bancwright left that reference in place. Check the scene in Longshot."
            )
    return warnings


def compose(document: dict, diff: dict, *, drop_hidden: bool) -> tuple[list, list, list, list, set]:
    """Return actors, statuses, removed, warnings, gone hashes."""
    require_banc(document, diff.get("banc") or "this scene")
    warnings: list[str] = []
    actors_out: list[dict] = []
    statuses: list[str] = []
    removed: list[dict] = []
    gone: set[int] = set()
    seen: set[str] = set()
    records = diff.get("actors") or {}

    for index, actor in enumerate(document["Actors"]):
        if not isinstance(actor, dict):
            warnings.append(f"Actor {index} is not a dictionary, so it was left out of the editor.")
            continue
        ident = actor_id(actor)
        if not ident:
            warnings.append(
                f"Actor {index} ({actor.get('Gyaml', 'unnamed')}) has no Hash, so it cannot be selected. It is kept as-is."
            )
            actors_out.append(deepcopy(actor))
            statuses.append("locked")
            continue
        seen.add(ident)
        record = records.get(ident)
        if record is None:
            actors_out.append(deepcopy(actor))
            statuses.append("vanilla")
            continue
        state = record.get("state")
        if state == "removed":
            gone.add(parse_hash(ident))
            removed.append({"id": ident, "gyaml": record.get("gyaml") or actor.get("Gyaml") or "Actor"})
            continue
        edited = deepcopy(actor)
        _apply_record(edited, record)
        if state == "added" and record.get("actor"):
            edited["Gyaml"] = record["actor"]
        if state == "hidden":
            gone.add(parse_hash(ident))
            if drop_hidden:
                continue
            actors_out.append(edited)
            statuses.append("hidden")
            continue
        actors_out.append(edited)
        statuses.append("edited")

    for ident, record in records.items():
        if ident in seen or not isinstance(record, dict):
            continue
        state = record.get("state")
        if state == "removed":
            removed.append({"id": ident, "gyaml": record.get("gyaml") or "Removed"})
            gone.add(parse_hash(ident))
            continue
        if "actor" not in record:
            warnings.append(f"{ident} is in the project but not in this scene, so those edits were not applied.")
            continue
        made = _from_added(ident, record)
        if state == "hidden":
            gone.add(parse_hash(ident))
            if drop_hidden:
                continue
            actors_out.append(made)
            statuses.append("hidden")
        else:
            actors_out.append(made)
            statuses.append("added")
    return actors_out, statuses, removed, warnings, gone


def _shadow(document: dict, actors: list[dict]) -> dict:
    shadow = {"Actors": actors}
    for key in _GROUP_KEYS:
        if key in document:
            shadow[key] = document[key]
    if "PhiveHeader" in document:
        shadow["PhiveHeader"] = document["PhiveHeader"]
    return shadow


def sibling_bancs(banc: str, available: set[str]) -> list[str]:
    """The other age-matched banc: Static beside Dynamic, and the reverse.

    ``Banc/KokiriForest/KokiriForest_Child_Static.bcett.byml.zs`` pairs with
    ``KokiriForest_Child_Dynamic`` in the same folder. Names that do not end
    in ``_Static`` or ``_Dynamic`` have no sibling.
    """
    if not banc or ".." in banc.split("/"):
        return []
    folder, _, name = banc.rpartition("/")
    stem = name
    ext = ""
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            ext = suffix
            break
    if not ext:
        return []
    for source, dest in (("Static", "Dynamic"), ("Dynamic", "Static")):
        token = f"_{source}"
        if not stem.endswith(token):
            continue
        other = stem[: -len(token)] + f"_{dest}"
        candidate = f"{folder}/{other}{ext}" if folder else f"{other}{ext}"
        if candidate in available and candidate != banc:
            return [candidate]
    return []


def scene_view(document: dict, diff: dict) -> dict:
    actors, statuses, removed, warnings, gone = compose(document, diff, drop_hidden=False)
    warnings = warnings + reference_warnings(_shadow(document, actors), gone)
    return {
        "banc": diff.get("banc"),
        "actors": [view_actor(actor, status) for actor, status in zip(actors, statuses)],
        "removed": removed,
        "warnings": warnings,
        "diff": {"actors": diff.get("actors") or {}},
    }


def export_document(document: dict, diff: dict) -> tuple[dict, list[str]]:
    actors, _statuses, _removed, warnings, gone = compose(document, diff, drop_hidden=True)
    exported = deepcopy(document)
    exported["Actors"] = actors
    warnings = warnings + reference_warnings(_shadow(document, actors), gone)
    return exported, warnings


def _three(value, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise BancError(f"{label} needs three numbers.", "edit")
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError) as exc:
        raise BancError(f"{label} needs three numbers.", "edit") from exc


def _ensure(base: dict[str, dict], actors: dict, ident: str) -> dict:
    record = actors.get(ident)
    if record and record.get("state") == "removed":
        raise BancError("That actor was removed. Undo, or restore it, before editing it.", "edit")
    if _is_added(record):
        return record
    if ident not in base:
        raise BancError(f"No actor {ident} is placed in this scene.", "edit")
    if record is None:
        record = {"state": "edited"}
        actors[ident] = record
    return record


def _prune(base_actor: dict | None, actors: dict, ident: str, record: dict) -> None:
    if record.get("state") != "edited" or base_actor is None:
        return
    if record.get("set"):
        return
    if record.get("gyaml") and record["gyaml"] != base_actor.get("Gyaml"):
        return
    if "name" in record:
        current = record["name"] or None
        original = base_actor.get("Name") if isinstance(base_actor.get("Name"), str) else None
        if current != original:
            return
    if record.get("translate") is not None and not _close(record["translate"], read_vec(base_actor, "Translate", [0, 0, 0])):
        return
    if record.get("rotate") is not None and not _close(record["rotate"], read_vec(base_actor, "Rotate", [0, 0, 0])):
        return
    if record.get("scale") is not None and not _close(record["scale"], read_vec(base_actor, "Scale", [1, 1, 1])):
        return
    del actors[ident]


def _merged_by_id(document: dict, diff: dict) -> dict[str, dict]:
    actors, statuses, _removed, _warnings, _gone = compose(document, diff, drop_hidden=False)
    return {actor_id(actor): actor for actor, status in zip(actors, statuses) if actor_id(actor) and status != "locked"}


def apply_command(document: dict, diff: dict, command: dict) -> tuple[dict, str | None]:
    """Return (new diff, id to select). `document` is the vanilla banc."""
    require_banc(document, diff.get("banc") or "this scene")
    if not isinstance(command, dict) or "command" not in command:
        raise BancError("Missing edit command.", "edit")
    diff = deepcopy(diff)
    actors = diff.setdefault("actors", {})
    base = _base_map(document)
    name = command["command"]
    focus = None

    if name == "transform":
        ident = format_hash(parse_hash(command.get("id")))
        record = _ensure(base, actors, ident)
        if "translate" in command:
            record["translate"] = _three(command["translate"], "Position")
        if "rotate" in command:
            record["rotate"] = _three(command["rotate"], "Rotation")
        if "scale" in command:
            record["scale"] = _three(command["scale"], "Scale")
        _prune(base.get(ident), actors, ident, record)
        focus = ident
    elif name == "set":
        ident = format_hash(parse_hash(command.get("id")))
        path = str(command.get("path") or "")
        if path in ("Hash", "SRTHash") or path.endswith("Placement.ID"):
            raise BancError("The instance id stays put. It is how the project remembers this actor.", "edit")
        record = _ensure(base, actors, ident)
        value = untag(command.get("value"))
        if _is_added(record):
            set_path(record.setdefault("params", {}), path, value)
        else:
            record.setdefault("set", {})[path] = to_yaml(value)
            _prune(base.get(ident), actors, ident, record)
        focus = ident
    elif name == "set_name":
        ident = format_hash(parse_hash(command.get("id")))
        record = _ensure(base, actors, ident)
        text = command.get("name")
        text = str(text).strip() if text else ""
        if _is_added(record):
            if text:
                record["name"] = text
            else:
                record.pop("name", None)
        else:
            record["name"] = text or None
            _prune(base.get(ident), actors, ident, record)
        focus = ident
    elif name == "set_gyaml":
        ident = format_hash(parse_hash(command.get("id")))
        gyaml = str(command.get("gyaml") or "").strip()
        if not gyaml:
            raise BancError("An actor needs a name, such as Npc_Kokiri_F_01.", "edit")
        record = _ensure(base, actors, ident)
        if _is_added(record):
            record["actor"] = gyaml
        else:
            if gyaml == base[ident].get("Gyaml"):
                record.pop("gyaml", None)
            else:
                record["gyaml"] = gyaml
            _prune(base.get(ident), actors, ident, record)
        focus = ident
    elif name == "add":
        gyaml = str(command.get("actor") or command.get("gyaml") or "").strip()
        if not gyaml:
            raise BancError("Pick an actor name to add.", "edit")
        ident = format_hash(secrets.randbits(64) | (1 << 62))
        translate = _three(command["translate"], "Position") if command.get("translate") is not None else [0.0, 0.0, 0.0]
        actors[ident] = {
            "state": "added",
            "actor": gyaml,
            "translate": translate,
            "rotate": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
        }
        focus = ident
    elif name == "duplicate":
        ident = format_hash(parse_hash(command.get("id")))
        source = _merged_by_id(document, diff).get(ident)
        if source is None:
            raise BancError(f"No actor {ident} to duplicate.", "edit")
        new_id = format_hash(secrets.randbits(64) | (1 << 62))
        translate = read_vec(source, "Translate", [0.0, 0.0, 0.0])
        translate[0] += 1.0
        params = {key: value for key, value in source.items() if key not in HEADER_KEYS}
        record = {
            "state": "added",
            "actor": str(source.get("Gyaml") or "Obj"),
            "translate": translate,
            "rotate": read_vec(source, "Rotate", [0.0, 0.0, 0.0]),
            "scale": read_vec(source, "Scale", [1.0, 1.0, 1.0]),
        }
        if isinstance(source.get("Name"), str):
            record["name"] = source["Name"]
        if params:
            record["params"] = to_yaml(params)
        actors[new_id] = record
        focus = new_id
    elif name == "remove":
        ident = format_hash(parse_hash(command.get("id")))
        record = actors.get(ident)
        if _is_added(record):
            del actors[ident]
        elif ident in base:
            actors[ident] = {"state": "removed", "gyaml": base[ident].get("Gyaml")}
        else:
            raise BancError(f"No actor {ident} to remove.", "edit")
    elif name == "hide":
        ident = format_hash(parse_hash(command.get("id")))
        record = actors.get(ident)
        if _is_added(record):
            record["state"] = "hidden"
        elif ident in base:
            if record is None:
                actors[ident] = {"state": "hidden", "gyaml": base[ident].get("Gyaml")}
            else:
                record["state"] = "hidden"
                record.setdefault("gyaml", base[ident].get("Gyaml"))
        else:
            raise BancError(f"No actor {ident} to hide.", "edit")
        focus = ident
    elif name == "unhide":
        ident = format_hash(parse_hash(command.get("id")))
        record = actors.get(ident)
        if not record or record.get("state") != "hidden":
            raise BancError("That actor is not hidden.", "edit")
        if "actor" in record:
            record["state"] = "added"
        elif any(key in record for key in ("translate", "rotate", "scale", "gyaml", "name", "set")):
            record["state"] = "edited"
            _prune(base.get(ident), actors, ident, record)
        else:
            del actors[ident]
        focus = ident
    elif name == "revert":
        ident = format_hash(parse_hash(command.get("id")))
        if ident not in actors:
            raise BancError("That actor has no edits to revert.", "edit")
        del actors[ident]
        focus = ident if ident in base else None
    elif name == "revert_part":
        ident = format_hash(parse_hash(command.get("id")))
        record = actors.get(ident)
        if not isinstance(record, dict):
            raise BancError("That change is already gone.", "edit")
        part = str(command.get("part") or "")
        if part == "added":
            if not _is_added(record):
                raise BancError("That actor was not added by this project.", "edit")
            del actors[ident]
            focus = None
        elif part == "removed":
            if record.get("state") != "removed":
                raise BancError("That actor is not removed.", "edit")
            del actors[ident]
            focus = ident if ident in base else None
        elif part == "hidden":
            if record.get("state") != "hidden":
                raise BancError("That actor is not hidden.", "edit")
            if "actor" in record:
                record["state"] = "added"
            elif any(key in record for key in ("translate", "rotate", "scale", "gyaml", "name", "set")):
                record["state"] = "edited"
                _prune(base.get(ident), actors, ident, record)
            else:
                del actors[ident]
            focus = ident if ident in actors or ident in base else None
        elif part in ("translate", "rotate", "scale", "gyaml"):
            if part not in record:
                raise BancError("That change is already gone.", "edit")
            record.pop(part, None)
            _prune(base.get(ident), actors, ident, record)
            focus = ident
        elif part == "name":
            if "name" not in record:
                raise BancError("That change is already gone.", "edit")
            record.pop("name", None)
            _prune(base.get(ident), actors, ident, record)
            focus = ident
        elif part == "param":
            path = str(command.get("path") or "")
            if not path:
                raise BancError("Which parameter should be restored?", "edit")
            if _is_added(record):
                params = record.get("params")
                if not isinstance(params, dict) or not _pop_nested(params, path):
                    raise BancError("That parameter is not part of this actor.", "edit")
            else:
                bag = record.get("set")
                if not isinstance(bag, dict) or path not in bag:
                    raise BancError("That parameter is not part of this edit.", "edit")
                del bag[path]
                if not bag:
                    record.pop("set", None)
                _prune(base.get(ident), actors, ident, record)
            focus = ident
        else:
            raise BancError(f"Unknown change {part!r}.", "edit")
    else:
        raise BancError(f"Unknown edit {name!r}.", "edit")
    return diff, focus


def _pop_nested(root: dict, path: str) -> bool:
    parts = [part for part in path.split(".") if part]
    if not parts:
        return False
    current = root
    for part in parts[:-1]:
        nxt = current.get(part) if isinstance(current, dict) else None
        if not isinstance(nxt, dict):
            return False
        current = nxt
    if not isinstance(current, dict) or parts[-1] not in current:
        return False
    del current[parts[-1]]
    return True


def replace_diff(document: dict, banc: str, actors: dict) -> dict:
    """Replace the whole actor diff. Used by undo and by the YAML importer."""
    require_banc(document, banc)
    if not isinstance(actors, dict):
        raise BancError("A scene diff is a mapping of actor hash to edit.", "project")
    cleaned = {}
    for key, record in actors.items():
        if not isinstance(record, dict) or "state" not in record:
            raise BancError(f"Edit for {key} needs a state (edited, added, hidden or removed).", "project")
        if record["state"] not in ("edited", "added", "hidden", "removed"):
            raise BancError(f"Edit for {key} has an unknown state {record['state']!r}.", "project")
        cleaned[format_hash(parse_hash(key))] = record
    return {"format": 1, "banc": banc, "actors": cleaned}
