"""Print the ground the view would draw for one banc, without writing a glb.

``python -m bancwright ground-debug Banc/HyruleCastle/HyruleCastle_Static.bcett.byml.zs --at 27.2,-55.5``

The report names the GameBancParam and the TSCB, the world extent of every
tile, and the height at each requested x,z. Actors whose name or gyaml
contains fairy or gossip are sampled too. A second ``--at`` can be repeated.
The Static or Dynamic sibling is included in that actor list. The tile clip
is the one the opened banc uses.
"""

from __future__ import annotations

import math

from bancwright.actors import format_hash, read_vec
from bancwright.errors import BancError
from bancwright.formats import terrain as heightmap
from bancwright.modeldebug import scene_files


def parse_at(text: str) -> tuple[float, float]:
    parts = [item.strip() for item in text.split(",")]
    if len(parts) != 2:
        raise ValueError("--at needs x,z in metres, for example 27.2,-55.5")
    try:
        x, z = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise ValueError("--at needs two numbers") from exc
    return x, z


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _interest(actor: dict) -> bool:
    gyaml = str(actor.get("Gyaml") or "")
    name = actor.get("Name") if isinstance(actor.get("Name"), str) else ""
    text = f"{gyaml} {name}".lower()
    return "fairy" in text or "gossip" in text


def _actors(dump, banc: str) -> tuple[dict, list[dict]]:
    clips = {}
    found = []
    for relative in scene_files(dump, banc):
        try:
            document, _dict_id, _payload = dump.read_byml(relative)
        except BancError:
            continue
        clips[relative] = heightmap._actor_clip(document) if isinstance(document, dict) else None
        actors = document.get("Actors") if isinstance(document, dict) else None
        if not isinstance(actors, list):
            continue
        for index, actor in enumerate(actors):
            if not isinstance(actor, dict) or not _interest(actor):
                continue
            translate = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
            hash_value = actor.get("Hash")
            found.append(
                {
                    "banc": relative,
                    "index": index,
                    "hash": format_hash(int(hash_value)) if hash_value is not None else "",
                    "gyaml": str(actor.get("Gyaml") or ""),
                    "name": actor.get("Name") if isinstance(actor.get("Name"), str) else "",
                    "translate": translate,
                }
            )
    return clips, found


def _bilinear(heights: list[float], fx: float, fz: float) -> float | None:
    size = heightmap.SIZE
    if fx < 0 or fz < 0 or fx > size - 1 or fz > size - 1:
        return None
    x0 = int(math.floor(fx))
    z0 = int(math.floor(fz))
    x1 = min(x0 + 1, size - 1)
    z1 = min(z0 + 1, size - 1)
    tx = fx - x0
    tz = fz - z0

    def at(z: int, x: int) -> float:
        return heights[z * size + x]

    samples = (at(z0, x0), at(z0, x1), at(z1, x0), at(z1, x1))
    if not all(_finite(item) for item in samples):
        return None
    h00, h10, h01, h11 = samples
    return h00 * (1 - tx) * (1 - tz) + h10 * tx * (1 - tz) + h01 * (1 - tx) * tz + h11 * tx * tz


def _load(dump, banc: str) -> dict:
    from bancwright.models import (
        _age_variant,
        _choose_tiles,
        _fit_extra_tiles,
        _locate_heightmap,
        _lookup_directories,
        _read_height_tiles,
        _terrain_plan,
        _tscb_candidates,
    )

    located = _locate_heightmap(dump, banc)
    if not located.get("tscb"):
        plan = _terrain_plan(dump, banc)
        proxy = next((item[1] for item in plan if (dump.romfs / item[1]).is_file()), "")
        return {
            "kind": "proxy" if proxy else "missing",
            "reason": located.get("missing") or ("terrain proxy " + proxy if proxy else "no terrain"),
            "param": located.get("param") or "",
            "source": proxy,
            "tiles": [],
            "overlays": [],
        }
    tscb = located["tscb"]
    raw, _dict_id = dump.read_file(tscb)
    scale, height_scale, tiles = heightmap.read_tscb(raw)
    origin = tuple(located.get("origin") or (0.0, 0.0, 0.0))
    directories = _lookup_directories(dump, located.get("scene") or "", located.get("document"), raw)
    try:
        document, _dict_id, _payload = dump.read_byml(banc)
        clip = heightmap._actor_clip(document)
    except BancError:
        clip = None
    jobs = [("main", located.get("scene") or "", [(item[0], item[1], item[2], item[3], 1.0) for item in tiles], tscb)]
    overlays = []
    for name in directories:
        if name == (located.get("scene") or ""):
            continue
        path = next((item for item in _tscb_candidates(name) if (dump.romfs / item).is_file()), "")
        if not path:
            continue
        sub_raw, _dict_id = dump.read_file(path)
        sub_scale, sub_height, sub_tiles = heightmap.read_tscb(sub_raw)
        fitted, _differs = _fit_extra_tiles(sub_tiles, scale, height_scale, sub_scale, sub_height)
        overlays.append(path)
        jobs.append(("sub", name, fitted, path))
    variant = _age_variant(banc)
    loaded = []
    catalog = []
    for layer, folder, scene_tiles, source in jobs:
        quads = [(item[0], item[1], item[2], item[3]) for item in scene_tiles]
        multipliers = {item[3]: item[4] for item in scene_tiles}
        chosen, skipped, _notes = _choose_tiles(quads, scale, origin, clip)
        for info in skipped:
            center_x, center_z, edge = info.center[0], info.center[1], info.edge
            catalog.append(
                {
                    "layer": layer,
                    "source": source,
                    "name": info.name,
                    "center": [center_x, center_z],
                    "edge": edge,
                    "box": list(heightmap._world_box(center_x, center_z, edge, scale, origin)),
                    "status": "skipped",
                    "reason": info.reason,
                    "heights": None,
                }
            )
        loaded_tiles, failed = _read_height_tiles(dump, chosen, [folder] if folder else [], multipliers, layer, variant)
        for info in failed:
            catalog.append(
                {
                    "layer": layer,
                    "source": source,
                    "name": info.get("name") or "",
                    "status": info.get("status") or "skipped",
                    "reason": info.get("reason") or "",
                    "box": None,
                    "heights": None,
                }
            )
        for item in loaded_tiles:
            center_x, center_z, edge, name, heights = item[0], item[1], item[2], item[3], item[4]
            present = [value for value in heights if _finite(value)]
            catalog.append(
                {
                    "layer": layer,
                    "source": source,
                    "name": name,
                    "center": [center_x, center_z],
                    "edge": edge,
                    "box": list(heightmap._world_box(center_x, center_z, edge, scale, origin)),
                    "status": "readable",
                    "reason": "",
                    "path": item[8].path if len(item) > 8 else "",
                    "rawMin": min(present) if present else None,
                    "rawMax": max(present) if present else None,
                    "heights": heights,
                }
            )
            loaded.append(item)
    return {
        "kind": "heightmap",
        "source": tscb,
        "param": located.get("param") or "",
        "scene": located.get("scene") or "",
        "origin": list(origin),
        "scale": scale,
        "heightScale": height_scale,
        "clip": list(clip) if clip else None,
        "overlays": overlays,
        "tiles": catalog,
        "loaded": loaded,
    }


def _sample(report: dict, x: float, z: float) -> dict:
    origin = report["origin"]
    scale = report["scale"] or 1.0
    height_scale = report["heightScale"]
    unit_x = (x - origin[0]) / scale
    unit_z = (z - origin[2]) / scale
    hits = []
    for tile in report["tiles"]:
        heights = tile.get("heights")
        center = tile.get("center")
        edge = tile.get("edge")
        if not heights or not center or not edge:
            continue
        half = edge / 2
        x0, z0 = center[0] - half, center[1] - half
        if not (x0 <= unit_x <= x0 + edge and z0 <= unit_z <= z0 + edge):
            continue
        span = heightmap.SIZE - 1
        fx = (unit_x - x0) / edge * span
        fz = (unit_z - z0) / edge * span
        raw = _bilinear(heights, fx, fz)
        world = None if raw is None else raw * height_scale + origin[1]
        hits.append(
            {
                "layer": tile["layer"],
                "name": tile["name"],
                "edge": edge,
                "path": tile.get("path") or "",
                "raw": raw,
                "worldY": world,
            }
        )
    hits.sort(key=lambda item: (item["edge"], item["layer"], item["name"]))
    drawn = next((item for item in hits if item["worldY"] is not None), None)
    return {"x": x, "z": z, "unit": [unit_x, unit_z], "hits": hits, "drawn": drawn}


def _extent(tiles: list[dict]) -> dict | None:
    boxes = [tile["box"] for tile in tiles if tile.get("box")]
    if not boxes:
        return None
    return {
        "minX": min(box[0] for box in boxes),
        "minZ": min(box[1] for box in boxes),
        "maxX": max(box[2] for box in boxes),
        "maxZ": max(box[3] for box in boxes),
    }


def _world_heights(report: dict, tiles: list[dict]) -> dict | None:
    if report.get("kind") != "heightmap":
        return None
    origin_y = report["origin"][1]
    height_scale = report["heightScale"]
    lows = []
    highs = []
    for tile in tiles:
        if tile.get("rawMin") is None:
            continue
        lows.append(tile["rawMin"] * height_scale + origin_y)
        highs.append(tile["rawMax"] * height_scale + origin_y)
    if not lows:
        return None
    return {"minY": min(lows), "maxY": max(highs)}


def inspect_ground(dump, banc: str, points: list[tuple[float, float]]) -> dict:
    clips, actors = _actors(dump, banc)
    report = _load(dump, banc)
    report.pop("loaded", None)
    queries = list(points)
    for actor in actors:
        translate = actor["translate"]
        queries.append((translate[0], translate[2]))
    samples = []
    if report.get("kind") == "heightmap":
        seen = set()
        for x, z in queries:
            key = (round(x, 4), round(z, 4))
            if key in seen:
                continue
            seen.add(key)
            sample = _sample(report, x, z)
            sample["label"] = ""
            samples.append(sample)
        for sample, actor in _label_samples(samples, actors):
            sample["label"] = actor
    for tile in report["tiles"]:
        tile.pop("heights", None)
    readable = [tile for tile in report["tiles"] if tile.get("status") == "readable"]
    report["extent"] = _extent(report["tiles"])
    report["readableExtent"] = _extent(readable)
    report["heights"] = _world_heights(report, readable)
    report["clips"] = {path: list(box) if box else None for path, box in clips.items()}
    report["actors"] = actors
    report["samples"] = samples
    report["banc"] = banc
    return report


def _label_samples(samples: list[dict], actors: list[dict]) -> list[tuple[dict, str]]:
    pairs = []
    for sample in samples:
        names = []
        for actor in actors:
            translate = actor["translate"]
            if abs(translate[0] - sample["x"]) < 1e-3 and abs(translate[2] - sample["z"]) < 1e-3:
                names.append(actor["gyaml"] or actor["name"])
        if names:
            pairs.append((sample, ", ".join(names)))
    return pairs


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_text(report: dict) -> str:
    lines = [f"ground-debug {report.get('banc') or ''}"]
    lines.append(f"kind {report.get('kind')}")
    if report.get("reason"):
        lines.append(f"reason {report['reason']}")
    lines.append(f"param {report.get('param') or '-'}")
    lines.append(f"source {report.get('source') or '-'}")
    if report.get("kind") == "heightmap":
        origin = report.get("origin") or [0, 0, 0]
        lines.append(
            f"scene {report.get('scene') or '-'}  origin {_fmt(origin[0])} {_fmt(origin[1])} {_fmt(origin[2])}  "
            f"scale {_fmt(report.get('scale'))}  heightScale {_fmt(report.get('heightScale'))}"
        )
        clip = report.get("clip")
        lines.append(
            "clip "
            + ("-" if not clip else f"x {_fmt(clip[0])}..{_fmt(clip[2])}  z {_fmt(clip[1])}..{_fmt(clip[3])}")
        )
        for path in report.get("overlays") or []:
            lines.append(f"overlay {path}")
        extent = report.get("extent") or {}
        if extent:
            lines.append(
                f"index extent x {_fmt(extent['minX'])}..{_fmt(extent['maxX'])}  z {_fmt(extent['minZ'])}..{_fmt(extent['maxZ'])}"
            )
        readable = report.get("readableExtent") or {}
        if readable:
            lines.append(
                f"drawn extent x {_fmt(readable['minX'])}..{_fmt(readable['maxX'])}  z {_fmt(readable['minZ'])}..{_fmt(readable['maxZ'])}"
            )
        heights = report.get("heights") or {}
        if heights:
            lines.append(f"readable heights y {_fmt(heights['minY'])}..{_fmt(heights['maxY'])}")
        lines.append(f"tiles {len(report.get('tiles') or [])}")
        for tile in report.get("tiles") or []:
            box = tile.get("box")
            box_text = "-" if not box else f"x {_fmt(box[0])}..{_fmt(box[2])} z {_fmt(box[1])}..{_fmt(box[3])}"
            lines.append(
                f"  {tile.get('layer')} {tile.get('status')} {tile.get('name')} edge {_fmt(tile.get('edge'))} {box_text}"
            )
            if tile.get("rawMin") is not None:
                lines.append(f"    raw {_fmt(tile['rawMin'])}..{_fmt(tile['rawMax'])}  {tile.get('path') or tile.get('source') or ''}")
            elif tile.get("reason"):
                lines.append(f"    {tile['reason']}")
    for path, clip in (report.get("clips") or {}).items():
        shown = "-" if not clip else f"x {_fmt(clip[0])}..{_fmt(clip[2])} z {_fmt(clip[1])}..{_fmt(clip[3])}"
        lines.append(f"actors {path} {shown}")
    for actor in report.get("actors") or []:
        translate = actor["translate"]
        lines.append(
            f"  {actor['banc']} #{actor['index']} {actor['hash']} {actor['gyaml']} {actor['name']}".rstrip()
        )
        lines.append(f"    at {_fmt(translate[0])} {_fmt(translate[1])} {_fmt(translate[2])}")
    lines.append(f"samples {len(report.get('samples') or [])}")
    for sample in report.get("samples") or []:
        label = f"  {sample['label']}" if sample.get("label") else ""
        lines.append(f"  xz {_fmt(sample['x'])} {_fmt(sample['z'])}{label}")
        drawn = sample.get("drawn")
        if not drawn:
            lines.append("    no height tile covers this point")
        else:
            lines.append(
                f"    drawn {drawn['layer']} {drawn['name']} edge {_fmt(drawn['edge'])}  "
                f"raw {_fmt(drawn['raw'])}  worldY {_fmt(drawn['worldY'])}"
            )
        for hit in sample.get("hits") or []:
            if drawn and hit["name"] == drawn["name"] and hit["layer"] == drawn["layer"] and hit["edge"] == drawn["edge"]:
                continue
            lines.append(
                f"    also {hit['layer']} {hit['name']} edge {_fmt(hit['edge'])} worldY {_fmt(hit['worldY'])}"
            )
    return "\n".join(lines) + "\n"
