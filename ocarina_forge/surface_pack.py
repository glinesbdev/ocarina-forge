"""Compact walkable-surface pack for local Snap in the viewer.

The pack holds welded Land_* mesh vertices (float32), triangle indices, a
per-actor Hash range for exclusion, and the same heightmap tiles
``query_ground`` would sample. It is gzipped on disk under the Forge cache.
The binary layout is read by ``native/src/ground.rs``.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import struct
import time
from pathlib import Path

from ocarina_forge.actors import parse_hash
from ocarina_forge.dump import Dump
from ocarina_forge.formats.terrain import SIZE
from ocarina_forge.grounddebug import _load
from ocarina_forge.locate import user_cache_root
from ocarina_forge.modeldebug import scene_files
from ocarina_forge.world_surface import (
    actor_ident,
    local_floor_triangles,
    rolling_rock_map,
    terrain_actor,
    transform_triangle,
    _iter_actors,
)
from ocarina_forge.actors import ROLLING_ROCK_LAND, read_vec

MAGIC = b"OFGS"
VERSION = 1
PACK_VERSION = 1
STEP = 32.0
HEADER_FORMAT = "<4sHH6f6I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
assert HEADER_SIZE == 56
TILE_HEADER_SIZE = 24
ACTOR_SIZE = 16
HEIGHT_COUNT = SIZE * SIZE


class SurfacePackError(ValueError):
    """The surface pack could not be built or read."""


def cache_dir() -> Path:
    root = user_cache_root() / "surfaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _layer_code(layer: str) -> int:
    return 0 if str(layer) == "main" else 1


def _exploded_to_indexed(triangles) -> tuple[list[tuple[float, float, float]], list[int]]:
    positions: list[tuple[float, float, float]] = []
    indices: list[int] = []
    seen: dict[tuple[float, float, float], int] = {}
    for triangle in triangles:
        for corner in range(3):
            vertex = (float(triangle[corner * 3]), float(triangle[corner * 3 + 1]), float(triangle[corner * 3 + 2]))
            index = seen.get(vertex)
            if index is None:
                index = len(positions)
                seen[vertex] = index
                positions.append(vertex)
            indices.append(index)
    return positions, indices


def collect_meshes(dump: Dump, banc: str, cache: dict | None = None) -> list[dict]:
    """World-space Land_* meshes for a banc and its Static/Dynamic sibling."""
    cache = cache if cache is not None else {}
    actors = []
    for relative, actor_index, actor in _iter_actors(dump, banc):
        gyaml = str(actor.get("Gyaml") or "")
        verified = rolling_rock_map(relative) and gyaml.startswith(ROLLING_ROCK_LAND)
        if not verified and not terrain_actor(gyaml):
            continue
        triangles, source = local_floor_triangles(dump, gyaml, cache)
        if not triangles:
            continue
        translate = read_vec(actor, "Translate", [0.0, 0.0, 0.0])
        rotate = read_vec(actor, "Rotate", [0.0, 0.0, 0.0])
        scale = read_vec(actor, "Scale", [1.0, 1.0, 1.0])
        world = [transform_triangle(triangle, translate, rotate, scale) for triangle in triangles]
        positions, indices = _exploded_to_indexed(world)
        ident = actor_ident(actor)
        try:
            hash_value = parse_hash(ident) if ident else 0
        except Exception:
            hash_value = 0
        actors.append(
            {
                "hash": hash_value,
                "gyaml": gyaml,
                "banc": relative,
                "index": actor_index,
                "source": source or "mesh",
                "positions": positions,
                "indices": indices,
            }
        )
    return actors


def collect_heightmap(dump: Dump, banc: str) -> dict:
    """The same readable height tiles ``query_ground`` uses."""
    report = _load(dump, banc)
    tiles = []
    if report.get("kind") == "heightmap":
        for tile in report.get("tiles") or []:
            heights = tile.get("heights")
            if not heights or tile.get("status") != "readable" or not tile.get("center") or not tile.get("edge"):
                continue
            if len(heights) < HEIGHT_COUNT:
                continue
            tiles.append(
                {
                    "layer": _layer_code(tile.get("layer") or "main"),
                    "name": str(tile.get("name") or ""),
                    "center": [float(tile["center"][0]), float(tile["center"][1])],
                    "edge": float(tile["edge"]),
                    "heights": [float(value) for value in heights[:HEIGHT_COUNT]],
                }
            )
    origin = report.get("origin") or (0.0, 0.0, 0.0)
    return {
        "origin": [float(origin[0]), float(origin[1]), float(origin[2])],
        "scale": float(report.get("scale") or 1.0),
        "heightScale": float(report.get("heightScale") or 1.0),
        "tiles": tiles,
        "kind": report.get("kind") or "missing",
    }


def pack_surface(meshes: list[dict], heightmap: dict, *, step: float = STEP) -> bytes:
    positions: list[float] = []
    indices: list[int] = []
    actors: list[tuple[int, int, int]] = []
    for mesh in meshes:
        first_triangle = len(indices) // 3
        base = len(positions) // 3
        for vertex in mesh["positions"]:
            positions.extend((float(vertex[0]), float(vertex[1]), float(vertex[2])))
        for index in mesh["indices"]:
            indices.append(base + int(index))
        count = len(mesh["indices"]) // 3
        if count:
            actors.append((int(mesh.get("hash") or 0), first_triangle, count))

    names = bytearray()
    name_spans = []
    tiles = heightmap.get("tiles") or []
    for tile in tiles:
        encoded = str(tile.get("name") or "").encode("utf-8")
        name_spans.append((len(names), len(encoded)))
        names.extend(encoded)

    origin = heightmap.get("origin") or [0.0, 0.0, 0.0]
    header = struct.pack(
        HEADER_FORMAT,
        MAGIC,
        VERSION,
        0,
        float(step),
        float(origin[0]),
        float(origin[1]),
        float(origin[2]),
        float(heightmap.get("scale") or 1.0),
        float(heightmap.get("heightScale") or 1.0),
        len(positions) // 3,
        len(indices),
        len(actors),
        len(tiles),
        len(names),
        0,
    )
    out = io.BytesIO()
    out.write(header)
    if positions:
        out.write(struct.pack(f"<{len(positions)}f", *positions))
    if indices:
        out.write(struct.pack(f"<{len(indices)}I", *indices))
    for hash_value, first, count in actors:
        out.write(struct.pack("<QII", hash_value & 0xFFFFFFFFFFFFFFFF, first, count))
    for tile, (name_offset, name_len) in zip(tiles, name_spans):
        out.write(struct.pack(
            "<B3xIIfff",
            int(tile["layer"]) & 0xFF,
            name_offset,
            name_len,
            float(tile["center"][0]),
            float(tile["center"][1]),
            float(tile["edge"]),
        ))
    out.write(names)
    for tile in tiles:
        heights = list(tile["heights"])
        if len(heights) < HEIGHT_COUNT:
            heights.extend([0.0] * (HEIGHT_COUNT - len(heights)))
        out.write(struct.pack(f"<{HEIGHT_COUNT}f", *heights[:HEIGHT_COUNT]))
    return out.getvalue()


def _source_stamp(dump: Dump, banc: str) -> bytes:
    parts = [f"ofgs-{PACK_VERSION}".encode(), str(dump.root).encode(), banc.encode()]
    for relative in scene_files(dump, banc):
        path = dump.romfs / relative
        try:
            stat = path.stat()
            parts.append(f"{relative}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        except OSError:
            parts.append(f"{relative}:0:0".encode())
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
        digest.update(b"\0")
    return digest.hexdigest().encode()


def cache_paths(dump: Dump, banc: str) -> tuple[Path, Path, str]:
    digest = hashlib.sha256(_source_stamp(dump, banc)).hexdigest()
    folder = cache_dir()
    return folder / f"{digest}.ofgs.gz", folder / f"{digest}.json", digest


def build_surface(dump: Dump, banc: str, cache: dict | None = None) -> tuple[bytes, dict, bytes]:
    started = time.perf_counter()
    meshes = collect_meshes(dump, banc, cache=cache)
    heightmap = collect_heightmap(dump, banc)
    raw = pack_surface(meshes, heightmap)
    compressed = gzip.compress(raw, compresslevel=6)
    triangles = sum(len(mesh["indices"]) // 3 for mesh in meshes)
    vertices = sum(len(mesh["positions"]) for mesh in meshes)
    meta = {
        "banc": banc,
        "triangles": triangles,
        "vertices": vertices,
        "actors": len(meshes),
        "tiles": len(heightmap["tiles"]),
        "rawBytes": len(raw),
        "gzipBytes": len(compressed),
        "buildMs": (time.perf_counter() - started) * 1000,
        "kind": heightmap.get("kind"),
        "version": PACK_VERSION,
    }
    return compressed, meta, raw


def load_or_build_surface(dump: Dump, banc: str) -> tuple[Path, dict]:
    """Return the gzip cache path and metadata, building the pack if needed."""
    path, meta_path, digest = cache_paths(dump, banc)
    if path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("version") == PACK_VERSION and meta.get("digest") == digest:
                return path, meta
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    compressed, meta, _raw = build_surface(dump, banc)
    meta["digest"] = digest
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(compressed)
    tmp.replace(path)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path, meta


def unpack_surface(raw: bytes) -> dict:
    if len(raw) < HEADER_SIZE or raw[:4] != MAGIC:
        raise SurfacePackError("Not an Ocarina Forge ground surface pack.")
    unpacked = struct.unpack_from(HEADER_FORMAT, raw, 0)
    _magic, version, _flags, step, ox, oy, oz, scale, height_scale, vertex_count, index_count, actor_count, tile_count, names_size, _reserved = unpacked
    if version != VERSION:
        raise SurfacePackError(f"Unsupported surface pack version {version}.")
    offset = HEADER_SIZE
    pos_count = vertex_count * 3
    positions = list(struct.unpack_from(f"<{pos_count}f", raw, offset)) if pos_count else []
    offset += pos_count * 4
    indices = list(struct.unpack_from(f"<{index_count}I", raw, offset)) if index_count else []
    offset += index_count * 4
    actors = []
    for _ in range(actor_count):
        hash_value, first, count = struct.unpack_from("<QII", raw, offset)
        actors.append({"hash": hash_value, "firstTriangle": first, "triangleCount": count})
        offset += ACTOR_SIZE
    tile_headers = []
    for _ in range(tile_count):
        layer, name_offset, name_len, cx, cz, edge = struct.unpack_from("<B3xIIfff", raw, offset)
        tile_headers.append((layer, name_offset, name_len, cx, cz, edge))
        offset += TILE_HEADER_SIZE
    names = raw[offset:offset + names_size]
    offset += names_size
    tiles = []
    for layer, name_offset, name_len, cx, cz, edge in tile_headers:
        name = names[name_offset:name_offset + name_len].decode("utf-8", "replace")
        heights = list(struct.unpack_from(f"<{HEIGHT_COUNT}f", raw, offset))
        offset += HEIGHT_COUNT * 4
        tiles.append({"layer": layer, "name": name, "center": [cx, cz], "edge": edge, "heights": heights})
    return {
        "step": step,
        "origin": [ox, oy, oz],
        "scale": scale,
        "heightScale": height_scale,
        "positions": positions,
        "indices": indices,
        "actors": actors,
        "tiles": tiles,
        "vertexCount": vertex_count,
        "triangleCount": index_count // 3,
    }



def _bilinear(heights, fx: float, fz: float):
    from ocarina_forge.grounddebug import _bilinear as _height_bilinear

    return _height_bilinear(heights, fx, fz)


def sample_unpacked(pack: dict, x: float, z: float, exclude_hash: int = 0):
    """Python reference sample of a pack, matching ``query_ground`` / the wasm sampler."""
    from ocarina_forge.world_surface import MeshIndex, compose_height

    index = MeshIndex(step=pack["step"] or STEP)
    skip_first = None
    skip_count = 0
    if exclude_hash:
        for actor in pack["actors"]:
            if actor["hash"] == exclude_hash:
                skip_first = actor["firstTriangle"]
                skip_count = actor["triangleCount"]
                break
    positions = pack["positions"]
    indices = pack["indices"]
    triangle_count = len(indices) // 3
    for triangle_id in range(triangle_count):
        if skip_first is not None and skip_first <= triangle_id < skip_first + skip_count:
            continue
        i = triangle_id * 3
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        triangle = (
            positions[a * 3],
            positions[a * 3 + 1],
            positions[a * 3 + 2],
            positions[b * 3],
            positions[b * 3 + 1],
            positions[b * 3 + 2],
            positions[c * 3],
            positions[c * 3 + 1],
            positions[c * 3 + 2],
        )
        index.add(triangle, {})
    mesh = index.sample(x, z)
    mesh_y = None if not mesh else mesh.get("worldY")
    origin = pack["origin"]
    scale = pack["scale"] or 1.0
    height_scale = pack["heightScale"]
    unit_x = (x - origin[0]) / scale
    unit_z = (z - origin[2]) / scale
    hits = []
    for tile in pack["tiles"]:
        edge = tile["edge"]
        half = edge / 2
        x0 = tile["center"][0] - half
        z0 = tile["center"][1] - half
        if not (x0 <= unit_x <= x0 + edge and z0 <= unit_z <= z0 + edge):
            continue
        span = SIZE - 1
        fx = (unit_x - x0) / edge * span
        fz = (unit_z - z0) / edge * span
        raw = _bilinear(tile["heights"], fx, fz)
        if raw is None or raw == 0.0:
            continue
        hits.append((edge, tile["layer"], tile["name"], raw * height_scale + origin[1]))
    hits.sort()
    tile_y = hits[0][3] if hits else None
    world_y, source, _mesh = compose_height(tile_y, {"worldY": mesh_y} if mesh_y is not None else None)
    return {"worldY": world_y, "source": source, "meshY": mesh_y, "tileY": tile_y}
