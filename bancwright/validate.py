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

# A key is a flag reference when it is a flag, a GameData name, or ends in either.
# Drop__DropActor is an actor class, so a bare "Actor" suffix is not enough.
_FLAG_KEYS = {"flag", "flags", "flagname", "gamedataflag", "gamedata", "gamedataname"}

# Effect locators and other markers never have a mesh. A pack with no
# ModelInfo is the same kind of actor: the game did not give it a model.
_NO_MODEL_PREFIXES = ("EftLoc_", "Loc_", "Tag_", "TeraTreeTag_")
_CATALOG_CACHE: dict[str, "FlagCatalog | None"] = {}


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
    link_ids.update(_folder_hashes(dump, banc, known, skip=set(bancs)))
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
    rank = {"error": 0, "warning": 1, "info": 2}
    rows.sort(key=lambda row: (rank.get(row["severity"], 3), row["code"], row.get("actor") or "", row["message"]))
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
        record = (diff_actors or {}).get(placement) if placement else None
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
        elif (
            _visual_actor(str(actor.get("gyaml") or ""), actor.get("scale"))
            and model_of(actor.get("gyaml") or "") == "missing"
        ):
            rows.append(
                _issue(
                    file=file,
                    actor=ident,
                    pointer=_pointer(pointer_base + ["gyaml"]),
                    code="missing_model",
                    message=f"{label} ({actor.get('gyaml')}) has no model file in this dump.",
                    severity="warning" if _was_edited(actor, record, "Gyaml") else "info",
                )
            )
        base = vanilla.get(placement) if placement else None
        if base is not None and isinstance(record, dict):
            rows.extend(_set_type_issues(file, ident, label, base, record, pointer_base + ["params"]))
        rows.extend(
            _link_issues(
                file,
                ident,
                label,
                actor,
                record,
                link_ids,
                pointer_base + ["params"],
                placement,
            )
        )
        if flag_names is not None:
            rows.extend(_flag_issues(file, ident, label, actor, record, flag_names, pointer_base))
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


class FlagCatalog:
    """Names from GameData string tables, and murmur3 hashes from Hash fields.

    The shipped GameDataList stores each flag as the murmur3 of its name. The
    string table has the dictionary keys, not those names, so a vanilla
    ``HavePlayedEvent.Dm_Kakariko_0050`` is known when its hash is in the file.
    A name that is written out in full still matches the string table.
    """

    def __init__(self, strings: set[str], hashes: set[int]):
        self.strings = strings
        self.hashes = hashes

    def __contains__(self, name: object) -> bool:
        text = str(name or "")
        if not text:
            return False
        if text in self.strings:
            return True
        if not self.hashes:
            return False
        return murmur3_32(text.encode("utf-8")) in self.hashes

    def __bool__(self) -> bool:
        return bool(self.strings or self.hashes)


def gamedata_names(dump) -> FlagCatalog | None:
    """Flag catalog from every GameData BYML, or None when the dump has none."""
    key = str(getattr(dump, "romfs", ""))
    if key in _CATALOG_CACHE:
        return _CATALOG_CACHE[key]
    catalog = _read_gamedata(dump)
    _CATALOG_CACHE[key] = catalog
    return catalog


def _read_gamedata(dump) -> FlagCatalog | None:
    paths = _gamedata_files(dump)
    if not paths:
        return None
    strings: set[str] = set()
    hashes: set[int] = set()
    for relative in paths:
        try:
            raw, _dict_id = dump.read_file(relative)
        except Exception:
            continue
        try:
            strings.update(byml_strings(raw))
        except (ValueError, UnicodeDecodeError, struct.error):
            pass
        try:
            hashes.update(hash_values(raw))
        except (ValueError, struct.error, IndexError):
            pass
    if not strings and not hashes:
        return None
    return FlagCatalog(strings, hashes)


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 32-bit, x86. GameData and progress.sav hash flag names with seed 0."""
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    digest = seed & 0xFFFFFFFF
    blocks = len(data) // 4
    for index in range(blocks):
        part = int.from_bytes(data[index * 4 : index * 4 + 4], "little")
        part = (part * c1) & 0xFFFFFFFF
        part = ((part << 15) | (part >> 17)) & 0xFFFFFFFF
        part = (part * c2) & 0xFFFFFFFF
        digest ^= part
        digest = ((digest << 13) | (digest >> 19)) & 0xFFFFFFFF
        digest = (digest * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[blocks * 4 :]
    part = 0
    if len(tail) >= 3:
        part ^= tail[2] << 16
    if len(tail) >= 2:
        part ^= tail[1] << 8
    if tail:
        part ^= tail[0]
        part = (part * c1) & 0xFFFFFFFF
        part = ((part << 15) | (part >> 17)) & 0xFFFFFFFF
        part = (part * c2) & 0xFFFFFFFF
        digest ^= part
    digest ^= len(data)
    digest ^= digest >> 16
    digest = (digest * 0x85EBCA6B) & 0xFFFFFFFF
    digest ^= digest >> 13
    digest = (digest * 0xC2B2AE35) & 0xFFFFFFFF
    digest ^= digest >> 16
    return digest


def hash_values(data: bytes) -> set[int]:
    """Every ``Hash`` field in a BYML v7 file. Little-endian YB or big-endian BY."""
    if len(data) < 16 or data[:2] not in (b"YB", b"BY"):
        return set()
    endian, order = ("<", "little") if data[:2] == b"YB" else (">", "big")
    version, keys_at, _strings_at, root_at = struct.unpack_from(endian + "HIII", data, 2)
    if version != 7 or not keys_at or not root_at or root_at >= len(data):
        return set()
    keys = _string_table(data, keys_at, endian, order)
    found: set[int] = set()
    _walk_hashes(data, data[root_at], root_at, endian, order, keys, found)
    return found


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


def render_markdown(report: dict) -> str:
    """A shareable list. The same text the Problems tab copies."""
    rows = list(report.get("diagnostics") or [])
    banc = report.get("banc") or "scene"
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("severity") or "info"] = counts.get(row.get("severity") or "info", 0) + 1
    summary = ", ".join(f"{counts[key]} {key}" for key in ("error", "warning", "info") if counts.get(key))
    lines = [f"# Problems for {banc}", "", summary or "No problems.", ""]
    for row in rows:
        lines.append(f"- **{row.get('severity')}** `{row.get('code')}` {row.get('message')}")
        where = " · ".join(part for part in (row.get("file"), row.get("pointer") or row.get("actor")) if part)
        if where:
            lines.append(f"  {where}")
    lines.append("")
    return "\n".join(lines)


def _gamedata_files(dump) -> list[str]:
    folder = dump.romfs / "GameData"
    found: list[str] = []
    if folder.is_dir():
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".byml.zs") or name.endswith(".byml"):
                found.append(path.relative_to(dump.romfs).as_posix())
    if GAMEDATA_REL not in found and (dump.romfs / GAMEDATA_REL).is_file():
        found.append(GAMEDATA_REL)
    return found


def _string_table(data: bytes, at: int, endian: str, order: str) -> list[str]:
    if not at or at + 4 > len(data) or data[at] != 0xC2:
        return []
    count = int.from_bytes(data[at + 1 : at + 4], order)
    offsets = struct.unpack_from(endian + f"{count + 1}I", data, at + 4)
    found = []
    for index in range(count):
        start = at + offsets[index]
        end = data.index(b"\0", start)
        found.append(data[start:end].decode("utf-8"))
    return found


def _walk_hashes(data, kind, at, endian, order, keys, found: set[int]) -> None:
    if at < 0 or at >= len(data):
        return
    if kind == 0xC1:
        count = int.from_bytes(data[at + 1 : at + 4], order)
        for index in range(count):
            pos = at + 4 + 8 * index
            if pos + 8 > len(data):
                return
            key_index = int.from_bytes(data[pos : pos + 3], order)
            child = data[pos + 3]
            raw = struct.unpack_from(endian + "I", data, pos + 4)[0]
            name = keys[key_index] if 0 <= key_index < len(keys) else ""
            if name == "Hash" and child in (0xD1, 0xD3):
                found.add(raw)
            elif child in (0xC0, 0xC1, 0x20, 0x21):
                _walk_hashes(data, child, raw, endian, order, keys, found)
    elif kind == 0xC0:
        count = int.from_bytes(data[at + 1 : at + 4], order)
        types = data[at + 4 : at + 4 + count]
        vals = at + 4 + ((count + 3) & ~3)
        for index, child in enumerate(types):
            raw = struct.unpack_from(endian + "I", data, vals + 4 * index)[0]
            if child in (0xC0, 0xC1, 0x20, 0x21):
                _walk_hashes(data, child, raw, endian, order, keys, found)
    elif kind in (0x20, 0x21):
        count = int.from_bytes(data[at + 1 : at + 4], order)
        wide = kind == 0x21
        stride = 12 if wide else 8
        types_at = at + 4 + stride * count
        for index in range(count):
            pos = at + 4 + stride * index
            raw = struct.unpack_from(endian + "I", data, pos + stride - 4)[0]
            child = data[types_at + index]
            if child in (0xC0, 0xC1, 0x20, 0x21):
                _walk_hashes(data, child, raw, endian, order, keys, found)


def _folder_hashes(dump, banc: str, known: set[str], skip: set[str]) -> set[str]:
    """Actor hashes in the other bancs of this map folder.

    A link's Dst can land in Dynamic, or in another layer that shares the
    folder, such as a night or adult placement. Those hashes count as resolved.
    """
    folder = banc.rpartition("/")[0]
    found: set[str] = set()
    for path in known:
        if path in skip or path.rpartition("/")[0] != folder:
            continue
        try:
            document, _dict_id, _raw = dump.read_byml(path)
        except Exception:
            continue
        for actor in document.get("Actors") or []:
            if isinstance(actor, dict) and (ident := actor_id(actor)):
                found.add(ident)
    return found


def _visual_actor(gyaml: str, scale) -> bool:
    if not _visual_name(gyaml):
        return False
    return not is_volume(gyaml, scale)


def _visual_name(gyaml: str) -> bool:
    name = gyaml or ""
    if name.startswith(_NO_MODEL_PREFIXES) or "Locator" in name:
        return False
    return True


def _pack_declares_model(dump, gyaml: str) -> bool:
    """True when the actor pack names a mesh. Locators have no ModelInfo."""
    if not gyaml:
        return False
    relative = f"Pack/Actor/{gyaml}.pack.zs"
    if not (dump.romfs / relative).is_file():
        return False
    try:
        from bancwright.formats.sarc import Sarc
        from bancwright.models import _modelinfo_pairs

        payload, _dict_id = dump.read_file(relative)
        archive = Sarc(payload)
    except Exception:
        return False
    if _modelinfo_pairs(archive):
        return True
    for name in archive.names():
        blob = archive.read(name)
        if name.endswith(".bfres") or (len(blob) >= 8 and blob[:8] == b"FRES    "):
            return True
    return False


def _was_edited(actor: dict, record, dotted: str) -> bool:
    if actor.get("status") in ("added", "recipe"):
        return True
    if not isinstance(record, dict):
        return False
    if record.get("state") == "added":
        return True
    for path in (record.get("set") or {}):
        text = str(path)
        if text == dotted or text.startswith(f"{dotted}.") or text.startswith(f"{dotted}["):
            return True
    if dotted == "Gyaml" and record.get("gyaml"):
        return True
    return False


def _touch_severity(actor: dict, record, dotted: str) -> str:
    """An unedited vanilla value is a warning. Editing that value makes it an error."""
    if _was_edited(actor, record, dotted):
        return "error"
    return "warning"


def _set_path(parts: list) -> str:
    if "params" in parts:
        body = parts[parts.index("params") + 1 :]
    else:
        body = [part for part in parts if part not in ("actors",)]
    text = ""
    for part in body:
        piece = str(part)
        if piece.isdigit():
            text += f"[{piece}]"
        else:
            text = f"{text}.{piece}" if text else piece
    return text


def _class_names(dump) -> set[str] | None:
    folder = dump.romfs / "Pack" / "Actor"
    if not folder.is_dir():
        return None
    return set(dump.actor_names())


def _model_of(dump, gyaml: str, cache: dict[str, str]) -> str:
    """``none`` when the actor is not a mesh, ``missing`` when its model file is absent."""
    if gyaml in cache:
        return cache[gyaml]
    if not _visual_name(gyaml) or not _pack_declares_model(dump, gyaml):
        cache[gyaml] = "none"
        return "none"
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


def _link_issues(file, actor_id_text, label, actor, record, link_ids, path, placement) -> list[dict]:
    node = _node(actor.get("params") if isinstance(actor, dict) else None)
    if node is None or node[0] != "dict":
        return []
    links = _node((node[1] or {}).get("Links"))
    if links is None:
        return []
    rows = []
    severity = _touch_severity(actor, record, "Links")
    if links[0] != "array":
        rows.append(
            _issue(
                file=file,
                actor=actor_id_text,
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
                    actor=actor_id_text,
                    pointer=_pointer(item_path),
                    code="bad_param",
                    message=f"{label}: link {index + 1} is not a reference.",
                    severity="error",
                )
            )
            continue
        fields = entry[1] or {}
        target_key, hashed = _link_target(fields)
        if hashed is None:
            rows.append(
                _issue(
                    file=file,
                    actor=actor_id_text,
                    pointer=_pointer(item_path),
                    code="missing_link",
                    message=f"{label}: link {index + 1} has no actor hash.",
                    severity=severity,
                )
            )
        else:
            ident = _hash_text(hashed)
            # A zero hash is an empty slot, not a pointer at a missing actor.
            if ident and ident != format_hash(0) and ident not in link_ids:
                rows.append(
                    _issue(
                        file=file,
                        actor=actor_id_text,
                        pointer=_pointer(item_path + [target_key]),
                        code="missing_link",
                        message=(
                            f"{label}: a link points at {ident}, and no actor in this scene "
                            "or its other layers has that hash."
                        ),
                        severity=severity,
                    )
                )
        source = _node(fields.get("Src"))
        if source is not None and placement:
            src = _hash_text(source)
            if src and src != placement:
                rows.append(
                    _issue(
                        file=file,
                        actor=actor_id_text,
                        pointer=_pointer(item_path + ["Src"]),
                        code="link_source",
                        message=f"{label}: link {index + 1} says it starts at {src}, but this actor is {placement}.",
                        severity=severity,
                    )
                )
    return rows


def _link_target(fields: dict):
    """Vanilla links store the other actor in ``Dst``. Edited links may use ``Hash``."""
    for key in ("Dst", "Hash"):
        node = _node(fields.get(key))
        if node is not None:
            return key, node
    return "", None


def _hash_text(node) -> str:
    if node is None or node[0] not in ("u64", "s32", "u32", "str"):
        return ""
    try:
        return format_hash(parse_hash(node[1]))
    except Exception:
        return ""


def _flag_issues(file, actor_id_text, label, actor, record, names, path) -> list[dict]:
    rows = []
    recipe = actor.get("recipe") if isinstance(actor.get("recipe"), dict) else None
    if recipe and recipe.get("flag"):
        rows.extend(
            _one_flag(
                file,
                actor_id_text,
                label,
                str(recipe["flag"]),
                names,
                path + ["recipe", "flag"],
                _touch_severity(actor, record, "flag"),
            )
        )
    rows.extend(_walk_flags(file, actor_id_text, label, actor, record, actor.get("params"), names, path + ["params"], ""))
    return rows


def _walk_flags(file, actor_id_text, label, actor, record, node, names, path, key) -> list[dict]:
    parsed = _node(node)
    if parsed is None:
        return []
    kind, payload = parsed
    rows = []
    if kind == "str" and _is_flag_key(key):
        rows.extend(
            _one_flag(
                file,
                actor_id_text,
                label,
                str(payload),
                names,
                path,
                _touch_severity(actor, record, _set_path(path)),
            )
        )
    elif kind == "dict":
        for child, value in (payload or {}).items():
            rows.extend(_walk_flags(file, actor_id_text, label, actor, record, value, names, path + [child], child))
    elif kind == "array":
        for index, value in enumerate(payload or []):
            rows.extend(_walk_flags(file, actor_id_text, label, actor, record, value, names, path + [str(index)], key))
    return rows


def _one_flag(file, actor, label, name, names, path, severity) -> list[dict]:
    if name in names:
        return []
    return [
        _issue(
            file=file,
            actor=actor,
            pointer=_pointer(path),
            code="unknown_flag",
            message=f"{label}: flag {name} is not in GameDataList.",
            severity=severity,
        )
    ]


def _is_flag_key(key: str) -> bool:
    lowered = str(key or "").replace("_", "").replace("-", "").lower()
    if lowered in _FLAG_KEYS or lowered.endswith("flag") or lowered.endswith("gamedataname"):
        return True
    return False


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
