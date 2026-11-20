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

A second scene named `<scene>_sub` (or a GameBancParam string that ends in
`_sub`) lists the same quadtree. Fine tiles often have their `.hght` only
under that directory, and the coarse tiles only under the main one. A tile
is read from whichever directory has the file. A finer tile replaces a
coarser one. Every tile that meets the placed actors, expanded by 200 m, is
meshed. There is no fixed tile cap. Tiles near that area are sampled more
finely than tiles at the edge of the margin.

The per-corner material is `<tile>.mate`, the same TARB-and-zstd wrapper as a
heightmap. After the header, `+0x2C` is a relative offset to a 4-byte texel
block and `+0x30` is its size. Retail blocks are two copies of the 257×257
block-linear surface used by heights (log2 4, 417792 bytes each). A texel is
material A, material B, then two blend bytes. 255 and 127 mean “no index”,
and 0x7f in a blend byte is neutral, so material A is used. That reading is
a guess. A tight linear grid is still accepted, and there blend at or above
128 selects material B. The block at `.hght` `+0x1C` is only a fallback when
no `.mate` decodes. `<tile>.<child|adult>.bake_ao.extm` uses the same header
and is a 1-byte or 4-byte block-linear grid. `<tile>.custom.extm` is left unused.

A coarser tile does not cover ground that a finer tile also covers.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from bancwright.formats.swizzle import deswizzle, surface_bytes
from bancwright.formats.zsio import decompress

SIZE = 257
_HEIGHT_LOG2 = 4
_UV_METRES = 8.0
_CLIP_PAD = 200.0
_MAX_LAYERS = 24
_MATE_LAYER = 89
_MATE_KEEP = 48
_NEAR_M = 80.0
_MID_M = 180.0


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
    directory: str = ""
    mate: str = ""
    mate_note: str = ""
    mate_histogram: str = ""
    ao: str = ""
    step: int = 0


@dataclass
class HeightPart:
    """One material's triangles. `layer` is None when the tile had no index."""

    layer: int | None
    positions: list[tuple[float, float, float]]
    indices: list[int]
    uvs: list[tuple[float, float]]
    colors: list[tuple[float, float, float]] | None = None


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


def embedded_sub_names(data: bytes) -> list[str]:
    """Scene names ending in `_sub` stored as ASCII inside a TSCB."""
    found: list[str] = []
    for match in re.findall(rb"[A-Za-z0-9][A-Za-z0-9_]{0,80}_sub", data or b""):
        text = match.decode("ascii", "ignore")
        if text not in found:
            found.append(text)
    return found


def _resample(values: list, width: int):
    if width == SIZE:
        return list(values[: SIZE * SIZE])
    out = []
    for z in range(SIZE):
        sz = min(width - 1, int(z * width / SIZE))
        row = sz * width
        for x in range(SIZE):
            sx = min(width - 1, int(x * width / SIZE))
            out.append(values[row + sx])
    return out


def _linear_or_swizzled(block: bytes, width: int, channels: int) -> bytes | None:
    tight = width * width * channels
    if len(block) == tight:
        return block
    planes = _swizzled_planes(block, channels, width=width)
    for plane in planes:
        if plane is not None:
            return plane
    return None


def _swizzled_planes(block: bytes, channels: int, width: int = SIZE) -> list[bytes | None]:
    """Linear images packed one after another in the block-linear surface.

    A retail mate is two 257×257 surfaces. The second is often zeros. An empty
    surface is None so it is not read as material 0.
    """
    if width != SIZE:
        return []
    tight = SIZE * SIZE * channels
    if len(block) == tight:
        return [block]
    for log2 in (_HEIGHT_LOG2, 5, 3, 2, 1, 0):
        one = surface_bytes(SIZE, SIZE, 1, 1, channels, log2)
        if one <= 0 or len(block) < one or len(block) % one != 0:
            continue
        count = len(block) // one
        if count > 4:
            continue
        planes: list[bytes | None] = []
        for index in range(count):
            piece = block[index * one : (index + 1) * one]
            if not any(piece):
                planes.append(None)
                continue
            linear = deswizzle(SIZE, SIZE, 1, 1, channels, 0, log2, piece)
            planes.append(linear[:tight] if len(linear) >= tight else None)
        if any(plane is not None for plane in planes):
            return planes
    return []


def _fmt_counts(counts: dict[int, int], limit: int = 12) -> str:
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    shown = ", ".join(f"{layer}×{count}" for layer, count in ordered[:limit])
    extra = len(ordered) - limit
    if extra > 0:
        shown += f", +{extra} more"
    return shown or "empty"


def _sentinel_mate(raw: bytes) -> bool:
    """True when the blend bytes are 0 or 0x7f, which is the retail mate pattern."""
    count = SIZE * SIZE
    if len(raw) < count * 4:
        return False
    hits = 0
    seen = 0
    for index in range(0, count, 16):
        blend = raw[index * 4 + 2]
        extra = raw[index * 4 + 3]
        seen += 1
        if blend in (0, 0x7F) and extra in (0, 0x7F):
            hits += 1
    return seen > 0 and hits / seen >= 0.9


def _read_sentinel_plane(raw: bytes) -> tuple[list[int], dict[int, int], dict[int, int]]:
    """Chosen layer per sample, plus how often each index appears in byte 0 and byte 1.

    (0, 0, 0, 0) is an empty texel, not material 0. 255 and 127 are not indices.
    When both indices are real and the blend bytes are the 0x7f sentinel, byte 0
    is the one drawn. A blend byte other than 0 or 0x7f is a 0..255 weight, and
    128 or more selects byte 1. That weight rule is a guess.
    """
    chosen = []
    seen0: dict[int, int] = {}
    seen1: dict[int, int] = {}
    for index in range(SIZE * SIZE):
        b0, b1, b2, b3 = raw[index * 4 : index * 4 + 4]
        if (b0, b1, b2, b3) == (0, 0, 0, 0):
            chosen.append(-1)
            continue
        first = b0 if b0 <= _MATE_LAYER else None
        second = b1 if b1 <= _MATE_LAYER else None
        if first is not None:
            seen0[first] = seen0.get(first, 0) + 1
        if second is not None:
            seen1[second] = seen1.get(second, 0) + 1
        weight = next((item for item in (b2, b3) if item not in (0, 0x7F)), None)
        if first is not None and second is not None and weight is not None and weight >= 128:
            chosen.append(second)
        elif first is not None:
            chosen.append(first)
        elif second is not None:
            chosen.append(second)
        else:
            chosen.append(-1)
    return chosen, seen0, seen1


def _decode_retail_planes(planes: list[bytes | None]) -> tuple[list[int], str] | None:
    decoded: list[list[int] | None] = []
    parts = []
    for number, plane in enumerate(planes, start=1):
        if plane is None or not _sentinel_mate(plane):
            decoded.append(None)
            continue
        chosen, seen0, seen1 = _read_sentinel_plane(plane)
        if not seen0 and not seen1:
            decoded.append(None)
            continue
        decoded.append(chosen)
        text = f"image {number} b0 {_fmt_counts(seen0)}, b1 {_fmt_counts(seen1)}"
        parts.append(text.replace("; ", ", "))
    if not any(item is not None for item in decoded):
        if any(plane is not None and _sentinel_mate(plane) for plane in planes):
            return [-1] * (SIZE * SIZE), "hist empty; 257×257×4 block-linear, indices were 255 or 127 (a guess)"
        return None
    merged = []
    for index in range(SIZE * SIZE):
        layer = -1
        for plane in decoded:
            if plane is not None and plane[index] >= 0:
                layer = plane[index]
                break
        merged.append(layer)
    merged, kept, dropped = _cap_mate(merged)
    note = (
        "hist "
        + " | ".join(parts)
        + f"; 257×257×4 block-linear, {len(planes)} image(s), "
        + "255 and 127 mean no material, 0x7f blend keeps byte 0 (a guess)"
    )
    if dropped:
        note += f", {dropped} rarer materials left as the earth colour"
    if not kept and not any(item >= 0 for item in merged):
        note = "hist empty; 257×257×4 block-linear decoded with no material index (a guess)"
    return merged, note


def _cap_mate(chosen: list[int]) -> tuple[list[int], list[int], int]:
    counts: dict[int, int] = {}
    for value in chosen:
        if value >= 0:
            counts[value] = counts.get(value, 0) + 1
    if len(counts) <= _MATE_KEEP:
        return chosen, sorted(counts), 0
    keep = {item for item, _count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:_MATE_KEEP]}
    capped = [value if value in keep else -1 for value in chosen]
    return capped, sorted(keep), len(counts) - len(keep)


def _blend_grid(raw: bytes, width: int) -> tuple[list[int], str] | None:
    count = width * width
    if len(raw) < count * 4:
        return None
    chosen = []
    usable = 0
    for index in range(count):
        m0, m1, blend, _extra = raw[index * 4 : index * 4 + 4]
        layer = None
        if m0 <= _MATE_LAYER and m1 <= _MATE_LAYER:
            layer = m1 if blend >= 128 else m0
        elif m0 <= _MATE_LAYER:
            layer = m0
        elif m1 <= _MATE_LAYER:
            layer = m1
        if layer is not None:
            usable += 1
            chosen.append(layer)
        else:
            chosen.append(-1)
    if usable * 2 < count:
        return None
    layers, kept, dropped = _cap_mate(_resample(chosen, width))
    note = (
        f"mate {width}×{width} blend records (material, material, blend, extra; "
        f"blend >= 128 uses the second index; this layout is a guess), layers {', '.join(str(item) for item in kept) or 'none'}"
    )
    if dropped:
        note += f", {dropped} rarer materials left as the earth colour"
    return layers, note


def _index_grid(raw: bytes, width: int, kind: str) -> tuple[list[int], str] | None:
    count = width * width
    if kind == "u16":
        if len(raw) < count * 2:
            return None
        values = list(struct.unpack_from(f"<{count}H", raw))
    else:
        if len(raw) < count:
            return None
        values = list(raw[:count])
    usable = [item for item in values if item <= _MATE_LAYER]
    if len(usable) * 2 < count:
        return None
    chosen = [item if item <= _MATE_LAYER else -1 for item in values]
    layers, kept, dropped = _cap_mate(_resample(chosen, width))
    if not kept:
        return None
    note = f"mate {width}×{width} {kind} indices, layers {', '.join(str(item) for item in kept)}"
    if dropped:
        note += f", {dropped} rarer materials left as the earth colour"
    return layers, note


def _parse_mate_block(block: bytes) -> tuple[list[int], str] | None:
    planes = _swizzled_planes(block, 4)
    if planes:
        retail = _decode_retail_planes(planes)
        if retail:
            return retail
        raw = next((plane for plane in planes if plane is not None), None)
        if raw is not None:
            parsed = _blend_grid(raw, SIZE)
            if parsed:
                layers, note = parsed
                if len(block) != SIZE * SIZE * 4:
                    note += ", block-linear"
                return layers, note
    for width in (SIZE, 256):
        raw = _linear_or_swizzled(block, width, 4)
        if raw is not None and not _sentinel_mate(raw):
            parsed = _blend_grid(raw, width)
            if parsed:
                swizzle = "" if len(block) == width * width * 4 else ", block-linear"
                layers, note = parsed
                return layers, note + swizzle
        raw = _linear_or_swizzled(block, width, 1)
        if raw is not None:
            parsed = _index_grid(raw, width, "u8")
            if parsed:
                return parsed
        raw = _linear_or_swizzled(block, width, 2)
        if raw is not None:
            parsed = _index_grid(raw, width, "u16")
            if parsed:
                return parsed
    return None


def split_histogram(note: str) -> tuple[str, str]:
    """The `hist ...` clause, and the rest of the mate note."""
    if note.startswith("hist ") and "; " in note:
        first, rest = note.split("; ", 1)
        return first[len("hist ") :], rest
    return "", note


def read_mate(data: bytes, dicts=None) -> tuple[list[int] | None, str]:
    """Per-sample MaterialAlb index, or None when the file is not a material grid.

    A negative index means that sample had no usable layer and should use the earth colour.
    """
    try:
        payload = unpack_hght(data, dicts)
    except TerrainError as exc:
        return None, str(exc)
    parsed = _parse_mate_block(payload)
    if parsed:
        layers, note = parsed
        return layers, f"{note} (payload)"
    seen = []
    for at, label in ((0x2C, "+0x2C"), (0x1C, "+0x1C")):
        block = _block(payload, at)
        if not block:
            continue
        seen.append(f"{label} {len(block)} bytes")
        parsed = _parse_mate_block(block)
        if parsed:
            layers, note = parsed
            format_byte = payload[4] if len(payload) > 4 else None
            if format_byte is not None:
                note += f", format byte {format_byte:#04x}"
            return layers, f"{note} ({label})"
    where = ", ".join(seen) if seen else "no image block"
    return None, f"mate did not contain a material grid ({where})"


def _occlusion_grid(raw: bytes, width: int) -> list[float] | None:
    count = width * width
    if len(raw) < count:
        return None
    values = [raw[index] / 255.0 for index in range(count)]
    return _resample(values, width)


def _occlusion_varies(values: list[float]) -> bool:
    sample = values[::73] or values
    mean = sum(sample) / len(sample)
    variance = sum((item - mean) ** 2 for item in sample) / len(sample)
    return 0.15 <= mean <= 0.98 and variance >= 0.0004


def _occlusion_from_plane(plane: bytes, channels: int) -> list[float] | None:
    count = SIZE * SIZE
    if channels == 1:
        if len(plane) < count:
            return None
        return [plane[index] / 255.0 for index in range(count)]
    if len(plane) < count * 4:
        return None
    values = []
    for index in range(count):
        b0, b1, b2, b3 = plane[index * 4 : index * 4 + 4]
        if (b0, b1, b2, b3) == (0, 0, 0, 0):
            values.append(1.0)
            continue
        if b1 in (0, 0x7F) and b2 in (0, 0x7F) and b3 in (0, 0x7F):
            values.append(b0 / 255.0)
            continue
        parts = [item for item in (b0, b1, b2, b3) if item != 0x7F]
        shade = sum(parts) / len(parts) if parts else b0
        values.append(shade / 255.0)
    return values


def read_occlusion(data: bytes, dicts=None) -> tuple[list[float] | None, str]:
    """0..1 occlusion per height sample, 1 meaning open ground. None when the file is not that grid."""
    try:
        payload = unpack_hght(data, dicts)
    except TerrainError as exc:
        return None, str(exc)
    format_byte = payload[4] if len(payload) > 0x30 else None
    blocks = []
    for at in (0x2C, 0x1C):
        block = _block(payload, at)
        if block:
            blocks.append(block)
    if not blocks:
        blocks.append(payload)
    order = (4, 1) if format_byte == 0x06 else (1, 4)
    flat_note = ""
    for block in blocks:
        for channels in order:
            planes = _swizzled_planes(block, channels)
            if not planes and channels == 1:
                for width in (SIZE, 256, 128, 64):
                    raw = _linear_or_swizzled(block, width, 1)
                    if raw is None:
                        continue
                    values = _occlusion_grid(raw, width)
                    if values and _occlusion_varies(values):
                        return values, f"bake_ao {width}×{width} applied as vertex shade"
                    if values:
                        flat_note = f"bake_ao {width}×{width} was flat or not an occlusion range, so it was not applied"
            for plane in planes:
                if plane is None:
                    continue
                values = _occlusion_from_plane(plane, channels)
                if values and _occlusion_varies(values):
                    kind = "4-byte" if channels == 4 else "1-byte"
                    shown = f", format byte {format_byte:#04x}" if format_byte is not None else ""
                    return values, f"bake_ao 257×257 {kind} block-linear applied as vertex shade{shown}"
                if values:
                    flat_note = "bake_ao decoded but it was flat or outside an occlusion range, so it was not applied"
    return None, flat_note or "bake_ao is not a single-channel occlusion grid"


def sample_steps(tiles, scale: float, origin, focus: tuple[float, float]) -> tuple[dict[str, int], str]:
    """Finer samples near the actors. More than 40 nearby tiles drop from step 4 to step 8."""
    focus_x, focus_z = focus
    distances = []
    near = 0
    for center_x, center_z, edge, name in tiles:
        box = _world_box(center_x, center_z, edge, scale, origin)
        dx = (box[0] + box[2]) / 2 - focus_x
        dz = (box[1] + box[3]) / 2 - focus_z
        distance = (dx * dx + dz * dz) ** 0.5
        distances.append((name, distance))
        if distance <= _NEAR_M:
            near += 1
    near_step = 8 if near > 40 else 4
    steps: dict[str, int] = {}
    used: set[int] = set()
    for name, distance in distances:
        if distance <= _NEAR_M:
            step = near_step
        elif distance <= _MID_M:
            step = 8
        else:
            step = 16
        steps[name] = step
        used.add(step)
    if not used:
        return steps, "height samples step 16"
    note = f"height samples step {min(used)} near the actors"
    if len(used) > 1:
        note += f" and step {max(used)} at the edge of the margin"
    if near > 40:
        note += f" ({near} tiles within {_NEAR_M:.0f} m, so nearby tiles use step {near_step})"
    return steps, note


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
    tiles: list[tuple],
    scale: float,
    height_scale: float,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    clip: tuple[float, float, float, float] | None = None,
    steps: dict[str, int] | None = None,
) -> tuple[list[HeightPart], list[TileInfo]]:
    """Mesh the tiles. Each item is centre x, centre z, edge, name, heights, layers, and optional occlusion."""
    default_step = _sample_step(len(tiles))
    boxes = []
    for tile in tiles:
        center_x, center_z, edge = tile[0], tile[1], tile[2]
        boxes.append((center_x - edge / 2, center_z - edge / 2, center_x + edge / 2, center_z + edge / 2, edge))
    grouped: dict[int | None, list[tuple[tuple[float, float, float], tuple[float, float], tuple[float, float, float]]]] = {}
    grouped_index: dict[int | None, list[int]] = {}
    reports: list[TileInfo] = []
    ox, oy, oz = origin
    for tile in tiles:
        center_x, center_z, edge, name, heights, layers = tile[:6]
        occlusion = tile[6] if len(tile) > 6 else None
        step = int((steps or {}).get(name) or default_step)
        samples = _indices(step)
        count = len(samples)
        info = TileInfo(name=name, center=(center_x, center_z), edge=edge, step=step)
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
                raw = int(layers[samples[row] * SIZE + samples[col]])
                if raw >= 0:
                    layer = raw
            layer_cells.setdefault(layer, []).append((row, col))
        info.layers = sorted(item for item in layer_cells if item is not None)
        info.status = "drawn"
        shade = occlusion if occlusion and len(occlusion) >= SIZE * SIZE else None
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
                amount = shade[sz * SIZE + sx] if shade is not None else 1.0
                color = (amount, amount, amount)
                index = len(bucket)
                used[key] = index
                bucket.append((position, uv, color))
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
        colored = any(abs(item[2][0] - 1.0) > 1e-4 for item in verts)
        parts.append(
            HeightPart(
                layer=layer,
                positions=[item[0] for item in verts],
                indices=grouped_index[layer],
                uvs=[item[1] for item in verts],
                colors=[item[2] for item in verts] if colored else None,
            )
        )
    return parts, reports
