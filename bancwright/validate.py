"""Structured diagnostics for one scene.

Each item names a file, a JSON pointer and/or an actor hash, a stable code,
a message, and a severity. The editor shows them in Properties and in the
Problems list, and asks again after every edit. Nothing here writes the
project or converts a model.
"""

from __future__ import annotations

import struct

from bancwright.actors import actor_id, format_hash, is_volume, parse_hash, view_actor
from bancwright.models import CacheMiss, describe_actor
from bancwright.project import scene_filename
from bancwright.scene import scene_view
from bancwright.values import get_path, parse_path, tag

GAMEDATA_REL = "GameData/GameDataList.Product.110.byml.zs"

_FLAG_KEYS = {"flag", "flagname", "gamedataflag", "gamedata"}


def diagnose(dump, project, banc: str) -> dict:
    """Diagnostics for `banc` and its Static/Dynamic sibling, if one is open."""
    from bancwright.scene import sibling_bancs

    flags = gamedata_names(dump)
    classes = _class_names(dump)
    known = {scene["path"] for scene in dump.list_scenes()}
    bancs = [banc, *sibling_bancs(banc, known)]
    link_ids: set[str] = set()
    prepared = []
    for path in bancs:
        try:
            document, _dict_id, _raw = dump.read_byml(path)
        except Exception:
            continue
        diff = project.load_scene_diff(path)
        view = scene_view(document, diff)
        views = list(view.get("actors") or [])
        views.extend(_recipe_views(project, path))
        prepared.append((path, document, views, diff.get("actors") or {}))
        link_ids.update(_placement_ids(views))
    rows: list[dict] = []
    if flags is None:
        rows.append(
            _issue(
                file=scene_filename(banc).as_posix(),
                pointer="",
                code="gamedata_missing",
                message=(
                    f"This dump has no {GAMEDATA_REL}, so flag names were not checked against GameDataList."
                ),
                severity="warning",
            )
        )
    if classes is None:
        rows.append(
            _issue(
                file=scene_filename(banc).as_posix(),
                pointer="",
                code="classes_unavailable",
                message="This dump has no Pack/Actor folder, so actor classes were not checked.",
                severity="warning",
            )
        )
    models: dict[str, str] = {}
    for path, document, views, edits in prepared:
        rows.extend(
            issues_for(
                file=scene_filename(path).as_posix(),
                document=document,
                actors=views,
                diff_actors=edits,
                known_classes=classes,
                flag_names=flags,
                model_of=lambda gyaml, dump=dump, models=models: _model_of(dump, gyaml, models),
                link_ids=link_ids,
                id_prefix="" if path == banc else path,
            )
        )
    rows.sort(key=lambda row: (0 if row["severity"] == "error" else 1, row["code"], row.get("actor") or "", row["message"]))
    return {"banc": banc, "diagnostics": rows}


def issues_for(
    *,
    file: str,
    document: dict,
    actors: list[dict],
    known_classes: set[str] | None,
    flag_names: set[str] | None,
    model_of,
    link_ids: set[str],
    diff_actors: dict | None = None,
    id_prefix: str = "",
) -> list[dict]:
    rows: list[dict] = []
    vanilla = {}
    for actor in document.get("Actors") or []:
        if isinstance(actor, dict) and actor_id(actor):
            vanilla[actor_id(actor)] = actor
    seen: dict[str, int] = {}
    for actor in actors:
        ident = _select_id(actor, id_prefix)
        placement = _placement_id(actor)
        if placement:
            seen[placement] = seen.get(placement, 0) + 1
        label = actor.get("name") or actor.get("gyaml") or "actor"
        pointer_base = ["actors", placement or ident or label]
        if known_classes is not None and actor.get("gyaml") not in known_classes:
            rows.append(
                _issue(
                    file=file,
                    actor=ident,
                    pointer=_pointer(pointer_base + ["gyaml"]),
                    code="unknown_class",
                    message=f"{label} uses {actor.get('gyaml') or 'an empty name'}, and that class is not in Pack/Actor.",
                    severity="error",
                )
            )
        elif not is_volume(str(actor.get("gyaml") or ""), actor.get("scale")) and model_of(actor.get("gyaml") or "") == "missing":
            rows.append(
                _issue(
                    file=file,
                    actor=ident,
                    pointer=_pointer(pointer_base + ["gyaml"]),
                    code="missing_model",
                    message=f"{label} ({actor.get('gyaml')}) has no model file in this dump.",
                    severity="warning",
                )
            )
        base = vanilla.get(placement) if placement else None
        record = (diff_actors or {}).get(placement) if placement else None
        if base is not None and isinstance(record, dict):
            rows.extend(_set_type_issues(file, ident, label, base, record, pointer_base + ["params"]))
        rows.extend(_link_issues(file, ident, label, actor.get("params"), link_ids, pointer_base + ["params"]))
        if flag_names is not None:
            rows.extend(_flag_issues(file, ident, label, actor, flag_names, pointer_base))
    for placement, count in seen.items():
        if count < 2:
            continue
        ident = f"{id_prefix}::{placement}" if id_prefix else placement
        rows.append(
            _issue(
                file=file,
                actor=ident,
                pointer=_pointer(["actors", placement]),
                code="duplicate_hash",
                message=f"{placement} is used by {count} actors in this scene. A hash has to be unique.",
                severity="error",
            )
        )
    return rows


def gamedata_names(dump) -> set[str] | None:
    """Flag and other strings from GameDataList, or None when the dump has no list."""
    path = dump.romfs / GAMEDATA_REL
    if not path.is_file():
        return None
    try:
        raw, _dict_id = dump.read_file(GAMEDATA_REL)
        return byml_strings(raw)
    except (OSError, ValueError, KeyError, struct.error):
        return None


def byml_strings(data: bytes) -> set[str]:
    """Every string in a BYML v7 string table. Little-endian YB or big-endian BY."""
    if len(data) < 16:
        raise ValueError("BYML is shorter than a header")
    if data[:2] == b"YB":
        endian = "<"
    elif data[:2] == b"BY":
        endian = ">"
    else:
        raise ValueError(f"not a BYML file (magic {data[:2]!r})")
    version, _keys_at, strings_at, _root_at = struct.unpack_from(endian + "HIII", data, 2)
    if version != 7 or strings_at == 0:
        return set()
    if strings_at + 4 > len(data) or data[strings_at] != 0xC2:
        raise ValueError("BYML string table is missing")
    order = "little" if endian == "<" else "big"
    count = int.from_bytes(data[strings_at + 1 : strings_at + 4], order)
    offsets = struct.unpack_from(endian + f"{count + 1}I", data, strings_at + 4)
    found = set()
    for index in range(count):
        start = strings_at + offsets[index]
        end = data.index(b"\0", start)
        found.add(data[start:end].decode("utf-8"))
    return found


def _class_names(dump) -> set[str] | None:
    folder = dump.romfs / "Pack" / "Actor"
    if not folder.is_dir():
        return None
    return set(dump.actor_names())


def _model_of(dump, gyaml: str, cache: dict[str, str]) -> str:
    if gyaml in cache:
        return cache[gyaml]
    try:
        info = describe_actor(dump, gyaml, convert=False)
    except CacheMiss:
        state = "present"
    else:
        reason = str(info.get("reason") or "")
        state = "missing" if info.get("status") == "box" and reason.startswith("No model") else "present"
    cache[gyaml] = state
    return state


def _recipe_views(project, banc: str) -> list[dict]:
    try:
        from bancwright.recipes import recipe_actors

        return list(recipe_actors(project, banc) or [])
    except Exception:
        return []


def _placement_ids(actors: list[dict]) -> set[str]:
    return {ident for actor in actors if (ident := _placement_id(actor))}


def _placement_id(actor: dict) -> str:
    ident = str(actor.get("placementId") or actor.get("id") or "")
    if "::" in ident:
        ident = ident.split("::", 1)[1]
    return ident


def _select_id(actor: dict, prefix: str) -> str:
    placement = _placement_id(actor)
    if prefix and placement:
        return f"{prefix}::{placement}"
    return str(actor.get("id") or placement)


def _set_type_issues(file, actor, label, vanilla_actor, record, pointer_base) -> list[dict]:
    """The patch stores a different kind than the actor in the scene file.

    Saving a bool as text, or a u32 as a string, is reported even when the
    exporter would later coerce the value back. A matching kind is quiet.
    """
    rows = []
    for path, value in (record.get("set") or {}).items():
        try:
            current = get_path(vanilla_actor, path)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        expected = tag(current)[0]
        actual = _yaml_kind(value)
        if actual is None or actual == expected:
            continue
        try:
            parts = pointer_base + [str(part) for part in parse_path(path)]
        except Exception:
            parts = pointer_base + [path]
        rows.append(
            _issue(
                file=file,
                actor=actor,
                pointer=_pointer(parts),
                code="bad_param",
                message=f"{label}: {path} is a {actual} and the scene file has a {expected}.",
                severity="error",
            )
        )
    return rows


def _yaml_kind(value) -> str | None:
    if isinstance(value, bool):
        return "bool"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "str"
    if isinstance(value, int):
        return "s32" if -(1 << 31) <= value <= (1 << 31) - 1 else "s64"
    if isinstance(value, float):
        return "f32"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict) and len(value) == 1:
        key = next(iter(value))
        mapped = {
            "$u32": "u32",
            "$s64": "s64",
            "$u64": "u64",
            "$f64": "f64",
            "$bin": "bin",
            "$h32": "h32",
            "$h64": "h64",
        }.get(key)
        if mapped:
            return mapped
    if isinstance(value, dict):
        return "dict"
    return None


def _link_issues(file, actor, label, params, link_ids, path) -> list[dict]:
    node = _node(params)
    if node is None or node[0] != "dict":
        return []
    links = _node((node[1] or {}).get("Links"))
    if links is None:
        return []
    rows = []
    if links[0] != "array":
        rows.append(
            _issue(
                file=file,
                actor=actor,
                pointer=_pointer(path + ["Links"]),
                code="bad_param",
                message=f"{label}: Links should be a list of actor references.",
                severity="error",
            )
        )
        return rows
    for index, item in enumerate(links[1] or []):
        entry = _node(item)
        item_path = path + ["Links", str(index)]
        if entry is None or entry[0] != "dict":
            rows.append(
                _issue(
                    file=file,
                    actor=actor,
                    pointer=_pointer(item_path),
                    code="bad_param",
                    message=f"{label}: link {index + 1} is not a reference.",
                    severity="error",
                )
            )
            continue
        hashed = _node((entry[1] or {}).get("Hash"))
        if hashed is None:
            rows.append(
                _issue(
                    file=file,
                    actor=actor,
                    pointer=_pointer(item_path),
                    code="missing_link",
                    message=f"{label}: link {index + 1} has no actor hash.",
                    severity="error",
                )
            )
            continue
        try:
            ident = format_hash(parse_hash(hashed[1] if hashed[0] in ("u64", "s32", "str") else None))
        except Exception:
            ident = ""
        if not ident or ident not in link_ids:
            shown = ident or "that hash"
            rows.append(
                _issue(
                    file=file,
                    actor=actor,
                    pointer=_pointer(item_path + ["Hash"]),
                    code="missing_link",
                    message=f"{label}: a link points at {shown}, and no actor in this scene has that hash.",
                    severity="error",
                )
            )
    return rows


def _flag_issues(file, actor, label, record, names: set[str], path) -> list[dict]:
    rows = []
    recipe = record.get("recipe") if isinstance(record.get("recipe"), dict) else None
    if recipe and recipe.get("flag"):
        rows.extend(_one_flag(file, actor, label, str(recipe["flag"]), names, path + ["recipe", "flag"]))
    rows.extend(_walk_flags(file, actor, label, record.get("params"), names, path + ["params"], ""))
    return rows


def _walk_flags(file, actor, label, node, names, path, key) -> list[dict]:
    parsed = _node(node)
    if parsed is None:
        return []
    kind, payload = parsed
    rows = []
    if kind == "str" and _is_flag_key(key):
        rows.extend(_one_flag(file, actor, label, str(payload), names, path))
    elif kind == "dict":
        for child, value in (payload or {}).items():
            rows.extend(_walk_flags(file, actor, label, value, names, path + [child], child))
    elif kind == "array":
        for index, value in enumerate(payload or []):
            rows.extend(_walk_flags(file, actor, label, value, names, path + [str(index)], key))
    return rows


def _one_flag(file, actor, label, name, names: set[str], path) -> list[dict]:
    if name in names:
        return []
    return [
        _issue(
            file=file,
            actor=actor,
            pointer=_pointer(path),
            code="unknown_flag",
            message=f"{label}: flag {name} is not in GameDataList.",
            severity="error",
        )
    ]


def _is_flag_key(key: str) -> bool:
    lowered = str(key or "").replace("_", "").replace("-", "").lower()
    return lowered in _FLAG_KEYS or lowered.endswith("flag")


def _node(value):
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
        return value[0], value[1]
    return None


def _pointer(parts: list) -> str:
    out = ""
    for part in parts:
        text = str(part).replace("~", "~0").replace("/", "~1")
        out += "/" + text
    return out


def _issue(*, file, code, message, severity, actor=None, pointer="") -> dict:
    return {
        "file": file,
        "pointer": pointer,
        "actor": actor,
        "code": code,
        "message": message,
        "severity": severity,
    }
