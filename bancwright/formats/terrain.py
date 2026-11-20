"""Field ground from TerrainArc heightmaps.

The layout was checked against the files the game ships, not against game code:

- `Banc/GameBancParam/<Level>.game__banc__GameBancParam.bgyml` names the
  terrain scene (`TerrainSceneName`) and its world origin
  (`TerrainScenePosition`). `$parent` is followed.
- `TerrainArc/<scene>.tscb` is a TSCB quadtree. World scale (metres per
  unit) is the float at `+0x10`, height scale is the float at `+0x14`, the
  tile count is the u32 at `+0x38`, and `+0x78` is a relative offset to a
  table of relative offsets, one per tile. A tile stores centre x, centre z
  and edge length as floats, and the name at `+0x60` (relative offset).
- `TerrainArc/<scene>/<tile>.hght` starts with `TARB`. The u16 at `+0x0A`
  is the header size; the rest is a zstd frame. Unpacked, `+0x2C` is a
  relative offset to the height block and `+0x30` is its size. The block is
  257 by 257 float32 heights, 0 meaning no ground, stored in the same
  block-linear order as a texture (16 GOBs per block, so log2 4). Rows run
  along z.

`+0x1C` / `+0x20` are the same kind of record for a second block. When that
block is one byte per sample and the values stay inside the MaterialAlb
array, they are used as layer indices. That reading is a guess: the public
notes only identify the height block. A tile with no usable index is drawn
in one flat earth colour.

A coarser tile does not cover ground that a finer tile also covers. Tiles
outside the placed actors, expanded by 200 m, are left out when the scene
has more tiles than that neighbourhood needs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from bancwright.formats.swizzle import deswizzle, surface_bytes
from bancwright.formats.zsio import decompress

SIZE = 257
_HEIGHT_LOG2 = 4
_UV_METRES = 8.0
_CLIP_PAD = 200.0
_MAX_LAYERS = 24


class TerrainError(ValueError):
    pass


@dataclass
class TileInfo:
    name: str
    center: tuple[float, float]
    edge: float
    path: str = ""
    status: str = "skipped"
    reason: str = ""
    triangles: int = 0
    layers: list[int] = field(default_factory=list)
    height_min: float = 0.0
    height_max: float = 0.0


@dataclass
class HeightPart:
    """One material's triangles. `layer` is None when the tile had no index."""

    layer: int | None
    positions: list[tuple[float, float, float]]
    indices: list[int]
    uvs: list[tuple[float, float]]


@dataclass
class HeightScene:
    scene: str
    source: str
    origin: tuple[float, float, float]
    param_path: str
    scale: float
    height_scale: float
    tiles: list[TileInfo]
    parts: list[HeightPart]
    notes: list[str]


def _rel(data: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(data):
        raise TerrainError(f"relative offset at {at:#x} is outside the file")
    return at + struct.unpack_from("<I", data, at)[0]


def read_tscb(data: bytes) -> tuple[float, float, list[tuple[float, float, float, str]]]:
    """World scale, height scale, and tiles (centre x, centre z, edge, name)."""
    if len(data) < 0x80 or data[:4] != b"TSCB":
        raise TerrainError("not a TSCB terrain index")
    scale, height = struct.unpack_from("<2f", data, 0x10)
    count = struct.unpack_from("<I", data, 0x38)[0]
    if count <= 0 or count > 200000:
        raise TerrainError(f"TSCB tile count {count} is not usable")
    if scale <= 0 or height == 0:
        raise TerrainError(f"TSCB scales {scale} and {height} are not usable")
    table = _rel(data, 0x78)
    tiles = []
    for index in range(count):
        slot = table + index * 4
        if slot + 4 > len(data):
            break
        at = _rel(data, slot)
        if at + 0x64 > len(data):
            continue
        center_x, center_z, edge = struct.unpack_from("<3f", data, at)
        if edge <= 0:
            continue
        name_at = _rel(data, at + 0x60)
        if name_at < 0 or name_at >= len(data):
            continue
        end = data.find(b"\0", name_at, name_at + 80)
        if end < 0:
            end = min(len(data), name_at + 80)
        name = data[name_at:end].decode("ascii", "replace").strip()
        if name:
            tiles.append((center_x, center_z, edge, name))
    if not tiles:
        raise TerrainError("TSCB has no named tiles")
    return scale, height, tiles


def unpack_hght(data: bytes, dicts) -> bytes:
    """Strip a TARB header and decompress the zstd frame. A raw payload is returned as-is."""
    if data[:4] == b"TARB":
        if len(data) < 12:
            raise TerrainError("TARB header is truncated")
        header = struct.unpack_from("<H", data, 10)[0]
        if header < 12 or header >= len(data):
            raise TerrainError(f"TARB header size {header} does not leave a zstd frame")
        data = data[header:]
    try:
        payload, _dict_id = decompress(data, dicts)
    except Exception as exc:
        raise TerrainError(f"heightmap did not decompress: {exc}") from exc
    return payload


def _block(data: bytes, at: int) -> bytes | None:
    if at + 8 > len(data):
        return None
    try:
        start = _rel(data, at)
    except TerrainError:
        return None
    size = struct.unpack_from("<I", data, at + 4)[0]
    if size <= 0 or size > 32 * 1024 * 1024 or start < 0 or start + size > len(data):
        return None
    return data[start : start + size]


def _as_floats(block: bytes) -> list[float] | None:
    tight = SIZE * SIZE * 4
    if len(block) == tight:
        return list(struct.unpack_from(f"<{SIZE * SIZE}f", block))
    need = surface_bytes(SIZE, SIZE, 1, 1, 4, _HEIGHT_LOG2)
    if len(block) < need:
        return None
    linear = deswizzle(SIZE, SIZE, 1, 1, 4, 0, _HEIGHT_LOG2, block)
    if len(linear) < tight:
        return None
    return list(struct.unpack_from(f"<{SIZE * SIZE}f", linear))


def _accept_layers(values: list[int]) -> list[int] | None:
    if len(values) < SIZE * SIZE:
        return None
    distinct = set(values[: SIZE * SIZE])
    if not distinct or len(distinct) > _MAX_LAYERS or max(distinct) > 128:
        return None
    return values[: SIZE * SIZE]


def _as_layers(block: bytes) -> list[int] | None:
    """One MaterialAlb index per sample, or None when the block does not look like one.

    The byte block beside the heights is not documented. A tight 257×257
    byte image, the same image as u16, or the byte image in block-linear
    order, is accepted when the values can be layers. Anything else is unused.
    """
    tight = SIZE * SIZE
    if len(block) == tight * 2:
        return _accept_layers(list(struct.unpack_from(f"<{tight}H", block)))
    raw = None
    if len(block) == tight:
        raw = block
    else:
        need = surface_bytes(SIZE, SIZE, 1, 1, 1, _HEIGHT_LOG2)
        if len(block) >= need:
            raw = deswizzle(SIZE, SIZE, 1, 1, 1, 0, _HEIGHT_LOG2, block)[:tight]
    if not raw or len(raw) < tight:
        return None
    return _accept_layers(list(raw[:tight]))


def read_height_file(data: bytes) -> tuple[list[float], list[int] | None, str]:
    """Heights (row-major, z then x) and an optional per-sample layer."""
    heights = _as_floats(_block(data, 0x2C) or b"")
    if not heights:
        raise TerrainError("height block is missing or not 257×257 floats")
    material = _block(data, 0x1C)
    layers = _as_layers(material) if material else None
    note = "material index from the block at +0x1C" if layers is not None else "no material index in the heightmap"
    return heights, layers, note


def _sample_step(tile_count: int) -> int:
    """Grid step. 4 is a 65×65 mesh, 8 is 33×33, 16 is 17×17."""
    if tile_count <= 24:
        return 4
    if tile_count <= 80:
        return 8
    return 16


def _indices(step: int) -> list[int]:
    found = list(range(0, SIZE, step))
    if found[-1] != SIZE - 1:
        found.append(SIZE - 1)
    return found


def level_stems(banc: str) -> list[str]:
    """Names to try for GameBancParam and TerrainArc. Static and Dynamic share a scene."""
    name = (banc or "").rsplit("/", 1)[-1]
    for suffix in (".bcett.byml.zs", ".bcett.byml"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = (banc or "").split("/")
    area = parts[1] if len(parts) >= 3 else ""
    base = name
    for suffix in ("_Static", "_Dynamic"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    stems: list[str] = []
    for text in (base, area, name):
        if text and text not in stems:
            stems.append(text)
    return stems


def _actor_clip(document) -> tuple[float, float, float, float] | None:
    if not isinstance(document, dict):
        return None
    xs: list[float] = []
    zs: list[float] = []
    for actor in document.get("Actors") or []:
        if not isinstance(actor, dict):
            continue
        translate = actor.get("Translate")
        if isinstance(translate, dict):
            x, z = translate.get("X"), translate.get("Z")
        elif isinstance(translate, (list, tuple)) and len(translate) >= 3:
            x, z = translate[0], translate[2]
        else:
            continue
        try:
            xs.append(float(x))
            zs.append(float(z))
        except (TypeError, ValueError):
            continue
    if not xs:
        return None
    return (min(xs) - _CLIP_PAD, min(zs) - _CLIP_PAD, max(xs) + _CLIP_PAD, max(zs) + _CLIP_PAD)


def _hits(box, clip) -> bool:
    if clip is None:
        return True
    x0, z0, x1, z1 = box
    return x1 >= clip[0] and x0 <= clip[2] and z1 >= clip[1] and z0 <= clip[3]


def _world_box(center_x, center_z, edge, scale, origin) -> tuple[float, float, float, float]:
    half = edge / 2
    ox, _oy, oz = origin
    return (
        (center_x - half) * scale + ox,
        (center_z - half) * scale + oz,
        (center_x + half) * scale + ox,
        (center_z + half) * scale + oz,
    )


def build_scene(
    tiles: list[tuple[float, float, float, str, list[float], list[int] | None]],
    scale: float,
    height_scale: float,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    clip: tuple[float, float, float, float] | None = None,
) -> tuple[list[HeightPart], list[TileInfo]]:
    """Mesh the tiles. Each item is centre x, centre z, edge, name, heights, layers."""
    step = _sample_step(len(tiles))
    samples = _indices(step)
    count = len(samples)
    boxes = []
    for center_x, center_z, edge, _name, _heights, _layers in tiles:
        boxes.append((center_x - edge / 2, center_z - edge / 2, center_x + edge / 2, center_z + edge / 2, edge))
    grouped: dict[int | None, list[tuple[tuple[float, float, float], tuple[float, float]]]] = {}
    grouped_index: dict[int | None, list[int]] = {}
    reports: list[TileInfo] = []
    ox, oy, oz = origin
    for center_x, center_z, edge, name, heights, layers in tiles:
        info = TileInfo(name=name, center=(center_x, center_z), edge=edge)
        present = [value for value in heights if value == value]
        if present:
            info.height_min = min(present)
            info.height_max = max(present)
        box = _world_box(center_x, center_z, edge, scale, origin)
        if not _hits(box, clip):
            info.reason = "outside the placed actors"
            reports.append(info)
            continue
        grid = []
        for row in samples:
            line = []
            for col in samples:
                value = heights[row * SIZE + col]
                line.append(0.0 if value != value or value <= 0 else value)
            grid.append(line)
        keep = []
        for row in range(count - 1):
            for col in range(count - 1):
                corners = (
                    grid[row][col],
                    grid[row][col + 1],
                    grid[row + 1][col],
                    grid[row + 1][col + 1],
                )
                if any(item <= 0 for item in corners):
                    continue
                unit_x = (center_x - edge / 2) + edge * ((samples[col] + samples[col + 1]) / 2) / (SIZE - 1)
                unit_z = (center_z - edge / 2) + edge * ((samples[row] + samples[row + 1]) / 2) / (SIZE - 1)
                covered = False
                for bx0, bz0, bx1, bz1, bedge in boxes:
                    if bedge < edge - 1e-4 and bx0 < unit_x < bx1 and bz0 < unit_z < bz1:
                        covered = True
                        break
                if not covered:
                    keep.append((row, col))
        if not keep:
            info.reason = "height is 0, or a finer tile covers it"
            reports.append(info)
            continue
        layer_cells: dict[int | None, list[tuple[int, int]]] = {}
        for row, col in keep:
            layer = None
            if layers is not None:
                layer = int(layers[samples[row] * SIZE + samples[col]])
            layer_cells.setdefault(layer, []).append((row, col))
        info.layers = sorted(item for item in layer_cells if item is not None)
        info.status = "drawn"
        for layer, cells in layer_cells.items():
            bucket = grouped.setdefault(layer, [])
            tris = grouped_index.setdefault(layer, [])
            used: dict[tuple[int, int], int] = {}

            def vert(row: int, col: int) -> int:
                key = (row, col)
                found = used.get(key)
                if found is not None:
                    return found
                sx = samples[col]
                sz = samples[row]
                unit_x = (center_x - edge / 2) + edge * sx / (SIZE - 1)
                unit_z = (center_z - edge / 2) + edge * sz / (SIZE - 1)
                position = (unit_x * scale + ox, grid[row][col] * height_scale + oy, unit_z * scale + oz)
                uv = (position[0] / _UV_METRES, position[2] / _UV_METRES)
                index = len(bucket)
                used[key] = index
                bucket.append((position, uv))
                return index

            for row, col in cells:
                a = vert(row, col)
                b = vert(row, col + 1)
                c = vert(row + 1, col)
                d = vert(row + 1, col + 1)
                # (x, z), (x, z+1), (x+1, z) then the opposite corner. The cross product points up.
                tris.extend((a, c, b, b, c, d))
            info.triangles += len(cells) * 2
        reports.append(info)
    parts = []
    for layer, verts in grouped.items():
        if not verts or not grouped_index.get(layer):
            continue
        parts.append(
            HeightPart(
                layer=layer,
                positions=[item[0] for item in verts],
                indices=grouped_index[layer],
                uvs=[item[1] for item in verts],
            )
        )
    return parts, reports
