"""Shared walkable and water surface queries for ground-debug, height-diff, and snap.

Height tiles alone miss the Land_* island, cliff, soil, and hill meshes, and a
surface AABB covers holes (the Lake Hylia island) that the water mesh itself
does not. This module classifies those floors, loads their triangles (Phive
collision first, visual BFRES as a fallback; water is visual only), and samples
the highest surface under an XZ point. The viewport keeps a lighter heightmap
plus the earlier ground/floor/road cutouts, and Snap queries this sampler so
those island meshes stay correct without baking them into the visible GLB.
"""

from __future__ import annotations

import math
import re

from ocarina_forge.actors import ROLLING_ROCK_LAND, read_vec
from ocarina_forge.errors import BancError
from ocarina_forge.formats import bfres as fres
from ocarina_forge.formats.phive import read_mesh_shape
from ocarina_forge.modeldebug import scene_files
from ocarina_forge.models import _actor_plan

KOKIRI_CHILD = "Banc/KokiriForest/KokiriForest_Child_"

_SURFACE = re.compile(
    r"(?:ground|floor|road|path|terrain|rockarea|island|cliff|soil|riverbank|slope|hill)",
    re.IGNORECASE,
)
_SCENERY = re.compile(
    r"detail|scenery|interior|inside|underground|house|roof|bridge|foliage|tree|water|vine|prop|debris",
    re.IGNORECASE,
)
_WATER_SHAPE = re.compile(r"^(?:p?plane_)?(?:surface|front)(?:_|$)", re.IGNORECASE)
_SKIP_WATER_SHAPE = re.compile(r"^(?:p?plane_)?(?:back|box)(?:_|$)", re.IGNORECASE)
_SKIP_WATER_ACTOR = ("eftloc",)
_EPS = 1e-12
_BARY = 1e-7


def terrain_actor(name: str) -> bool:
    """Land_* floors, islands, cliffs, and soils that form the walkable world surface."""
    return bool(name) and name.startswith("Land_") and bool(_SURFACE.search(name)) and not _SCENERY.search(name)


def rolling_rock_map(banc: str) -> bool:
    return banc in {KOKIRI_CHILD + part + ".bcett.byml.zs" for part in ("Static", "Dynamic")}


def is_water_plane(gyaml: str) -> bool:
    text = (gyaml or "").lower()
    if not text or any(text.startswith(prefix) for prefix in _SKIP_WATER_ACTOR):
        return False
    return "waterplane" in text or "watersurface" in text


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf"))


def _euler_xyz(rx: float, ry: float, rz: float, vx: float, vy: float, vz: float) -> tuple[float, float, float]:
    """Apply extrinsic XYZ (Rz · Ry · Rx) to a vector."""
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    x1, y1, z1 = vx, vy * cx - vz * sx, vy * sx + vz * cx
    x2, y2, z2 = x1 * cy + z1 * sy, y1, -x1 * sy + z1 * cy
    return x2 * cz - y2 * sz, x2 * sz + y2 * cz, z2


def inverse_euler_xyz(rx: float, ry: float, rz: float, vx: float, vy: float, vz: float) -> tuple[float, float, float]:
    """Inverse of extrinsic XYZ: Rx^{-1} · Ry^{-1} · Rz^{-1}."""
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    x1, y1, z1 = vx * cz + vy * sz, -vx * sz + vy * cz, vz
    x2, y2, z2 = x1 * cy - z1 * sy, y1, x1 * sy + z1 * cy
    return x2, y2 * cx + z2 * sx, -y2 * sx + z2 * cx


def local_to_world(local, translate, rotate, scale) -> tuple[float, float, float]:
    sx, sy, sz = scale
    vx, vy, vz = local[0] * sx, local[1] * sy, local[2] * sz
    rx, ry, rz = rotate
    wx, wy, wz = _euler_xyz(rx, ry, rz, vx, vy, vz)
    return wx + translate[0], wy + translate[1], wz + translate[2]


def world_to_local(world, translate, rotate, scale) -> tuple[float, float, float]:
    sx, sy, sz = (s if s else 1.0 for s in scale)
    vx, vy, vz = world[0] - translate[0], world[1] - translate[1], world[2] - translate[2]
    lx, ly, lz = inverse_euler_xyz(rotate[0], rotate[1], rotate[2], vx, vy, vz)
    return lx / sx, ly / sy, lz / sz


def triangle_height(x: float, z: float, triangle) -> float | None:
    """World Y of a triangle if (x, z) is inside its XZ projection, else None."""
    ax, ay, az, bx, by, bz, cx, cy, cz = triangle
    det = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
    if abs(det) < _EPS:
        return None
    u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / det
    v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / det
    w = 1.0 - u - v
    if u < -_BARY or v < -_BARY or w < -_BARY:
        return None
    return u * ay + v * by + w * cy


def transform_triangle(triangle, translate, rotate, scale) -> tuple:
    a = local_to_world(triangle[0:3], translate, rotate, scale)
    b = local_to_world(triangle[3:6], translate, rotate, scale)
    c = local_to_world(triangle[6:9], translate, rotate, scale)
    return (*a, *b, *c)


def local_aabb(triangles) -> dict | None:
    if not triangles:
        return None
    xs, ys, zs = [], [], []
    for triangle in triangles:
        xs.extend((triangle[0], triangle[3], triangle[6]))
        ys.extend((triangle[1], triangle[4], triangle[7]))
        zs.extend((triangle[2], triangle[5], triangle[8]))
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def world_aabb(local: dict, translate, rotate, scale) -> dict | None:
    if not local:
        return None
    lo, hi = local["min"], local["max"]
    xs, ys, zs = [], [], []
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                wx, wy, wz = local_to_world((x, y, z), translate, rotate, scale)
                xs.append(wx)
                ys.append(wy)
                zs.append(wz)
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def overlaps_xz(box: dict | None, bounds: tuple[float, float, float, float] | None) -> bool:
    if not box:
        return False
    if bounds is None:
        return True
    min_x, min_z, max_x, max_z = bounds
    lo, hi = box["min"], box["max"]
    return hi[0] >= min_x and lo[0] <= max_x and hi[2] >= min_z and lo[2] <= max_z


class MeshIndex:
    """Uniform XZ grid of world-space triangles."""

    def __init__(self, step: float = 32.0):
        self.step = step if step else 32.0
        self.cells: dict[tuple[int, int], list] = {}
        self.count = 0

    def add(self, triangle, meta: dict) -> None:
        xs = (triangle[0], triangle[3], triangle[6])
        zs = (triangle[2], triangle[5], triangle[8])
        step = self.step
        x0, x1 = min(xs), max(xs)
        z0, z1 = min(zs), max(zs)
        for ix in range(int(math.floor(x0 / step)), int(math.floor(x1 / step)) + 1):
            for iz in range(int(math.floor(z0 / step)), int(math.floor(z1 / step)) + 1):
                self.cells.setdefault((ix, iz), []).append((triangle, meta))
        self.count += 1

    def sample(self, x: float, z: float) -> dict | None:
        step = self.step
        key = (int(math.floor(x / step)), int(math.floor(z / step)))
        best = None
        for triangle, meta in self.cells.get(key, ()):
            y = triangle_height(x, z, triangle)
            if y is None:
                continue
            if best is None or y > best["worldY"]:
                best = {**meta, "worldY": y}
        return best


def compose_height(heightmap_y, mesh_hit: dict | None) -> tuple[float | None, str | None, dict | None]:
    """Highest real surface: a finite heightmap sample or a mesh hit, not sentinel 0."""
    tile_y = heightmap_y if _finite(heightmap_y) and heightmap_y != 0.0 else None
    mesh_y = None if not mesh_hit else mesh_hit.get("worldY")
    mesh_y = mesh_y if _finite(mesh_y) else None
    if tile_y is None and mesh_y is None:
        return None, None, mesh_hit
    if tile_y is None:
        return mesh_y, "mesh", mesh_hit
    if mesh_y is None or tile_y > mesh_y:
        return tile_y, "heightmap", mesh_hit
    return mesh_y, "mesh", mesh_hit


def physical_triangles(dump, name: str) -> list:
    path = f"Phive/Shape/Dcc/{name}__Physical.Nin_Ounce_NVN2.bphsh"
    for suffix in (".zs", ""):
        try:
            payload, _ = dump.read_file(path + suffix)
        except (BancError, OSError):
            continue
        try:
            return [tuple(item) for item in read_mesh_shape(payload)]
        except (ValueError, OSError, TypeError):
            return []
    return []


def _actor_blob(dump, gyaml: str):
    try:
        plan, _tried, referenced = _actor_plan(dump, gyaml)
    except BancError:
        return None
    if not referenced:
        return None
    for item in plan:
        if item[0] == "file" and (dump.romfs / item[1]).is_file():
            try:
                blob, _ = dump.read_file(item[1])
            except (BancError, OSError):
                blob = None
            if blob:
                return blob
        elif item[0] == "blob" and len(item) > 2:
            return item[2]
    return None


def _keep_visual_part(name: str, *, water: bool) -> bool:
    """Water uses Front/Surface shapes, not Back, Box, or a name that only contains 'surface' as in polySurface."""
    text = name or ""
    if water:
        head = text.split("__")[0]
        if _SKIP_WATER_SHAPE.search(head):
            return False
        return bool(_WATER_SHAPE.search(head))
    return True


def visual_triangles(dump, gyaml: str, *, water: bool = False) -> list:
    blob = _actor_blob(dump, gyaml)
    if not blob:
        return []
    try:
        models = fres.parse(blob)
    except (fres.FresError, ValueError, OSError):
        return []
    triangles = []
    for model in models:
        for part in model.parts:
            if getattr(part, "omit", False) or getattr(part, "lod", 0):
                continue
            if not _keep_visual_part(getattr(part, "name", "") or "", water=water):
                continue
            positions = part.positions
            indices = part.indices or []
            if not positions or len(indices) < 3:
                continue
            for i in range(0, len(indices) - 2, 3):
                try:
                    a = positions[indices[i]]
                    b = positions[indices[i + 1]]
                    c = positions[indices[i + 2]]
                except (IndexError, TypeError):
                    continue
                triangles.append((a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]))
    return triangles


def local_floor_triangles(dump, gyaml: str, cache: dict) -> tuple[list, str]:
    key = ("floor", gyaml)
    if key in cache:
        return cache[key]
    triangles = physical_triangles(dump, gyaml)
    source = "phive"
    if not triangles:
        triangles = visual_triangles(dump, gyaml, water=False)
        source = "visual" if triangles else ""
    cache[key] = (triangles, source)
    return triangles, source


def local_water_triangles(dump, gyaml: str, cache: dict) -> list:
    key = ("water", gyaml)
    if key in cache:
        return cache[key]
    triangles = visual_triangles(dump, gyaml, water=True)
    cache[key] = triangles
    return triangles


def _iter_actors(dump, banc: str):
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
            yield relative, index, actor


def actor_translate_overlaps(translate, bounds, reach: float = 300.0) -> bool:
    """Cheap XZ reject before loading a floor mesh for a bounded query."""
    if bounds is None:
        return True
    tx, tz = translate[0], translate[2]
    return not (tx < bounds[0] - reach or tx > bounds[2] + reach or tz < bounds[1] - reach or tz > bounds[3] + reach)


def collect_terrain_surfaces(dump, banc: str, cache: dict | None = None, bounds=None) -> MeshIndex:
    """Phive-or-visual triangles for classified Land_* floors that overlap bounds."""
    cache = cache if cache is not None else {}
    index = MeshIndex()
    for relative, actor_index, actor in _iter_actors(dump, banc):
        gyaml = str(actor.get("Gyaml") or "")
        verified = rolling_rock_map(relative) and gyaml.startswith(ROLLING_ROCK_LAND)
        if not verified and not terrain_actor(gyaml):
            continue
        translate = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        if not actor_translate_overlaps(translate, bounds):
            continue
        triangles, source = local_floor_triangles(dump, gyaml, cache)
        if not triangles:
            continue
        rotate = read_vec(actor, "Rotate", [0.0, 0.0, 0.0])
        scale = read_vec(actor, "Scale", [1.0, 1.0, 1.0])
        local = local_aabb(triangles)
        if not overlaps_xz(world_aabb(local, translate, rotate, scale), bounds):
            continue
        meta = {"gyaml": gyaml, "banc": relative, "index": actor_index, "source": source or "mesh"}
        for triangle in triangles:
            index.add(transform_triangle(triangle, translate, rotate, scale), meta)
    return index


def collect_water_surfaces(dump, banc: str, cache: dict | None = None, bounds=None) -> tuple[MeshIndex, list[dict]]:
    """Front/Surface water-plane triangles (not Back or Box volumes) and actor records."""
    cache = cache if cache is not None else {}
    index = MeshIndex()
    planes = []
    for relative, actor_index, actor in _iter_actors(dump, banc):
        gyaml = str(actor.get("Gyaml") or "")
        if not is_water_plane(gyaml):
            continue
        triangles = local_water_triangles(dump, gyaml, cache)
        if not triangles:
            continue
        translate = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        rotate = read_vec(actor, "Rotate", [0.0, 0.0, 0.0])
        scale = read_vec(actor, "Scale", [1.0, 1.0, 1.0])
        local = local_aabb(triangles)
        if not overlaps_xz(world_aabb(local, translate, rotate, scale), bounds):
            continue
        meta = {"gyaml": gyaml, "banc": relative, "index": actor_index, "source": "visual"}
        for triangle in triangles:
            index.add(transform_triangle(triangle, translate, rotate, scale), meta)
        surface_y = local_to_world((0.0, local["max"][1] if local else 0.0, 0.0), translate, rotate, scale)[1]
        planes.append(
            {
                "banc": relative,
                "index": actor_index,
                "gyaml": gyaml,
                "translate": translate,
                "rotate": rotate,
                "scale": scale,
                "local": local,
                "surfaceY": surface_y,
            }
        )
    return index, planes


def query_bounds(points, pad: float = 80.0) -> tuple[float, float, float, float] | None:
    xs = [point[0] for point in points]
    zs = [point[1] for point in points]
    if not xs:
        return None
    return (min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad)