"""Compare Longshot world.ray height-check results against Forge terrain and water.

``python -m ocarina_forge height-diff results.json --dump <dump>``

Game points come from a height-check storage JSON (a ``heights`` summary plus
``heights:<area>:<chunk>`` compact row arrays). Forge ground is the composed
world surface used by ``ground-debug``: the finest height tile (a leftover
exact 0 is no height) plus Land_* island, cliff, soil, and hill meshes,
taking the highest surface under the point. Forge water uses WaterPlane
(and WaterSurface) Front/Surface mesh triangles with the actor transform
(extrinsic XYZ, ``Rz · Ry · Rx``), not the surface AABB, and the highest
real surface at that XZ wins.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from ocarina_forge.errors import BancError
from ocarina_forge.formats.terrain import TerrainError
from ocarina_forge.grounddebug import _load, _sample
from ocarina_forge.world_surface import (
    MeshIndex,
    _euler_xyz,
    collect_terrain_surfaces,
    collect_water_surfaces,
    compose_height,
    inverse_euler_xyz,
    is_water_plane,
    local_to_world,
    query_bounds,
    world_to_local,
)

WORST_N = 10
_FLAT = 0.02
_FLIP_IMPROVE = 0.6
_SEAM_EDGE = 0.04

_ROW_FIELDS = (
    "x",
    "z",
    "water_y",
    "ground_y",
    "water_kind",
    "ground_kind",
    "water_nx",
    "water_ny",
    "water_nz",
    "ground_nx",
    "ground_ny",
    "ground_nz",
    "water_hit",
    "ground_hit",
    "skipped",
)


def banc_for_map(map_name: str) -> str:
    """Static banc path for a destination or map name such as KokiriForest_Child."""
    name = (map_name or "").strip().replace("\\", "/")
    if not name:
        raise ValueError("No map name")
    if name.startswith("Banc/"):
        return name
    stem = name.split("/")[-1]
    for suffix in ("_Static", "_Dynamic"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    folder = stem
    for tag in ("_Child", "_Adult"):
        if folder.endswith(tag):
            folder = folder[: -len(tag)]
            break
    return f"Banc/{folder}/{stem}_Static.bcett.byml.zs"


def load_results(path: str | Path) -> dict:
    """Read a height-check JSON, including the storage.global wrapper."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a height-check results object")
    summary = data.get("heights") if isinstance(data.get("heights"), dict) else {}
    areas_meta = summary.get("areas") if isinstance(summary.get("areas"), dict) else {}
    areas = {}
    for area_id, meta in areas_meta.items():
        prefix = f"heights:{area_id}:"
        indexed = []
        for key, value in data.items():
            if not (isinstance(key, str) and key.startswith(prefix) and isinstance(value, dict)):
                continue
            try:
                index = int(key[len(prefix) :])
            except ValueError:
                index = len(indexed)
            indexed.append((index, value))
        rows: list = []
        fields = list(_ROW_FIELDS)
        for _index, chunk in sorted(indexed):
            fields = list(chunk.get("fields") or fields)
            rows.extend(chunk.get("rows") or [])
        points = []
        if rows:
            points = [_row_point(fields, row) for row in rows]
        elif isinstance(meta.get("points"), list):
            points = [dict(item) for item in meta["points"] if isinstance(item, dict)]
        areas[area_id] = {
            "id": area_id,
            "label": meta.get("label") or area_id,
            "map": meta.get("map") or "",
            "box": meta.get("box"),
            "done": bool(meta.get("done")),
            "points": points,
        }
    return {
        "source": str(path),
        "status": summary.get("status"),
        "hasWorldRay": summary.get("hasWorldRay"),
        "mapsSeen": list(summary.get("mapsSeen") or []),
        "notes": list(summary.get("notes") or []),
        "areas": areas,
    }


def _row_point(fields: list, row: list) -> dict:
    item = {name: row[i] if i < len(row) else None for i, name in enumerate(fields)}
    return {
        "x": float(item.get("x") or 0.0),
        "z": float(item.get("z") or 0.0),
        "water_y": item.get("water_y"),
        "ground_y": item.get("ground_y"),
        "water_hit": bool(item.get("water_hit")),
        "ground_hit": bool(item.get("ground_hit")),
        "water_kind": item.get("water_kind"),
        "ground_kind": item.get("ground_kind"),
        "skipped": item.get("skipped"),
    }


def collect_water_planes(dump, banc: str, cache: dict | None = None, bounds=None):
    """WaterPlane / WaterSurface Front/Surface meshes on a banc and its sibling.

    Returns ``(MeshIndex, plane records)``. Callers that only need to query a
    point can pass the index to ``sample_water``.
    """
    return collect_water_surfaces(dump, banc, cache, bounds=bounds)


def sample_water(planes, x: float, z: float) -> dict | None:
    """Highest water surface whose mesh contains this XZ point.

    ``planes`` is a ``MeshIndex`` or an iterable of dicts with world-space
    ``triangles``. Surface AABBs are not used.
    """
    if planes is None:
        return None
    if isinstance(planes, MeshIndex):
        return planes.sample(x, z)
    index = MeshIndex()
    for plane in planes:
        meta = {
            "gyaml": plane.get("gyaml"),
            "banc": plane.get("banc"),
            "index": plane.get("index"),
            "source": "visual",
        }
        for triangle in plane.get("triangles") or ():
            index.add(tuple(triangle), meta)
    return index.sample(x, z)

def sample_ground_at(report: dict, x: float, z: float, surfaces: MeshIndex | None = None) -> dict:
    if report.get("kind") != "heightmap":
        mesh = None if surfaces is None else surfaces.sample(x, z)
        world_y, source, mesh = compose_height(None, mesh)
        return {"worldY": world_y, "drawn": None, "nearSeam": False, "source": source, "mesh": mesh}
    sample = _sample(report, x, z)
    drawn = sample.get("drawn")
    near = False
    if drawn and drawn.get("edge"):
        unit = sample.get("unit") or [0.0, 0.0]
        for tile in report.get("tiles") or []:
            if (
                tile.get("name") == drawn.get("name")
                and tile.get("layer") == drawn.get("layer")
                and tile.get("edge") == drawn.get("edge")
            ):
                center = tile.get("center")
                edge = tile.get("edge")
                if center and edge:
                    half = edge / 2
                    x0, z0 = center[0] - half, center[1] - half
                    fx = (unit[0] - x0) / edge if edge else 0.5
                    fz = (unit[1] - z0) / edge if edge else 0.5
                    near = fx <= _SEAM_EDGE or fx >= 1 - _SEAM_EDGE or fz <= _SEAM_EDGE or fz >= 1 - _SEAM_EDGE
                break
    tile_y = None if not drawn else drawn.get("worldY")
    mesh = None if surfaces is None else surfaces.sample(x, z)
    world_y, source, mesh = compose_height(tile_y, mesh)
    return {
        "worldY": world_y,
        "drawn": drawn,
        "nearSeam": near,
        "hits": sample.get("hits") or [],
        "source": source,
        "mesh": None if not mesh else {"gyaml": mesh.get("gyaml"), "worldY": mesh.get("worldY"), "source": mesh.get("source")},
    }


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf"))


def _deltas(pairs: list[tuple[float, float]]) -> list[float]:
    return [forge - game for game, forge in pairs]


def error_stats(pairs: list[tuple[float, float]]) -> dict:
    if not pairs:
        return {"count": 0, "meanAbs": None, "medianAbs": None, "maxAbs": None, "bias": None, "std": None}
    deltas = _deltas(pairs)
    abserr = [abs(item) for item in deltas]
    return {
        "count": len(pairs),
        "meanAbs": statistics.fmean(abserr),
        "medianAbs": statistics.median(abserr),
        "maxAbs": max(abserr),
        "bias": statistics.fmean(deltas),
        "std": statistics.pstdev(deltas) if len(deltas) > 1 else 0.0,
    }


def _worst(entries: list[dict], key: str, n: int = WORST_N) -> list[dict]:
    ranked = [item for item in entries if _finite(item.get(key))]
    ranked.sort(key=lambda item: abs(item[key]), reverse=True)
    return ranked[:n]


def detect_patterns(entries: list[dict], ground_pairs: list[tuple[float, float]], resample) -> list[dict]:
    """Flag a constant Y offset, a likely axis flip or swap, and tile-seam clustering."""
    notes = []
    if ground_pairs:
        stats = error_stats(ground_pairs)
        if (
            stats["count"] >= 8
            and _finite(stats["bias"])
            and _finite(stats["std"])
            and abs(stats["bias"]) >= 0.25
            and stats["std"] <= max(0.15, 0.35 * abs(stats["bias"]))
        ):
            notes.append(
                {
                    "kind": "constant-y-offset",
                    "bias": stats["bias"],
                    "std": stats["std"],
                    "detail": f"Forge ground is about {stats['bias']:+.3f} m versus the game (std {stats['std']:.3f} m).",
                }
            )
        games = [pair[0] for pair in ground_pairs]
        forges = [pair[1] for pair in ground_pairs]
        if len(games) >= 8 and max(games) - min(games) > 1e-3:
            mean_g = statistics.fmean(games)
            mean_f = statistics.fmean(forges)
            var = sum((g - mean_g) ** 2 for g in games)
            if var > 1e-6:
                slope = sum((g - mean_g) * (f - mean_f) for g, f in zip(games, forges)) / var
                intercept = mean_f - slope * mean_g
                if abs(slope - 1.0) >= 0.05:
                    notes.append(
                        {
                            "kind": "scale",
                            "slope": slope,
                            "intercept": intercept,
                            "detail": f"Forge ground fits {slope:.4f} * gameY + {intercept:.3f}.",
                        }
                    )
    baseline = error_stats(ground_pairs).get("meanAbs")
    if baseline and baseline > 0 and resample is not None:
        variants = {
            "neg-x": lambda x, z: (-x, z),
            "neg-z": lambda x, z: (x, -z),
            "neg-xz": lambda x, z: (-x, -z),
            "swap-xz": lambda x, z: (z, x),
            "swap-neg-x": lambda x, z: (-z, x),
            "swap-neg-z": lambda x, z: (z, -x),
        }
        for name, transform in variants.items():
            pairs = []
            for item in entries:
                if not _finite(item.get("gameGround")):
                    continue
                tx, tz = transform(item["x"], item["z"])
                sampled = resample(tx, tz)
                if sampled is None or not _finite(sampled):
                    continue
                pairs.append((item["gameGround"], sampled))
            stats = error_stats(pairs)
            if stats["count"] >= 8 and stats["meanAbs"] is not None and stats["meanAbs"] <= baseline * _FLIP_IMPROVE:
                notes.append(
                    {
                        "kind": "axis-transform",
                        "transform": name,
                        "meanAbs": stats["meanAbs"],
                        "baselineMeanAbs": baseline,
                        "detail": (
                            f"Sampling Forge at {name} cuts mean abs ground error "
                            f"from {baseline:.3f} m to {stats['meanAbs']:.3f} m."
                        ),
                    }
                )
    seam = [item["groundDelta"] for item in entries if item.get("nearSeam") and _finite(item.get("groundDelta"))]
    interior = [item["groundDelta"] for item in entries if item.get("nearSeam") is False and _finite(item.get("groundDelta"))]
    if len(seam) >= 8 and len(interior) >= 8:
        seam_abs = statistics.fmean(abs(v) for v in seam)
        interior_abs = statistics.fmean(abs(v) for v in interior)
        if interior_abs > 1e-6 and seam_abs >= 1.5 * interior_abs:
            notes.append(
                {
                    "kind": "tile-seams",
                    "seamMeanAbs": seam_abs,
                    "interiorMeanAbs": interior_abs,
                    "detail": (
                        f"Points near height-tile edges have {seam_abs:.3f} m mean abs error "
                        f"vs {interior_abs:.3f} m inside a tile."
                    ),
                }
            )
    return notes


def compare_area(dump, area: dict, banc: str, water_cache: dict) -> dict:
    points = area.get("points") or []
    ground_report = _load(dump, banc)
    bounds = query_bounds([(point["x"], point["z"]) for point in points], pad=80.0)
    surfaces = collect_terrain_surfaces(dump, banc, water_cache, bounds=bounds)
    water_index, planes = collect_water_planes(dump, banc, water_cache, bounds=bounds)
    entries = []
    ground_pairs = []
    water_pairs = []
    mismatches = []
    for point in points:
        x, z = point["x"], point["z"]
        ground = sample_ground_at(ground_report, x, z, surfaces)
        water = sample_water(water_index, x, z)
        game_ground = point.get("ground_y") if point.get("ground_hit") else None
        game_water = point.get("water_y") if point.get("water_hit") else None
        forge_ground = ground.get("worldY")
        forge_water = None if not water else water.get("worldY")
        ground_delta = (forge_ground - game_ground) if _finite(game_ground) and _finite(forge_ground) else None
        water_delta = (forge_water - game_water) if _finite(game_water) and _finite(forge_water) else None
        if _finite(game_ground) and _finite(forge_ground):
            ground_pairs.append((game_ground, forge_ground))
        if _finite(game_water) and _finite(forge_water):
            water_pairs.append((game_water, forge_water))
        game_has = bool(point.get("water_hit"))
        forge_has = forge_water is not None
        if game_has != forge_has:
            mismatches.append(
                {
                    "x": x,
                    "z": z,
                    "gameWater": game_has,
                    "forgeWater": forge_has,
                    "gameWaterY": game_water,
                    "forgeWaterY": forge_water,
                    "gameGroundY": game_ground,
                    "forgeGroundY": forge_ground,
                }
            )
        drawn = ground.get("drawn") or {}
        entries.append(
            {
                "x": x,
                "z": z,
                "gameGround": game_ground,
                "forgeGround": forge_ground,
                "groundDelta": ground_delta,
                "gameWater": game_water,
                "forgeWater": forge_water,
                "waterDelta": water_delta,
                "gameHasWater": game_has,
                "forgeHasWater": forge_has,
                "nearSeam": ground.get("nearSeam"),
                "tile": drawn.get("name"),
                "groundSource": ground.get("source"),
                "groundActor": None if not ground.get("mesh") else ground["mesh"].get("gyaml"),
                "waterActor": None if not water else water.get("gyaml"),
            }
        )

    def resample(x: float, z: float):
        sampled = sample_ground_at(ground_report, x, z, surfaces).get("worldY")
        return sampled if _finite(sampled) else None

    patterns = detect_patterns(entries, ground_pairs, resample)
    readable = [tile for tile in (ground_report.get("tiles") or []) if tile.get("status") == "readable"]
    return {
        "id": area.get("id"),
        "label": area.get("label"),
        "map": area.get("map"),
        "banc": banc,
        "terrain": {
            "kind": ground_report.get("kind"),
            "source": ground_report.get("source") or "",
            "reason": ground_report.get("reason") or "",
            "origin": ground_report.get("origin"),
            "scale": ground_report.get("scale"),
            "heightScale": ground_report.get("heightScale"),
            "tiles": len(readable),
        },
        "waterPlanes": [
            {
                "gyaml": plane["gyaml"],
                "banc": plane["banc"],
                "translate": plane["translate"],
                "surfaceY": plane.get("surfaceY"),
            }
            for plane in planes
        ],
        "surfaceTriangles": surfaces.count,
        "waterTriangles": water_index.count,
        "pointCount": len(points),
        "ground": error_stats(ground_pairs),
        "water": error_stats(water_pairs),
        "waterCoverage": {
            "game": sum(1 for item in entries if item["gameHasWater"]),
            "forge": sum(1 for item in entries if item["forgeHasWater"]),
            "both": sum(1 for item in entries if item["gameHasWater"] and item["forgeHasWater"]),
            "mismatches": len(mismatches),
            "gameOnly": sum(1 for item in mismatches if item["gameWater"] and not item["forgeWater"]),
            "forgeOnly": sum(1 for item in mismatches if item["forgeWater"] and not item["gameWater"]),
        },
        "worstGround": _worst(entries, "groundDelta"),
        "worstWater": _worst(entries, "waterDelta"),
        "mismatches": mismatches[:50],
        "patterns": patterns,
        "missingForgeGround": sum(1 for item in entries if _finite(item["gameGround"]) and not _finite(item["forgeGround"])),
        "missingGameGround": sum(1 for item in entries if _finite(item["forgeGround"]) and not _finite(item["gameGround"])),
    }


def compare_results(dump, results: dict, *, banc_overrides: dict[str, str] | None = None) -> dict:
    overrides = banc_overrides or {}
    water_cache: dict = {}
    areas = []
    errors = []
    for area_id, area in (results.get("areas") or {}).items():
        banc = overrides.get(area_id) or overrides.get(area.get("map") or "") or banc_for_map(area.get("map") or "")
        try:
            areas.append(compare_area(dump, area, banc, water_cache))
        except (BancError, TerrainError, OSError, ValueError) as exc:
            message = getattr(exc, "message", None) or str(exc)
            errors.append({"area": area_id, "banc": banc, "error": message})
            areas.append({"id": area_id, "label": area.get("label"), "map": area.get("map"), "banc": banc, "error": message})
    return {
        "source": results.get("source"),
        "status": results.get("status"),
        "mapsSeen": results.get("mapsSeen") or [],
        "areas": areas,
        "errors": errors,
    }


def render_text(report: dict) -> str:
    lines = [f"height-diff {report.get('source') or ''}".rstrip()]
    if report.get("mapsSeen"):
        lines.append("maps " + ", ".join(report["mapsSeen"]))
    for area in report.get("areas") or []:
        lines.append(f"{area.get('label') or area.get('id')} on {area.get('map')} ({area.get('banc')})")
        if area.get("error"):
            lines.append(f"  error {area['error']}")
            continue
        terrain = area.get("terrain") or {}
        lines.append(
            f"  terrain {terrain.get('kind')} {terrain.get('source') or terrain.get('reason') or ''} "
            f"tiles {terrain.get('tiles')}"
        )
        ground = area.get("ground") or {}
        water = area.get("water") or {}
        lines.append(_stats_line("ground", ground, area.get("missingForgeGround") or 0))
        lines.append(_stats_line("water", water, 0))
        coverage = area.get("waterCoverage") or {}
        lines.append(
            f"  water presence game {coverage.get('game')} forge {coverage.get('forge')} both {coverage.get('both')} "
            f"mismatches {coverage.get('mismatches')} (game-only {coverage.get('gameOnly')}, forge-only {coverage.get('forgeOnly')})"
        )
        for pattern in area.get("patterns") or []:
            lines.append(f"  pattern {pattern.get('kind')}: {pattern.get('detail')}")
        if area.get("worstGround"):
            lines.append("  worst ground:")
            for item in area["worstGround"][:5]:
                lines.append(
                    f"    xz {item['x']:.3f} {item['z']:.3f}  game {item['gameGround']:.4f}  "
                    f"forge {item['forgeGround']:.4f}  d {item['groundDelta']:+.4f}"
                )
        if area.get("worstWater"):
            lines.append("  worst water:")
            for item in area["worstWater"][:5]:
                lines.append(
                    f"    xz {item['x']:.3f} {item['z']:.3f}  game {item['gameWater']:.4f}  "
                    f"forge {item['forgeWater']:.4f}  d {item['waterDelta']:+.4f}"
                )
    for item in report.get("errors") or []:
        lines.append(f"error {item.get('area')}: {item.get('error')}")
    return "\n".join(lines) + "\n"


def _stats_line(label: str, stats: dict, missing: int) -> str:
    if not stats or not stats.get("count"):
        extra = f"  missing forge {missing}" if missing else ""
        return f"  {label} no paired samples{extra}"
    return (
        f"  {label} n={stats['count']}  meanAbs {stats['meanAbs']:.4f}  medianAbs {stats['medianAbs']:.4f}  "
        f"maxAbs {stats['maxAbs']:.4f}  bias {stats['bias']:+.4f}  std {stats['std']:.4f}"
        + (f"  missing forge {missing}" if missing else "")
    )


def json_ready(value):
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    return value