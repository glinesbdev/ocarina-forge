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
  257 by 257 float32 heights, stored in the same block-linear order as a
  texture (16 GOBs per block, so log2 4). Rows run along z. Columns 0..255
  fill sixteen GOBs. Column 256 is one texel wide, and its odd rows are
  packed 4 bytes after the even row; the ordinary GOB read lands on padding
  and comes back as exact 0. That slot is read from the packed address, and
  a leftover one-sample zero copies its horizontal neighbour. A finite
  sample, including a real 0, is ground: the stored range on the retail
  files is about 0..0.18, so 0 is the bottom of that range, at the terrain
  origin. Only a non-finite sample (NaN or infinity) is a hole. The header
  we read has no hole bitmap. An empty `.mate` texel is a missing material,
  not a hole.

A second scene named `<scene>_sub` (or a GameBancParam string that ends in
`_sub`) is a separate overlay, not a finer copy of the walkable floor. On
the retail Kokiri dump its heights sit about 13–24 m above the actors. Each
scene is meshed on its own: the finest tile in that scene wins, and a sub
quad does not drop a main cell. A tile reads `.hght` and `.mate` from its
own scene directory. A coarse cell is dropped only when its centre lies in
a quad that a finer tile of the same scene actually emitted. A finer sample
that never became a triangle leaves the coarse ground in place.
Every remaining tile that meets the placed actors, expanded by 200 m, is meshed. There is no fixed tile cap. Samples are about 2 m apart
near the actors, 5 m in the middle distance, and 10 m at the edge of the
margin, and never finer than step 8 on that path. Each tile grows a vertical
skirt so a coarser neighbour does not leave a crack. Surface triangles are
wound so the normal points up.

The per-corner material is `<tile>.mate`, the same TARB-and-zstd wrapper as a
heightmap. After the header, `+0x2C` is a relative offset to a 4-byte texel
block and `+0x30` is its size. Retail blocks are two copies of the 257×257
block-linear surface used by heights (log2 4, 417792 bytes each). A texel is
material A, material B, then two blend bytes. 255 and 127 mean “no index”.
An empty or invalid texel copies a neighbouring material; it is never drawn
as magenta. 0x7f in a blend byte is a neutral mix, so the two albedos are
blended halfway. A real blend byte is that byte divided by 255. Both of those
weights are a guess. The stored layer list still names material A unless a
non-neutral weight is 128 or more, so a diagnostic can name one index. A
tight linear grid is still accepted, and there blend at or above 128 selects
material B. The block at `.hght` `+0x1C` is only a fallback when no `.mate`
decodes. `<tile>.<child|adult>.bake_ao.extm` uses the same header and is a
1-byte or 4-byte block-linear grid. `<tile>.custom.extm` is left unused.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from ocarina_forge.formats.swizzle import deswizzle, surface_bytes
from ocarina_forge.formats.zsio import decompress

SIZE = 257
_HEIGHT_LOG2 = 4
_UV_METRES = 8.0
_CLIP_PAD = 200.0
_MAX_LAYERS = 24
_MATE_LAYER = 89
_MATE_KEEP = 48
_NEAR_M = 70.0
_MID_M = 180.0
_SKIRT_M = 4.0
_STEPS = (8, 16, 32, 64)


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
    holes: int = 0
    dropped: int = 0
    zeros: int = 0


@dataclass
class HeightPart:
    """Triangles that share one material pair. `layer` is None when the tile had no index.

    `weights` is the 0..1 mix toward `layer_b` at each vertex. The pair itself
    does not change across a triangle, so a shader can blend the two albedos
    without interpolating the layer ids.
    """

    layer: int | None
    positions: list[tuple[float, float, float]]
    indices: list[int]
    uvs: list[tuple[float, float]]
    colors: list[tuple[float, float, float]] | None = None
    layer_b: int | None = None
    weights: list[float] | None = None
    marks: list[tuple[float, float]] | None = None


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


def read_tscb_resources(data: bytes) -> dict:
    """Read the resource index of an OoT 0x10D TSCB without opening tile files."""
    if len(data) < 0x80 or data[:4] != b"TSCB":
        raise TerrainError("not a TSCB terrain index")
    version = struct.unpack_from("<I", data, 4)[0]
    if version != 0x10D:
        raise TerrainError(f"TSCB resource layout for version {version:#x} is unverified")
    strings = _rel(data, 0x0C)
    if not 0x80 <= strings < len(data):
        raise TerrainError("TSCB string pool is outside the file")

    def string(at: int) -> str:
        start = _rel(data, at)
        end = data.find(b"\0", start, start + 256)
        if not strings <= start < len(data) or end < 0:
            raise TerrainError(f"TSCB resource string at {at:#x} is invalid")
        return data[start:end].decode("utf-8", "replace")

    def offsets(count_at: int, table_at: int, maximum: int) -> list[int]:
        count = struct.unpack_from("<I", data, count_at)[0]
        start = _rel(data, table_at)
        end = start + 4 * count
        if count > maximum or not 0x7C <= start <= end <= strings:
            raise TerrainError(f"TSCB offset table at {table_at:#x} is outside the index")
        result = [_rel(data, slot) for slot in range(start, end, 4)]
        if any(not end <= at < strings for at in result):
            raise TerrainError(f"TSCB offset table at {table_at:#x} points outside its data")
        return result

    types = {}
    for at in offsets(0x30, 0x70, 4096):
        if at + 0x70 > strings:
            raise TerrainError("TSCB file-type record is truncated")
        code = struct.unpack_from("<I", data, at)[0]
        if code in types:
            raise TerrainError(f"TSCB repeats file type {code:#x}")
        resource_type = at + 0x6C
        types[code] = {
            "type": code,
            "name": string(at + 0x60),
            "extension": string(at + 0x64),
            "resourceType": string(resource_type) if _rel(data, resource_type) != resource_type else None,
        }

    area_offsets = offsets(0x38, 0x78, 200000)
    boundaries = sorted(set(area_offsets) | {strings})
    ends = dict(zip(boundaries, boundaries[1:]))
    tiles = []
    for at in area_offsets:
        end = ends[at]
        if at + 0x68 > end:
            raise TerrainError("TSCB area header is truncated")
        count = struct.unpack_from("<I", data, at + 0x20)[0]
        slots = at + 0x68
        slots_end = slots + 4 * count
        if slots_end > end:
            raise TerrainError("TSCB area resource table is truncated")
        x, z, edge = struct.unpack_from("<3f", data, at)
        if not all(math.isfinite(value) for value in (x, z, edge)) or edge <= 0:
            raise TerrainError("TSCB area dimensions are invalid")
        resources = []
        for slot in range(slots, slots_end, 4):
            record = _rel(data, slot)
            # The last record can have less trailing padding before strings.
            if not slots_end <= record <= end - 0x28:
                raise TerrainError("TSCB area resource record points outside its area")
            code = struct.unpack_from("<I", data, record)[0]
            low, high = struct.unpack_from("<2f", data, record + 0x20)
            resources.append({
                "type": code,
                "extension": types.get(code, {}).get("extension"),
                "rawMin": low if math.isfinite(low) else None,
                "rawMax": high if math.isfinite(high) else None,
            })
        tiles.append({"name": string(at + 0x60), "center": [x, z], "edge": edge, "resources": resources})
    scale, height = struct.unpack_from("<2f", data, 0x10)
    if not math.isfinite(scale) or not math.isfinite(height) or scale <= 0 or height == 0:
        raise TerrainError("TSCB resource index scales are invalid")
    return {"version": version, "scale": scale, "heightScale": height, "fileTypes": list(types.values()), "tiles": tiles}


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


def _exact_zeros(values: list[float]) -> int:
    return sum(1 for value in values if value == 0.0)


def _roughness(values: list[float]) -> float:
    """Mean absolute step between finite neighbours. A scrambled grid scores high."""
    total = 0.0
    count = 0
    for z in range(SIZE - 1):
        row = z * SIZE
        for x in range(SIZE - 1):
            here = values[row + x]
            if not _finite(here):
                continue
            right = values[row + x + 1]
            down = values[row + SIZE + x]
            if _finite(right):
                total += abs(here - right)
                count += 1
            if _finite(down):
                total += abs(here - down)
                count += 1
    if not count:
        return 1e9
    return total / count


def _decode_block(block: bytes, log2: int) -> list[float] | None:
    """Untile one 257×257 float image.

    Sixteen GOBs across hold columns 0..255. Column 256 is the first texel of
    the next GOB, and its odd rows are packed 4 bytes after the even row.
    The ordinary formula reads 16 bytes later, which is padding, so those
    samples come back as exact 0 and the mesh drops a dashed wall to the
    origin. When that read is 0, the packed address is used instead.
    """
    from ocarina_forge.formats.swizzle import _gob_address

    need = surface_bytes(SIZE, SIZE, 1, 1, 4, log2)
    if len(block) < need:
        return None
    linear = deswizzle(SIZE, SIZE, 1, 1, 4, 0, log2, block)
    tight = SIZE * SIZE * 4
    if len(linear) < tight:
        return None
    values = list(struct.unpack_from(f"<{SIZE * SIZE}f", linear))
    block_height = 1 << max(0, log2)
    column = SIZE - 1
    for y in range(1, SIZE, 2):
        index = y * SIZE + column
        if values[index] != 0.0:
            continue
        pos = _gob_address(column, y - 1, SIZE, 4, block_height) + 4
        if pos < 0 or pos + 4 > len(block):
            continue
        packed = struct.unpack_from("<f", block, pos)[0]
        if _finite(packed) and packed != 0.0:
            values[index] = packed
    return values


def _fill_isolated_zeros(values: list[float]) -> list[float]:
    """Copy a horizontal neighbour into an exact 0 that sits between real heights.

    A dashed column is one sample wide. A broad patch of zeros is left for
    ``_uncover_sentinel_zeros``: those samples are missing, not ground at the origin.
    """
    out = list(values)
    for _pass in range(2):
        nxt = list(out)
        for z in range(SIZE):
            row = z * SIZE
            for x in range(SIZE):
                if nxt[row + x] != 0.0:
                    continue
                left = nxt[row + x - 1] if x else 0.0
                right = nxt[row + x + 1] if x + 1 < SIZE else 0.0
                if left != 0.0 and right != 0.0 and _finite(left) and _finite(right):
                    nxt[row + x] = (left + right) * 0.5
                elif x == SIZE - 1 and left != 0.0 and _finite(left):
                    nxt[row + x] = left
                elif x == 0 and right != 0.0 and _finite(right):
                    nxt[row + x] = right
        out = nxt
    return out


# Packed leftovers and empty parent cells under a missing finer tile can be
# a tiny non-zero (1e-5 .. 1e-3) instead of an exact 0. At height scale 800
# that is still under a metre, and meshing it produces a flat ground sheet at
# the origin. The same rule is used when sampling.
_HEIGHT_HOLE = 1e-3


def _uncover_sentinel_zeros(values: list[float]) -> list[float]:
    """Exact 0, or a near-zero leftover, is uncovered, not a height at the origin."""
    return [float("nan") if not _finite(value) or abs(value) <= _HEIGHT_HOLE else value for value in values]

def _plausible_height_max(values) -> float | None:
    """Largest finite sample in the normalised 0..1 height range, excluding 0."""
    best = None
    for value in values:
        if value == value and 0.0 < value < 2.0:
            if best is None or value > best:
                best = value
    return best


def _packed_height_max(block: bytes) -> float | None:
    """Max plausible height already present in the packed (still swizzled) block."""
    if len(block) < 4:
        return None
    return _plausible_height_max(struct.unpack_from(f"<{len(block) // 4}f", block))


def _recovers_packed_range(values: list[float], packed_max: float | None) -> bool:
    """True when a deswizzle still contains the highs that were in the packed block."""
    if packed_max is None or packed_max <= 0:
        return True
    decoded_max = _plausible_height_max(values)
    return decoded_max is not None and decoded_max >= packed_max * 0.9


def _choose_height_decode(default: list[float], alternatives: list[list[float]], packed_max: float | None) -> list[float]:
    """Keep the default GOB layout unless it dropped the packed height range.

    A sparse tile can have tens of thousands of real zeros. The older "fewer
    zeros, smoother field" guess then prefers an almost-flat wrong log2
    (decoded max ~1/256) over the layout that still has the highs recorded
    in the TSCB resource rawMax and in the packed block itself.
    """
    if _recovers_packed_range(default, packed_max):
        return default
    recovered = [alt for alt in alternatives if _recovers_packed_range(alt, packed_max)]
    if not recovered:
        return default
    return min(recovered, key=_roughness)


def _as_floats(block: bytes) -> list[float] | None:
    tight = SIZE * SIZE * 4
    if len(block) == tight:
        return _uncover_sentinel_zeros(_fill_isolated_zeros(list(struct.unpack_from(f"<{SIZE * SIZE}f", block))))
    chosen = _decode_block(block, _HEIGHT_LOG2)
    if chosen is None:
        return None
    zeros = _exact_zeros(chosen)
    if zeros > 64:
        best = chosen
        best_zeros = zeros
        best_rough = _roughness(chosen)
        for log2 in (0, 1, 2, 3, 5):
            alt = _decode_block(block, log2)
            if alt is None:
                continue
            alt_zeros = _exact_zeros(alt)
            alt_rough = _roughness(alt)
            if alt_zeros * 2 < best_zeros and alt_rough <= best_rough:
                best = alt
                best_zeros = alt_zeros
                best_rough = alt_rough
        chosen = best
    alternatives = []
    for log2 in (0, 1, 2, 3, 5):
        alt = _decode_block(block, log2)
        if alt is not None:
            alternatives.append(alt)
    chosen = _choose_height_decode(chosen, alternatives, _packed_height_max(block))
    return _uncover_sentinel_zeros(_fill_isolated_zeros(chosen))


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


def _pair_weight(b2: int, b3: int) -> tuple[float | None, int | None]:
    """Blend toward material B, and the raw byte when one is not 0 or 0x7f.

    0x7f with no other weight is a halfway mix. A byte that is neither 0 nor
    0x7f is that byte over 255. Both bytes 0 means the blend was not written,
    so the mix stays on material A. The halfway reading is a guess.
    """
    weight = next((item for item in (b2, b3) if item not in (0, 0x7F)), None)
    if weight is not None:
        return weight / 255.0, weight
    if b2 == 0x7F or b3 == 0x7F:
        return 0.5, None
    return 0.0, None


def _read_sentinel_plane(raw: bytes) -> tuple[list[int], list[tuple[int, int, float]], dict[int, int], dict[int, int]]:
    """Chosen layer and (material A, material B, weight) per sample.

    (0, 0, 0, 0) is an empty texel, not material 0. 255 and 127 are not indices.
    The chosen layer is material A unless a non-neutral weight is 128 or more,
    which keeps the older diagnostic. The weight itself is the blend the mesh uses.
    """
    chosen = []
    pairs: list[tuple[int, int, float]] = []
    seen0: dict[int, int] = {}
    seen1: dict[int, int] = {}
    for index in range(SIZE * SIZE):
        b0, b1, b2, b3 = raw[index * 4 : index * 4 + 4]
        if (b0, b1, b2, b3) == (0, 0, 0, 0):
            chosen.append(-1)
            pairs.append((-1, -1, 0.0))
            continue
        first = b0 if b0 <= _MATE_LAYER else -1
        second = b1 if b1 <= _MATE_LAYER else -1
        if first >= 0:
            seen0[first] = seen0.get(first, 0) + 1
        if second >= 0:
            seen1[second] = seen1.get(second, 0) + 1
        mix, raw_weight = _pair_weight(b2, b3)
        if first >= 0 and second >= 0:
            pairs.append((first, second, mix if mix is not None else 0.0))
        elif first >= 0:
            pairs.append((first, first, 0.0))
        elif second >= 0:
            pairs.append((second, second, 0.0))
        else:
            pairs.append((-1, -1, 0.0))
        if first >= 0 and second >= 0 and raw_weight is not None and raw_weight >= 128:
            chosen.append(second)
        elif first >= 0:
            chosen.append(first)
        elif second >= 0:
            chosen.append(second)
        else:
            chosen.append(-1)
    return chosen, pairs, seen0, seen1


def _decode_retail_planes(planes: list[bytes | None]) -> tuple[list[int], list[tuple[int, int, float]], str] | None:
    decoded: list[list[int] | None] = []
    decoded_pairs: list[list[tuple[int, int, float]] | None] = []
    parts = []
    for number, plane in enumerate(planes, start=1):
        if plane is None or not _sentinel_mate(plane):
            decoded.append(None)
            decoded_pairs.append(None)
            continue
        chosen, pairs, seen0, seen1 = _read_sentinel_plane(plane)
        if not seen0 and not seen1:
            decoded.append(None)
            decoded_pairs.append(None)
            continue
        decoded.append(chosen)
        decoded_pairs.append(pairs)
        text = f"image {number} b0 {_fmt_counts(seen0)}, b1 {_fmt_counts(seen1)}"
        parts.append(text.replace("; ", ", "))
    empty = [-1] * (SIZE * SIZE)
    empty_pairs = [(-1, -1, 0.0)] * (SIZE * SIZE)
    if not any(item is not None for item in decoded):
        if any(plane is not None and _sentinel_mate(plane) for plane in planes):
            return empty, empty_pairs, "hist empty; 257×257×4 block-linear, indices were 255 or 127 (a guess)"
        return None
    merged = []
    merged_pairs: list[tuple[int, int, float]] = []
    for index in range(SIZE * SIZE):
        layer = -1
        pair = (-1, -1, 0.0)
        for plane, plane_pairs in zip(decoded, decoded_pairs):
            if plane is not None and plane[index] >= 0:
                layer = plane[index]
                if plane_pairs is not None:
                    pair = plane_pairs[index]
                break
            if plane_pairs is not None and (plane_pairs[index][0] >= 0 or plane_pairs[index][1] >= 0):
                pair = plane_pairs[index]
                break
        merged.append(layer)
        merged_pairs.append(pair)
    merged, kept, dropped = _cap_mate(merged)
    merged_pairs = _cap_pairs(merged_pairs, _pair_keep(merged_pairs))
    note = (
        "hist "
        + " | ".join(parts)
        + f"; 257×257×4 block-linear, {len(planes)} image(s), "
        + "255 and 127 mean no material, 0x7f blend mixes byte 0 and byte 1 equally (a guess)"
    )
    if dropped:
        note += f", {dropped} rarer materials left as the earth colour"
    if not kept and not any(item >= 0 for item in merged):
        note = "hist empty; 257×257×4 block-linear decoded with no material index (a guess)"
    return merged, merged_pairs, note


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


def _pair_keep(pairs: list[tuple[int, int, float]]) -> set[int]:
    """Layers to keep, counting both sides of a blend so material B is not dropped."""
    counts: dict[int, int] = {}
    for first, second, _weight in pairs:
        for value in (first, second):
            if value >= 0:
                counts[value] = counts.get(value, 0) + 1
    if len(counts) <= _MATE_KEEP:
        return set(counts)
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return {item for item, _count in ranked[:_MATE_KEEP]}


def _cap_pairs(pairs: list[tuple[int, int, float]], keep: set[int]) -> list[tuple[int, int, float]]:
    if not keep:
        return [(-1, -1, 0.0) for _item in pairs]
    capped = []
    for first, second, weight in pairs:
        left = first if first in keep else -1
        right = second if second in keep else -1
        if left < 0 and right >= 0:
            capped.append((right, right, 0.0))
        elif right < 0 and left >= 0:
            capped.append((left, left, 0.0))
        elif left < 0 and right < 0:
            capped.append((-1, -1, 0.0))
        else:
            capped.append((left, right, weight))
    return capped


def _blend_grid(raw: bytes, width: int) -> tuple[list[int], list[tuple[int, int, float]], str] | None:
    count = width * width
    if len(raw) < count * 4:
        return None
    chosen = []
    pairs = []
    usable = 0
    for index in range(count):
        m0, m1, blend, _extra = raw[index * 4 : index * 4 + 4]
        left = m0 if m0 <= _MATE_LAYER else -1
        right = m1 if m1 <= _MATE_LAYER else -1
        layer = None
        if left >= 0 and right >= 0:
            layer = right if blend >= 128 else left
            pairs.append((left, right, blend / 255.0))
        elif left >= 0:
            layer = left
            pairs.append((left, left, 0.0))
        elif right >= 0:
            layer = right
            pairs.append((right, right, 0.0))
        else:
            pairs.append((-1, -1, 0.0))
        if layer is not None:
            usable += 1
            chosen.append(layer)
        else:
            chosen.append(-1)
    if usable * 2 < count:
        return None
    layers, kept, dropped = _cap_mate(_resample(chosen, width))
    fitted = _cap_pairs(_resample(pairs, width), _pair_keep(_resample(pairs, width)))
    note = (
        f"mate {width}×{width} blend records (material, material, blend, extra; "
        f"blend >= 128 uses the second index; this layout is a guess), layers {', '.join(str(item) for item in kept) or 'none'}"
    )
    if dropped:
        note += f", {dropped} rarer materials left as the earth colour"
    return layers, fitted, note


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


def _parse_mate_block(block: bytes) -> tuple[list[int], list[tuple[int, int, float]] | None, str] | None:
    planes = _swizzled_planes(block, 4)
    if planes:
        retail = _decode_retail_planes(planes)
        if retail:
            return retail
        raw = next((plane for plane in planes if plane is not None), None)
        if raw is not None:
            parsed = _blend_grid(raw, SIZE)
            if parsed:
                layers, pairs, note = parsed
                if len(block) != SIZE * SIZE * 4:
                    note += ", block-linear"
                return layers, pairs, note
    for width in (SIZE, 256):
        raw = _linear_or_swizzled(block, width, 4)
        if raw is not None and not _sentinel_mate(raw):
            parsed = _blend_grid(raw, width)
            if parsed:
                swizzle = "" if len(block) == width * width * 4 else ", block-linear"
                layers, pairs, note = parsed
                return layers, pairs, note + swizzle
        raw = _linear_or_swizzled(block, width, 1)
        if raw is not None:
            parsed = _index_grid(raw, width, "u8")
            if parsed:
                layers, note = parsed
                return layers, None, note
        raw = _linear_or_swizzled(block, width, 2)
        if raw is not None:
            parsed = _index_grid(raw, width, "u16")
            if parsed:
                layers, note = parsed
                return layers, None, note
    return None


def split_histogram(note: str) -> tuple[str, str]:
    """The `hist ...` clause, and the rest of the mate note."""
    if note.startswith("hist ") and "; " in note:
        first, rest = note.split("; ", 1)
        return first[len("hist ") :], rest
    return "", note


def read_terrain_material(data: bytes, dicts=None) -> tuple[list[int] | None, list[tuple[int, int, float]] | None, str]:
    """Layer list, optional (material A, material B, weight) pairs, and a note.

    A negative index means that sample had no usable layer. `read_mate` is the
    layer list alone, which is what older callers check.
    """
    try:
        payload = unpack_hght(data, dicts)
    except TerrainError as exc:
        return None, None, str(exc)
    parsed = _parse_mate_block(payload)
    if parsed:
        layers, pairs, note = parsed
        return layers, pairs, f"{note} (payload)"
    seen = []
    for at, label in ((0x2C, "+0x2C"), (0x1C, "+0x1C")):
        block = _block(payload, at)
        if not block:
            continue
        seen.append(f"{label} {len(block)} bytes")
        parsed = _parse_mate_block(block)
        if parsed:
            layers, pairs, note = parsed
            format_byte = payload[4] if len(payload) > 4 else None
            if format_byte is not None:
                note += f", format byte {format_byte:#04x}"
            return layers, pairs, f"{note} ({label})"
    where = ", ".join(seen) if seen else "no image block"
    return None, None, f"mate did not contain a material grid ({where})"


def read_mate(data: bytes, dicts=None) -> tuple[list[int] | None, str]:
    """Per-sample MaterialAlb index, or None when the file is not a material grid.

    A negative index means that sample had no usable layer and should use the earth colour.
    """
    layers, _pairs, note = read_terrain_material(data, dicts)
    return layers, note


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


def _step_for(edge_metres: float, spacing: float) -> int:
    """Smallest step in ``_STEPS`` whose samples are at least ``spacing`` metres apart."""
    if edge_metres <= 0:
        return _STEPS[-1]
    raw = 256.0 * spacing / edge_metres
    for step in _STEPS:
        if step + 1e-6 >= raw:
            return step
    return _STEPS[-1]


def sample_steps(tiles, scale: float, origin, focus: tuple[float, float]) -> tuple[dict[str, int], str]:
    """About 2 m between samples near the actors, coarser farther out. Never step 4."""
    focus_x, focus_z = focus
    measured = []
    near = 0
    for center_x, center_z, edge, name in tiles:
        box = _world_box(center_x, center_z, edge, scale, origin)
        dx = (box[0] + box[2]) / 2 - focus_x
        dz = (box[1] + box[3]) / 2 - focus_z
        distance = (dx * dx + dz * dz) ** 0.5
        measured.append((name, distance, abs(edge * scale)))
        if distance <= _NEAR_M:
            near += 1
    near_spacing = 3.0 if near > 24 else 2.0
    steps: dict[str, int] = {}
    used: set[int] = set()
    capped = False
    for name, distance, edge_metres in measured:
        if distance <= _NEAR_M:
            spacing = near_spacing
        elif distance <= _MID_M:
            spacing = 5.0
        else:
            spacing = 10.0
        step = _step_for(edge_metres, spacing)
        # A short edge makes the spacing rule pick step 64, and the tile
        # collapses to a 5×5 sheet. Keep small tiles on a lattice that
        # still covers them.
        if edge_metres <= 24 and step > 8:
            step = 8
            capped = True
        elif edge_metres <= 80 and step > 16:
            step = 16
            capped = True
        steps[name] = step
        used.add(step)
    if not used:
        return steps, "height samples about 10 m apart"
    note = f"height samples about {near_spacing:.0f} m apart near the actors"
    if len(used) > 1:
        note += f" (step {min(used)} to {max(used)})"
    if near > 24:
        note += f" ({near} tiles within {_NEAR_M:.0f} m, so nearby samples are coarser)"
    if capped:
        note += "; small tiles stay on a finer lattice"
    return steps, note


def _fill_pairs(pairs: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Copy a neighbouring material into samples that have no index.

    A few sweeps is enough for the dashed lines at a material boundary. The
    index is taken from the nearest written texel. It is not blended.
    """
    count = SIZE * SIZE
    if len(pairs) < count:
        return pairs
    current = list(pairs[:count])
    neighbours = ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (-1, 1), (1, -1), (1, 1))
    for _sweep in range(6):
        nxt = list(current)
        changed = False
        for z in range(SIZE):
            row = z * SIZE
            for x in range(SIZE):
                first, second, _weight = current[row + x]
                if first >= 0 or second >= 0:
                    continue
                for dz, dx in neighbours:
                    zz = z + dz
                    xx = x + dx
                    if zz < 0 or xx < 0 or zz >= SIZE or xx >= SIZE:
                        continue
                    found = current[zz * SIZE + xx]
                    if found[0] >= 0 or found[1] >= 0:
                        nxt[row + x] = found
                        changed = True
                        break
        current = nxt
        if not changed:
            break
    return current


def _finite(value: float) -> bool:
    """A usable height. 0 is the bottom of the normalised range, not a hole."""
    return value == value and value not in (float("inf"), float("-inf"))


def _bracket(samples: list[int], unit: float) -> int | None:
    """Index of the sample span that contains ``unit`` on the 0..256 lattice."""
    if not samples or unit < samples[0] or unit > samples[-1]:
        return None
    for index in range(len(samples) - 1):
        if samples[index] <= unit <= samples[index + 1]:
            return index
    return None


def _covered_by_mesh(unit_x: float, unit_z: float, edge: float, meshes) -> bool:
    """True when a finer tile emitted a quad over this point.

    A finite sample in the finer height grid is not enough. The quad has to
    be in the mesh. A cell the finer tile skipped, or a coarse step whose
    centre sits on a yet finer tile so the whole quad was dropped, leaves
    this ground in place.
    """
    for mesh in meshes:
        if mesh["edge"] >= edge - 1e-4:
            continue
        if not (mesh["x0"] < unit_x < mesh["x1"] and mesh["z0"] < unit_z < mesh["z1"]):
            continue
        span_x = mesh["x1"] - mesh["x0"]
        span_z = mesh["z1"] - mesh["z0"]
        if span_x <= 0 or span_z <= 0:
            continue
        column = _bracket(mesh["samples"], (unit_x - mesh["x0"]) / span_x * (SIZE - 1))
        row = _bracket(mesh["samples"], (unit_z - mesh["z0"]) / span_z * (SIZE - 1))
        if row is None or column is None:
            continue
        if (row, column) in mesh["kept"]:
            return True
    return False


def _tile_hue(name: str) -> float:
    acc = 2166136261
    for byte in name.encode("utf-8", "replace"):
        acc ^= byte
        acc = (acc * 16777619) & 0xFFFFFFFF
    return (acc % 1000) / 1000.0


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
    """Mesh the tiles. Each item is centre x, centre z, edge, name, heights, layers, optional occlusion, optional pairs, optional sub-folder flag."""
    default_step = _sample_step(len(tiles))
    jobs = []
    reports: list[TileInfo] = []
    ox, oy, oz = origin
    for tile in tiles:
        center_x, center_z, edge, name, heights, layers = tile[:6]
        occlusion = tile[6] if len(tile) > 6 else None
        pairs = tile[7] if len(tile) > 7 else None
        sub = float(tile[8]) if len(tile) > 8 and isinstance(tile[8], (int, float)) else 0.0
        step = int((steps or {}).get(name) or default_step)
        samples = _indices(step)
        count = len(samples)
        info = TileInfo(name=name, center=(center_x, center_z), edge=edge, step=step)
        info.holes = sum(1 for value in heights[: SIZE * SIZE] if not _finite(value))
        info.zeros = sum(1 for value in heights[: SIZE * SIZE] if value == 0.0)
        present = [value for value in heights[: SIZE * SIZE] if _finite(value)]
        if present:
            info.height_min = min(present)
            info.height_max = max(present)
        box = _world_box(center_x, center_z, edge, scale, origin)
        if not _hits(box, clip):
            info.reason = "outside the placed actors"
            reports.append(info)
            continue
        if pairs is None and layers is not None and len(layers) >= SIZE * SIZE:
            pairs = [(item if item >= 0 else -1, item if item >= 0 else -1, 0.0) for item in layers[: SIZE * SIZE]]
        if pairs is not None and len(pairs) >= SIZE * SIZE:
            pairs = _fill_pairs(pairs)
        grid = []
        for row in samples:
            line = []
            for col in samples:
                value = heights[row * SIZE + col]
                line.append(value if _finite(value) else None)
            grid.append(line)

        cells = []
        for row in range(count - 1):
            for col in range(count - 1):
                corners = (
                    grid[row][col],
                    grid[row][col + 1],
                    grid[row + 1][col],
                    grid[row + 1][col + 1],
                )
                if any(item is None for item in corners):
                    continue
                x0 = (center_x - edge / 2) + edge * samples[col] / (SIZE - 1)
                x1 = (center_x - edge / 2) + edge * samples[col + 1] / (SIZE - 1)
                z0 = (center_z - edge / 2) + edge * samples[row] / (SIZE - 1)
                z1 = (center_z - edge / 2) + edge * samples[row + 1] / (SIZE - 1)
                cells.append((row, col, x0, z0, x1, z1))
        jobs.append(
            {
                "center_x": center_x,
                "center_z": center_z,
                "edge": edge,
                "name": name,
                "samples": samples,
                "count": count,
                "grid": grid,
                "pairs": pairs,
                "occlusion": occlusion,
                "info": info,
                "cells": cells,
                "mark": (_tile_hue(name), sub),
                "keep": [],
            }
        )
    meshes = []
    for job in sorted(jobs, key=lambda item: (item["edge"], item["name"])):
        kept = set()
        keep = []
        for row, col, x0, z0, x1, z1 in job["cells"]:
            if _covered_by_mesh((x0 + x1) * 0.5, (z0 + z1) * 0.5, job["edge"], meshes):
                job["info"].dropped += 1
                continue
            keep.append((row, col))
            kept.add((row, col))
        job["keep"] = keep
        meshes.append(
            {
                "edge": job["edge"],
                "x0": job["center_x"] - job["edge"] / 2,
                "z0": job["center_z"] - job["edge"] / 2,
                "x1": job["center_x"] + job["edge"] / 2,
                "z1": job["center_z"] + job["edge"] / 2,
                "samples": job["samples"],
                "kept": kept,
            }
        )
    grouped: dict[tuple[int | None, int | None], list] = {}
    grouped_index: dict[tuple[int | None, int | None], list[int]] = {}
    for job in jobs:
        center_x = job["center_x"]
        center_z = job["center_z"]
        edge = job["edge"]
        samples = job["samples"]
        count = job["count"]
        grid = job["grid"]
        pairs = job["pairs"]
        occlusion = job["occlusion"]
        info = job["info"]
        mark = job["mark"]
        keep = job["keep"]

        def sample_pair(row: int, col: int) -> tuple[int, int, float]:
            if not pairs:
                return (-1, -1, 0.0)
            found = pairs[samples[row] * SIZE + samples[col]]
            return int(found[0]), int(found[1]), float(found[2])

        if not keep:
            if info.dropped and info.holes < SIZE * SIZE:
                info.reason = "a finer tile's mesh covers every finite sample"
            elif info.holes:
                info.reason = "no valid height; a non-finite sample is a hole, and 0 is ground"
            else:
                info.reason = "no valid height samples"
            reports.append(info)
            continue
        layer_cells: dict[tuple[int | None, int | None], list[tuple[int, int]]] = {}
        seen_layers: set[int] = set()
        for row, col in keep:
            first, second, _weight = sample_pair(row, col)
            layer = first if first >= 0 else None
            other = second if second >= 0 else None
            if layer is not None:
                seen_layers.add(layer)
            layer_cells.setdefault((layer, other), []).append((row, col))
        info.layers = sorted(seen_layers)
        info.status = "drawn"
        shade = occlusion if occlusion and len(occlusion) >= SIZE * SIZE else None
        skirt = max(_SKIRT_M, abs(edge * scale) * 0.02)
        last = count - 2
        for (layer, other), cells in layer_cells.items():
            bucket = grouped.setdefault((layer, other), [])
            tris = grouped_index.setdefault((layer, other), [])
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
                _first, _second, weight = sample_pair(row, col)
                index = len(bucket)
                used[key] = index
                bucket.append((position, uv, (amount, amount, amount), weight, mark))
                return index

            def drop(row: int, col: int, down: float) -> int:
                sx = samples[col]
                sz = samples[row]
                unit_x = (center_x - edge / 2) + edge * sx / (SIZE - 1)
                unit_z = (center_z - edge / 2) + edge * sz / (SIZE - 1)
                position = (unit_x * scale + ox, grid[row][col] * height_scale + oy - down, unit_z * scale + oz)
                uv = (position[0] / _UV_METRES, position[2] / _UV_METRES)
                amount = shade[sz * SIZE + sx] if shade is not None else 1.0
                _first, _second, weight = sample_pair(row, col)
                index = len(bucket)
                bucket.append((position, uv, (amount, amount, amount), weight, mark))
                return index

            def skirt_edge(r0: int, c0: int, r1: int, c1: int, outward: str) -> None:
                top0 = drop(r0, c0, 0.0)
                top1 = drop(r1, c1, 0.0)
                bot0 = drop(r0, c0, skirt)
                bot1 = drop(r1, c1, skirt)
                if outward in ("-z", "+x"):
                    tris.extend((top0, top1, bot0, top1, bot1, bot0))
                else:
                    tris.extend((top0, bot0, top1, bot0, bot1, top1))

            def face_up(i0: int, i1: int, i2: int) -> None:
                p0, p1, p2 = bucket[i0][0], bucket[i1][0], bucket[i2][0]
                # Y of (p1 - p0) cross (p2 - p0). Negative means the camera above would see the back.
                facing = (p1[2] - p0[2]) * (p2[0] - p0[0]) - (p1[0] - p0[0]) * (p2[2] - p0[2])
                if facing < 0:
                    tris.extend((i0, i2, i1))
                else:
                    tris.extend((i0, i1, i2))

            for row, col in cells:
                a = vert(row, col)
                b = vert(row, col + 1)
                c = vert(row + 1, col)
                d = vert(row + 1, col + 1)
                # (x, z), (x, z+1), (x+1, z) then the opposite corner. Flipped when that would point down.
                face_up(a, c, b)
                face_up(b, c, d)
                added = 0
                if row == 0:
                    skirt_edge(row, col, row, col + 1, "-z")
                    added += 2
                if row == last:
                    skirt_edge(row + 1, col, row + 1, col + 1, "+z")
                    added += 2
                if col == 0:
                    skirt_edge(row, col, row + 1, col, "-x")
                    added += 2
                if col == last:
                    skirt_edge(row, col + 1, row + 1, col + 1, "+x")
                    added += 2
                info.triangles += 2 + added
        reports.append(info)
    parts = []
    for (layer, other), verts in grouped.items():
        if not verts or not grouped_index.get((layer, other)):
            continue
        colored = any(abs(item[2][0] - 1.0) > 1e-4 for item in verts)
        weighted = any(abs(item[3]) > 1e-4 for item in verts)
        parts.append(
            HeightPart(
                layer=layer,
                layer_b=other,
                positions=[item[0] for item in verts],
                indices=grouped_index[(layer, other)],
                uvs=[item[1] for item in verts],
                colors=[item[2] for item in verts] if colored else None,
                weights=[item[3] for item in verts] if weighted else None,
                marks=[item[4] for item in verts],
            )
        )
    return parts, reports


def write_coverage_png(path, positions, indices, origin, scale: float, size: int = 1024, layers=None) -> dict:
    """Paint emitted triangles into a top-down image of terrain-unit space.

    x increases to the right and z increases upward. The frame is -1..1.
    A black pixel was not covered. Main ground is red, the sub overlay is
    blue, and a pixel under both is purple. Green is the height.
    ``layers`` is 0 for main and 1 for sub, one value per triangle.
    """
    from PIL import Image, ImageDraw

    main_image = Image.new("RGB", (size, size), (0, 0, 0))
    sub_image = Image.new("RGB", (size, size), (0, 0, 0))
    ox, _oy, oz = origin
    scale = scale or 1.0
    triangles = []
    heights = []
    for start in range(0, len(indices) - 2, 3):
        tri = []
        for offset in range(3):
            point = positions[indices[start + offset]]
            tri.append(point)
            heights.append(point[1])
        triangles.append(tri)
    if not triangles:
        main_image.save(path)
        return {
            "covered": 0,
            "coveredMain": 0,
            "coveredSub": 0,
            "coveredBoth": 0,
            "pixels": size * size,
            "heightMin": 0.0,
            "heightMax": 0.0,
        }
    low = min(heights)
    high = max(heights)
    span = high - low or 1.0
    main_draw = ImageDraw.Draw(main_image)
    sub_draw = ImageDraw.Draw(sub_image)

    def pixel(point):
        unit_x = (point[0] - ox) / scale
        unit_z = (point[2] - oz) / scale
        x = int(round((unit_x + 1.0) * 0.5 * (size - 1)))
        y = int(round((1.0 - (unit_z + 1.0) * 0.5) * (size - 1)))
        return (x, y)

    for index, tri in enumerate(triangles):
        green = int(max(0, min(255, round((sum(point[1] for point in tri) / 3 - low) / span * 255))))
        overlay = layers is not None and index < len(layers) and layers[index]
        if overlay:
            sub_draw.polygon([pixel(point) for point in tri], fill=(0, green, 255))
        else:
            main_draw.polygon([pixel(point) for point in tri], fill=(255, green, 0))
    main_raw = main_image.tobytes()
    sub_raw = sub_image.tobytes()
    out = bytearray(len(main_raw))
    covered = covered_main = covered_sub = covered_both = 0
    for index in range(0, len(main_raw), 3):
        main_on = main_raw[index] > 0
        sub_on = sub_raw[index + 2] > 0
        green = sub_raw[index + 1] if sub_on else main_raw[index + 1]
        if main_on and sub_on:
            out[index : index + 3] = bytes((255, green, 255))
            covered_both += 1
            covered_main += 1
            covered_sub += 1
            covered += 1
        elif sub_on:
            out[index : index + 3] = bytes((0, green, 255))
            covered_sub += 1
            covered += 1
        elif main_on:
            out[index : index + 3] = bytes((255, green, 0))
            covered_main += 1
            covered += 1
    image = Image.frombytes("RGB", (size, size), bytes(out))
    image.save(path)
    return {
        "covered": covered,
        "coveredMain": covered_main,
        "coveredSub": covered_sub,
        "coveredBoth": covered_both,
        "pixels": size * size,
        "heightMin": low,
        "heightMax": high,
    }
