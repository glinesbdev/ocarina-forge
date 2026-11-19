"""What a project changed, per map and across the project.

Actor diffs compare the vanilla placement with the scene YAML. Advanced
patches (events, logic, schedules, text) are listed when they name the map
or an actor standing in it. Nothing here is written back to the dump.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from bancwright.actors import actor_id, read_vec
from bancwright.errors import BancError
from bancwright.project import load_yaml, scene_filename
from bancwright.values import get_path, to_yaml

COUNT_KEYS = ("added", "removed", "moved", "turned", "params", "events", "logic", "schedule", "text")
GROUP_TITLES = {
    "added": "Added",
    "removed": "Removed or hidden",
    "moved": "Moved",
    "turned": "Rotated or scaled",
    "params": "Parameters",
    "events": "Events",
    "logic": "Logic",
    "schedule": "Schedules",
    "text": "Text",
}
_PATCH_FOLDERS = ("text", "events", "gamedata", "rsdb", "tables", "ui", "scenes")
def _has_token(blob: str, name: str) -> bool:
    if len(name) < 6 or name not in blob:
        return False
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", blob) is not None

_gyaml_cache: dict = {"stamp": None, "index": {}}
_report_cache: dict = {"stamp": None, "report": None}


def scene_changes(dump, project, banc: str) -> dict:
    report = project_report(dump, project)
    for item in report["maps"]:
        if item["banc"] == banc:
            return item
    return _empty_map(banc, project)


def project_report(dump, project) -> dict:
    """Every changed map, with counts and the entries behind them."""
    stamp = (_project_stamp(project), _dump_stamp(dump) if dump is not None else None)
    if _report_cache["stamp"] == stamp and _report_cache["report"] is not None:
        return _report_cache["report"]
    report = _build_report(dump, project)
    _report_cache["stamp"] = stamp
    _report_cache["report"] = report
    return report


def _build_report(dump, project) -> dict:
    scenes = {item["path"]: item for item in dump.list_scenes()} if dump is not None else {}
    index = _gyaml_index(dump) if dump is not None else {}
    by_banc: dict[str, list[dict]] = {}

    for path in _yaml_files(project):
        data = _read(path)
        if not data:
            continue
        relative = path.relative_to(project.root).as_posix()
        if _is_actor_diff(data):
            banc = str(data.get("banc"))
            document = _document(dump, banc)
            by_banc.setdefault(banc, []).extend(_actor_entries(document, data, relative))
            continue
        file_bancs = _bancs_for(_blob(data), scenes, index)
        for entry in _patch_entries(data, relative):
            own = _bancs_for(entry["blob"], scenes, index)
            for banc in own or file_bancs:
                by_banc.setdefault(banc, []).append(_bind_patch(entry, banc, index))

    maps = []
    for banc in sorted(by_banc):
        entries = by_banc[banc]
        if not entries:
            continue
        info = scenes.get(banc) or {}
        maps.append(_map_payload(banc, project, entries, info.get("area") or _area_of(banc), info.get("name") or _name_of(banc)))
    totals = _counts([entry for item in maps for group in item["groups"] for entry in group["entries"]])
    return {"maps": maps, "totals": totals}


def _empty_map(banc: str, project) -> dict:
    return _map_payload(banc, project, [], _area_of(banc), _name_of(banc))


def _map_payload(banc: str, project, entries: list[dict], area: str, name: str) -> dict:
    for entry in entries:
        entry["banc"] = banc
    counts = _counts(entries)
    groups = []
    for kind in COUNT_KEYS:
        rows = [entry for entry in entries if entry["kind"] == kind]
        if rows:
            groups.append({"kind": kind, "title": GROUP_TITLES[kind], "entries": rows})
    file = scene_filename(banc).as_posix()
    if project is not None and not (project.root / file).is_file():
        file = ""
    return {
        "banc": banc,
        "area": area,
        "name": name,
        "file": file,
        "counts": counts,
        "groups": groups,
    }


def _counts(entries: list[dict]) -> dict:
    counts = {key: 0 for key in COUNT_KEYS}
    for entry in entries:
        kind = entry.get("kind")
        if kind in counts:
            counts[kind] += 1
    counts["total"] = sum(counts[key] for key in COUNT_KEYS)
    return counts


def _actor_entries(document: dict | None, diff: dict, relative: str) -> list[dict]:
    base = {}
    if isinstance(document, dict):
        for actor in document.get("Actors") or []:
            if isinstance(actor, dict):
                ident = actor_id(actor)
                if ident:
                    base[ident] = actor
    entries = []
    for ident, record in (diff.get("actors") or {}).items():
        if not isinstance(record, dict):
            continue
        vanilla = base.get(ident)
        label, gyaml = _who(vanilla, record)
        state = record.get("state")
        if state == "removed":
            entries.append(
                _actor_row(
                    "removed",
                    ident,
                    label,
                    gyaml,
                    _vanilla_place(vanilla, gyaml),
                    "Removed from the scene",
                    relative,
                    "removed",
                    "",
                )
            )
            continue
        if state == "hidden":
            entries.append(
                _actor_row("removed", ident, label, gyaml, "In the scene", "Hidden", relative, "hidden", "")
            )
        if state == "added" or (vanilla is None and record.get("actor")):
            if state != "hidden":
                place = _place(record.get("translate"), record.get("rotate"), record.get("scale"))
                after = gyaml if not place else f"{gyaml} at {place}"
                if record.get("name"):
                    after = f"{record['name']} · {after}"
                entries.append(_actor_row("added", ident, label, gyaml, "Not in this scene", after, relative, "added", ""))
            for path, value in _flatten(record.get("params") or {}):
                entries.append(
                    _actor_row("params", ident, label, gyaml, "not set", _brief(value), relative, "param", path, title=path)
                )
            continue
        if record.get("translate") is not None:
            before = _vec(read_vec(vanilla, "Translate", [0, 0, 0])) if vanilla else "unknown"
            entries.append(_actor_row("moved", ident, label, gyaml, before, _vec(record["translate"]), relative, "translate", ""))
        if record.get("rotate") is not None:
            before = _deg(read_vec(vanilla, "Rotate", [0, 0, 0])) if vanilla else "unknown"
            entries.append(_actor_row("turned", ident, label, gyaml, before, _deg(record["rotate"]), relative, "rotate", "", title="Rotation"))
        if record.get("scale") is not None:
            before = _vec(read_vec(vanilla, "Scale", [1, 1, 1])) if vanilla else "unknown"
            entries.append(_actor_row("turned", ident, label, gyaml, before, _vec(record["scale"]), relative, "scale", "", title="Scale"))
        if "name" in record:
            before_name = vanilla.get("Name") if vanilla and isinstance(vanilla.get("Name"), str) and vanilla.get("Name") else "no name"
            after_name = record.get("name") or "no name"
            if before_name != after_name:
                entries.append(_actor_row("params", ident, label, gyaml, before_name, after_name, relative, "name", "", title="Instance name"))
        if record.get("gyaml") and (not vanilla or record["gyaml"] != vanilla.get("Gyaml")):
            before_g = str(vanilla.get("Gyaml")) if vanilla else "unknown"
            entries.append(_actor_row("params", ident, label, gyaml, before_g, str(record["gyaml"]), relative, "gyaml", "", title="Actor"))
        for path, value in (record.get("set") or {}).items():
            before = "not set"
            if vanilla is not None:
                try:
                    before = _brief(to_yaml(get_path(vanilla, path)))
                except (KeyError, TypeError, IndexError, BancError):
                    before = "not set"
            entries.append(_actor_row("params", ident, label, gyaml, before, _brief(value), relative, "param", path, title=path))
    return entries


def _actor_row(kind, ident, label, gyaml, before, after, relative, part, path, title: str | None = None) -> dict:
    return {
        "id": f"{kind}:{ident}:{part}:{path}",
        "kind": kind,
        "actorId": ident,
        "gyaml": gyaml,
        "label": label,
        "title": title or label,
        "before": before,
        "after": after,
        "source": "actor",
        "sourceLabel": "Actor diff",
        "file": relative,
        "part": part,
        "path": path,
        "revertable": True,
    }


def _patch_entries(data: dict, relative: str) -> list[dict]:
    entries = []
    for index, edit in enumerate(data.get("edits") or []):
        if not isinstance(edit, dict) or not (edit.get("text") or edit.get("msbt")):
            continue
        label = str(edit.get("label") or edit.get("msbt") or "Text")
        before = "No such label" if edit.get("create") else "Vanilla line"
        entries.append(
            _patch_row("text", f"{relative}:text:{index}", label, before, _brief(edit.get("text") or ""), relative, _blob(edit))
        )
    for index, patch in enumerate(data.get("patches") or []):
        if not isinstance(patch, dict):
            continue
        title = Path(str(patch.get("file") or "event")).stem
        steps = [str(step.get("name")) for step in (patch.get("steps") or []) if isinstance(step, dict) and step.get("name")]
        before = str(patch.get("entry_point") or patch.get("file") or "Vanilla flow")
        after = ", ".join(steps) if steps else "Flow patched"
        entries.append(_patch_row("events", f"{relative}:event:{index}", title, before, after, relative, _blob(patch)))
    if data.get("schedule"):
        entries.append(
            _patch_row(
                "schedule",
                f"{relative}:schedule",
                str(data.get("gyaml") or "Schedule"),
                str(data["schedule"]),
                _flag_line(data, "Schedule patched"),
                relative,
                "\n".join([str(data.get("schedule")), str(data.get("gyaml") or ""), str(data.get("flag") or "")]),
            )
        )
    if data.get("logic") or data.get("door"):
        module = str(data.get("logic") or data.get("door"))
        entries.append(
            _patch_row(
                "logic",
                f"{relative}:logic",
                str(data.get("gyaml") or Path(module).name),
                module,
                _flag_line(data, "Spawn logic patched" if data.get("place") is not False else "Banc left as-is; spawn logic patched"),
                relative,
                "\n".join([module, str(data.get("gyaml") or ""), str(data.get("flag") or "")]),
            )
        )
    for index, item in enumerate(data.get("deletes") or []):
        if not isinstance(item, dict):
            continue
        instance = str(item.get("instance") or item.get("node_name") or "Actor")
        module = str(item.get("logic") or "")
        after = _flag_line(data, "Removed by logic")
        if item.get("open"):
            after = f"{after}; opens {item['open']}"
        entries.append(_patch_row("logic", f"{relative}:delete:{index}", instance, module or "Vanilla logic", after, relative, _blob(item)))
    for index, stone in enumerate(data.get("stones") or []):
        if not isinstance(stone, dict):
            continue
        gyaml = str(stone.get("gyaml") or "Actor")
        title = str(stone.get("name") or gyaml)
        after = gyaml
        if stone.get("translate"):
            after = f"{gyaml} at {_vec(stone['translate'])}"
        entries.append(
            _patch_row("logic", f"{relative}:stone:{index}", title, "Not placed in the banc", after, relative, _blob(stone), gyaml=gyaml)
        )
    reroutes = data.get("reroutes")
    if isinstance(reroutes, list) and reroutes:
        entries.append(
            _patch_row("logic", f"{relative}:reroutes", Path(relative).stem, "Vanilla links", f"{len(reroutes)} reroutes", relative, _blob(reroutes))
        )
    return entries


def _patch_row(kind, ident, title, before, after, relative, blob, gyaml: str = "") -> dict:
    return {
        "id": ident,
        "kind": kind,
        "actorId": None,
        "gyaml": gyaml,
        "label": title,
        "title": title,
        "before": before,
        "after": after,
        "source": "patch",
        "sourceLabel": "Advanced patch",
        "file": relative,
        "part": "",
        "path": "",
        "revertable": False,
        "blob": blob,
    }


def _bind_patch(entry: dict, banc: str, index: dict) -> dict:
    copied = {key: value for key, value in entry.items() if key != "blob"}
    copied["banc"] = banc
    copied["id"] = f"{entry['id']}@{banc}"
    ids = []
    names = [entry["gyaml"]] if entry.get("gyaml") else []
    for gyaml, rows in index.items():
        if _has_token(entry["blob"], gyaml):
            names.append(gyaml)
            ids.extend(row["id"] for row in rows if row["banc"] == banc and row.get("id"))
    if entry.get("gyaml"):
        ids.extend(row["id"] for row in index.get(entry["gyaml"], []) if row["banc"] == banc and row.get("id"))
    unique = list(dict.fromkeys(ids))
    if len(unique) == 1:
        copied["actorId"] = unique[0]
        if not copied.get("gyaml") and len(names) == 1:
            copied["gyaml"] = names[0]
    return copied


def _bancs_for(blob: str, scenes: dict, index: dict) -> list[str]:
    if not blob.strip():
        return []
    explicit = [path for path in scenes if path in blob]
    if explicit:
        return explicit
    found = []
    for path, scene in scenes.items():
        if _scene_mentioned(blob, scene.get("area") or "", scene.get("name") or ""):
            found.append(path)
    for gyaml, rows in index.items():
        if not _has_token(blob, gyaml):
            continue
        for row in rows:
            if row["banc"] not in found:
                found.append(row["banc"])
    return found


def _scene_mentioned(blob: str, area: str, scene_name: str) -> bool:
    if scene_name and scene_name in blob:
        return True
    stem = scene_name
    for suffix in ("_Static", "_Dynamic"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if stem and stem != area and "_" in stem and stem in blob:
        return True
    if area and (f"/{area}_" in blob or f"/{area}/" in blob):
        return True
    return False


def _gyaml_index(dump) -> dict[str, list[str]]:
    stamp = _dump_stamp(dump)
    if _gyaml_cache["stamp"] == stamp:
        return _gyaml_cache["index"]
    index: dict[str, list[str]] = {}
    for scene in dump.list_scenes():
        document = _document(dump, scene["path"])
        if not document:
            continue
        for actor in document.get("Actors") or []:
            if not isinstance(actor, dict) or not actor.get("Gyaml"):
                continue
            index.setdefault(str(actor["Gyaml"]), []).append({"banc": scene["path"], "id": actor_id(actor)})
    _gyaml_cache["stamp"] = stamp
    _gyaml_cache["index"] = index
    return index


def _document(dump, banc: str) -> dict | None:
    if dump is None:
        return None
    try:
        document, _dict_id, _raw = dump.read_byml(banc)
    except BancError:
        return None
    return document if isinstance(document, dict) else None


def _yaml_files(project) -> list[Path]:
    found = []
    for folder in _PATCH_FOLDERS:
        root = project.root / folder
        if root.is_dir():
            found.extend(sorted(path for path in root.rglob("*.yaml") if path.is_file()))
    return found


def _read(path: Path) -> dict:
    try:
        data = load_yaml(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_actor_diff(data: dict) -> bool:
    return isinstance(data.get("actors"), dict) and bool(data.get("banc"))


def _who(vanilla: dict | None, record: dict) -> tuple[str, str]:
    gyaml = str(record.get("gyaml") or record.get("actor") or (vanilla or {}).get("Gyaml") or "Actor")
    name = record.get("name") if isinstance(record.get("name"), str) and record.get("name") else None
    if name is None and vanilla is not None and isinstance(vanilla.get("Name"), str) and vanilla.get("Name"):
        name = vanilla["Name"]
    return (name or gyaml), gyaml


def _vanilla_place(vanilla: dict | None, gyaml: str) -> str:
    if vanilla is None:
        return gyaml
    return f"{gyaml} at {_vec(read_vec(vanilla, 'Translate', [0, 0, 0]))}"


def _place(translate, rotate, scale) -> str:
    bits = []
    if translate is not None:
        bits.append(_vec(translate))
    if scale is not None and list(scale) != [1, 1, 1] and list(scale) != [1.0, 1.0, 1.0]:
        bits.append(f"scale {_vec(scale)}")
    return ", ".join(bits)


def _vec(values) -> str:
    return ", ".join(_num(value) for value in list(values)[:3])


def _deg(values) -> str:
    return ", ".join(f"{_places(float(value) * 180 / math.pi, 2)}°" for value in list(values)[:3])


def _places(value, places: int) -> str:
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _num(value) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _brief(value) -> str:
    if isinstance(value, (list, tuple)) and value and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return _vec(value) if len(value) == 3 else ", ".join(_num(item) for item in value)
    if isinstance(value, dict) and len(value) == 1:
        key, item = next(iter(value.items()))
        if str(key).startswith("$"):
            return f"{key} {item}"
    text = str(value).replace("\n", " ")
    if len(text) > 140:
        text = text[:137] + "…"
    return text


def _flatten(value, prefix: str = "") -> list[tuple[str, object]]:
    if not isinstance(value, dict):
        return [(prefix, value)] if prefix else []
    if len(value) == 1 and str(next(iter(value))).startswith("$"):
        return [(prefix, value)] if prefix else []
    rows = []
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict) and item and not (len(item) == 1 and str(next(iter(item))).startswith("$")):
            rows.extend(_flatten(item, path))
        else:
            rows.append((path, item))
    return rows


def _flag_line(data: dict, fallback: str) -> str:
    flag = data.get("flag")
    if flag:
        return f"{fallback} when {flag}"
    return fallback


def _blob(value) -> str:
    parts: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for key, item in node.items():
                parts.append(str(key))
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return "\n".join(parts)


def _area_of(banc: str) -> str:
    parts = banc.split("/")
    return parts[1] if len(parts) >= 3 else ""


def _name_of(banc: str) -> str:
    name = banc.rsplit("/", 1)[-1]
    suffix = ".bcett.byml.zs"
    return name[: -len(suffix)] if name.endswith(suffix) else name


def _dump_stamp(dump) -> tuple:
    newest = 0.0
    count = 0
    banc = dump.romfs / "Banc"
    if banc.is_dir():
        for file in banc.rglob("*.bcett.byml.zs"):
            count += 1
            newest = max(newest, file.stat().st_mtime)
    return (str(dump.romfs), count, newest)


def _project_stamp(project) -> tuple:
    digest = hashlib.sha256()
    count = 0
    for path in _yaml_files(project):
        count += 1
        digest.update(path.relative_to(project.root).as_posix().encode())
        digest.update(path.read_bytes())
    return (str(project.root), count, digest.hexdigest())
