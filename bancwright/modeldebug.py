"""Print how one actor model is skinned, without writing a glb.

``python -m bancwright model-debug Land_HrlCastle_Gate_A_01 --banc Banc/HyruleCastle/HyruleCastle_Dynamic.bcett.byml.zs``

``--near 27.15,108.05,-55.49 --radius 10`` lists every actor in that banc and
its Static or Dynamic sibling within the radius, with the model file the
viewer would open. That is how a mesh drawn near a label is matched to the
actor that actually owns it.

The report is the converter's own read of the BFRES: skin count, the bone
each vertex uses, that bone's parents back to the root, the raw scale /
rotation / translation and rotation flags, the world matrix after the bind
bake, and the mesh bounds before and after that bake. Pass ``--banc`` to
include the actor's placement. Without it, every scene in the dump is scanned.
"""

from __future__ import annotations

import math
from pathlib import Path

from bancwright.actors import format_hash, read_vec
from bancwright.errors import BancError
from bancwright.formats.bfres import FresError, inspect
from bancwright.formats.zsio import decompress
from bancwright.scene import sibling_bancs


def load_bytes(dump, target: str) -> tuple[str, bytes]:
    """A gyaml through the actor plan, or a bfres path on disk."""
    path = Path(target).expanduser()
    if path.is_file():
        raw = path.read_bytes()
        if raw.startswith(b"\x28\xb5\x2f\xfd"):
            raw, _dict_id = decompress(raw, dump.dicts() if dump is not None else None)
        if not raw.startswith(b"FRES"):
            raise FresError(f"{target} is not a BFRES file")
        return str(path), raw
    from bancwright.models import _actor_plan, _realize

    plan, tried = _actor_plan(dump, target)
    errors: list[str] = []
    found = list(_realize(dump, plan, errors))
    if not found:
        tried_text = ", ".join(tried[:6]) or "Pack/Actor and Model"
        raise BancError(f"No model for {target}. Tried {tried_text}.", "not_found")
    return found[0]


def debug_bytes(label: str, blob: bytes) -> dict:
    if blob[:4] == b"FRES":
        report = inspect(blob)
        report["source"] = label
        return report
    # A pack may embed the bfres. The actor plan already unwraps it.
    raise FresError(f"{label} is not a BFRES file")


def debug_actor(dump, target: str) -> dict:
    source, blob = load_bytes(dump, target)
    # The viewer draws the first file that parses. List the rest so a pack
    # with several meshes is not a surprise.
    sources = [{"source": source, **debug_bytes(source, blob)}]
    if not Path(target).expanduser().is_file():
        from bancwright.models import _actor_plan, _realize

        plan, _tried = _actor_plan(dump, target)
        errors: list[str] = []
        for label, extra in _realize(dump, plan, errors):
            if label == source:
                continue
            try:
                sources.append({"source": label, **debug_bytes(label, extra)})
            except (FresError, BancError, OSError, ValueError) as exc:
                sources.append({"source": label, "error": str(exc), "models": []})
    return {"target": target, "files": sources}


_WATCH = ("model", "phive", "link", "parent", "attach", "door", "open", "shutter", "presence", "ivy")


def parse_near(text: str) -> tuple[float, float, float]:
    parts = [item.strip() for item in text.split(",")]
    if len(parts) != 3:
        raise ValueError("--near needs x,y,z in metres, for example 27.15,108.05,-55.49")
    try:
        point = tuple(float(item) for item in parts)
    except ValueError as exc:
        raise ValueError("--near needs three numbers") from exc
    return point  # type: ignore[return-value]


def scene_files(dump, banc: str | None) -> list[str]:
    if not banc:
        return [item["path"] for item in dump.list_scenes()]
    files = [banc.replace("\\", "/")]
    available = {item["path"] for item in dump.list_scenes()}
    for extra in sibling_bancs(files[0], available):
        if extra not in files:
            files.append(extra)
    return files


def _distance(origin, point) -> float:
    return math.sqrt(sum((float(origin[axis]) - float(point[axis])) ** 2 for axis in range(3)))


def _brief(value) -> str:
    if isinstance(value, dict):
        parts = [f"{key}:{_brief(value[key])}" for key in list(value)[:6]]
        text = "{" + ", ".join(parts) + "}"
    elif isinstance(value, list):
        if value and isinstance(value[0], dict):
            text = "[" + "; ".join(_brief(item) for item in value[:4]) + "]"
        else:
            text = "[" + ", ".join(str(item) for item in value[:8]) + "]"
    else:
        text = str(value)
    return text if len(text) <= 180 else text[:177] + "..."


def _notes(actor: dict) -> list[str]:
    """Params that can change which mesh is drawn or where it sits."""
    lines = []
    for key, value in actor.items():
        if key in ("Gyaml", "Hash", "SRTHash", "Translate", "Rotate", "Scale", "Name"):
            continue
        low = str(key).lower()
        if low == "dynamic" and isinstance(value, dict):
            lines.append("Dynamic keys: " + ", ".join(str(item) for item in list(value)[:12]))
            for child, item in value.items():
                if any(token in str(child).lower() for token in _WATCH):
                    lines.append(f"Dynamic.{child}={_brief(item)}")
            continue
        if low in {"links", "phive"} or any(token in low for token in _WATCH):
            lines.append(f"{key}={_brief(value)}")
    return lines


def model_file(dump, gyaml: str, cache: dict[str, str]) -> str:
    """The first file the viewer would open for this gyaml. Packs are read once per gyaml."""
    if gyaml in cache:
        return cache[gyaml]
    from bancwright.models import _actor_plan

    try:
        plan, tried = _actor_plan(dump, gyaml)
    except BancError as exc:
        cache[gyaml] = f"(unread: {exc.message})"
        return cache[gyaml]
    chosen = ""
    for item in plan:
        if item[0] == "file" and (dump.romfs / item[1]).is_file():
            chosen = item[1]
            break
        if item[0] == "blob" and not chosen:
            chosen = item[1]
    if not chosen:
        shown = ", ".join(tried[:4]) or "Pack/Actor and Model"
        chosen = f"(none) tried {shown}"
    cache[gyaml] = chosen
    return chosen


def neighbors(dump, origin: tuple[float, float, float], radius: float, banc: str | None) -> list[dict]:
    """Actors in the banc and its sibling whose Translate is within ``radius`` metres."""
    found = []
    models: dict[str, str] = {}
    for relative in scene_files(dump, banc):
        try:
            document, _dict_id, _payload = dump.read_byml(relative)
        except BancError:
            continue
        actors = document.get("Actors") if isinstance(document, dict) else None
        if not isinstance(actors, list):
            continue
        for index, actor in enumerate(actors):
            if not isinstance(actor, dict):
                continue
            translate = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
            distance = _distance(origin, translate)
            if distance > radius:
                continue
            gyaml = str(actor.get("Gyaml") or "")
            hash_value = actor.get("Hash")
            found.append(
                {
                    "banc": relative,
                    "index": index,
                    "distance": distance,
                    "hash": format_hash(int(hash_value)) if hash_value is not None else "",
                    "name": actor.get("Name") if isinstance(actor.get("Name"), str) else "",
                    "gyaml": gyaml,
                    "translate": translate,
                    "rotate": read_vec(actor, "Rotate", [0.0, 0.0, 0.0]),
                    "scale": read_vec(actor, "Scale", [1.0, 1.0, 1.0]),
                    "model": model_file(dump, gyaml, models) if gyaml else "",
                    "notes": _notes(actor),
                }
            )
    found.sort(key=lambda row: (row["distance"], row["gyaml"], row["index"]))
    by_model: dict[str, list[str]] = {}
    for row in found:
        if row["model"].startswith("("):
            continue
        by_model.setdefault(row["model"], [])
        if row["gyaml"] not in by_model[row["model"]]:
            by_model[row["model"]].append(row["gyaml"])
    for row in found:
        others = [gyaml for gyaml in by_model.get(row["model"], []) if gyaml != row["gyaml"]]
        row["sharedWith"] = others
    return found


def render_near(origin, radius: float, rows: list[dict]) -> str:
    lines = [f"near {_fmt_vec(origin)} radius {radius:.2f}", f"actors {len(rows)}"]
    for row in rows:
        title = f"  {row['distance']:.2f} m  {row['banc']} #{row['index']} {row['hash']} {row['gyaml']}"
        if row["name"]:
            title += f"  {row['name']}"
        lines.append(title.rstrip())
        lines.append(f"    translate {_fmt_vec(row['translate'])}")
        lines.append(f"    rotate    {_fmt_vec(row['rotate'])}")
        lines.append(f"    scale     {_fmt_vec(row['scale'])}")
        lines.append(f"    model {row['model']}")
        if row["sharedWith"]:
            lines.append("    shared model with " + ", ".join(row["sharedWith"]))
        for note in row["notes"]:
            lines.append(f"    {note}")
    return "\n".join(lines) + "\n"


def placements(dump, gyaml: str, banc: str | None = None) -> list[dict]:
    if Path(gyaml).expanduser().is_file():
        return []
    if banc:
        files = [banc.replace("\\", "/")]
        available = {item["path"] for item in dump.list_scenes()}
        files.extend(sibling_bancs(files[0], available))
    else:
        files = [item["path"] for item in dump.list_scenes()]
    found = []
    for relative in files:
        try:
            document, _dict_id, _payload = dump.read_byml(relative)
        except BancError:
            continue
        actors = document.get("Actors") if isinstance(document, dict) else None
        if not isinstance(actors, list):
            continue
        for index, actor in enumerate(actors):
            if not isinstance(actor, dict) or str(actor.get("Gyaml") or "") != gyaml:
                continue
            hash_value = actor.get("Hash")
            found.append(
                {
                    "banc": relative,
                    "index": index,
                    "hash": format_hash(int(hash_value)) if hash_value is not None else "",
                    "name": actor.get("Name") if isinstance(actor.get("Name"), str) else "",
                    "translate": read_vec(actor, "Translate", [0.0, 0.0, 0.0]),
                    "rotate": read_vec(actor, "Rotate", [0.0, 0.0, 0.0]),
                    "scale": read_vec(actor, "Scale", [1.0, 1.0, 1.0]),
                }
            )
    return found


def _fmt_vec(values) -> str:
    if not values:
        return "-"
    return " ".join("-" if item is None else f"{item:.5f}" for item in values)


def _fmt_bounds(bounds) -> str:
    if not bounds:
        return "-"
    return f"{_fmt_vec(bounds.get('min'))} -> {_fmt_vec(bounds.get('max'))}"


def render_text(report: dict, placed: list[dict]) -> str:
    lines = [f"model-debug {report.get('target') or ''}"]
    if placed:
        lines.append(f"placements {len(placed)}")
        for row in placed:
            lines.append(
                f"  {row['banc']} #{row['index']} {row['hash']} {row['name']}".rstrip()
            )
            lines.append(f"    translate {_fmt_vec(row['translate'])}")
            lines.append(f"    rotate    {_fmt_vec(row['rotate'])}")
            lines.append(f"    scale     {_fmt_vec(row['scale'])}")
    elif report.get("target") and not Path(str(report.get("target"))).expanduser().is_file():
        lines.append("placements 0 (pass --banc to limit the scan)")
    for item in report.get("files") or []:
        lines.append(f"file {item.get('source')}")
        if item.get("error"):
            lines.append(f"  error {item['error']}")
        lines.append(f"  bfres version {item.get('version', '-')}")
        for model in item.get("models") or []:
            lines.append(f"  model {model.get('name') or '(unnamed)'}  bones {len(model.get('bones') or [])}  palette {len(model.get('palette') or [])}")
            for shape in model.get("shapes") or []:
                lines.append(
                    f"  shape {shape.get('name')} lod {shape.get('lod')} skin {shape.get('skinCount')} "
                    f"bone {shape.get('boneIndex')} material {shape.get('material') or '-'}"
                )
                if shape.get("reason"):
                    lines.append(f"    {shape['reason']}")
                lines.append(f"    before {_fmt_bounds(shape.get('before') or shape.get('fileBounds'))}")
                lines.append(f"    after  {_fmt_bounds(shape.get('after'))}")
                for used in shape.get("bones") or []:
                    verts = used.get("vertices")
                    lines.append(f"    uses bone {used.get('bone')} vertices {verts}")
                    for bone in used.get("chain") or []:
                        lines.append(
                            f"      [{bone['index']}] {bone.get('name') or '(unnamed)'} parent {bone.get('parent')} "
                            f"{bone.get('flagText')} rigid {bone.get('rigidIndex')}"
                        )
                        lines.append(f"        S {_fmt_vec(bone.get('scale'))}")
                        lines.append(f"        R {_fmt_vec(bone.get('rotation'))}")
                        lines.append(f"        T {_fmt_vec(bone.get('translation'))}")
                        world = bone.get("world") or []
                        for row in world:
                            lines.append(f"        | {_fmt_vec(row)}")
    return "\n".join(lines) + "\n"
