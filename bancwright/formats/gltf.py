"""A small glTF binary writer for positions, UVs, normals, and one base-color image."""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass


@dataclass
class Primitive:
    positions: list[tuple[float, float, float]]
    indices: list[int]
    uvs: list[tuple[float, float]] | None = None
    normals: list[tuple[float, float, float]] | None = None
    png: bytes | None = None
    alpha: str = "opaque"
    cutoff: float = 0.5
    double_sided: bool = True
    base_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)


def png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        rows.extend(rgba[y * stride : (y + 1) * stride])
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")


def face_normals(positions: list[tuple[float, float, float]], indices: list[int]) -> list[tuple[float, float, float]]:
    normals = [[0.0, 0.0, 0.0] for _ in positions]
    for index in range(0, len(indices) - 2, 3):
        a, b, c = indices[index], indices[index + 1], indices[index + 2]
        if max(a, b, c) >= len(positions):
            continue
        ax, ay, az = positions[a]
        bx, by, bz = positions[b]
        cx, cy, cz = positions[c]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        for vertex in (a, b, c):
            normals[vertex][0] += nx
            normals[vertex][1] += ny
            normals[vertex][2] += nz
    out = []
    for nx, ny, nz in normals:
        length = (nx * nx + ny * ny + nz * nz) ** 0.5
        if length < 1e-8:
            out.append((0.0, 1.0, 0.0))
        else:
            out.append((nx / length, ny / length, nz / length))
    return out


def to_glb(primitives: list[Primitive]) -> bytes:
    bin_blob = bytearray()
    buffer_views = []
    accessors = []
    images = []
    textures = []
    materials = []
    gl_primitives = []
    image_of: dict[bytes, int] = {}
    material_of: dict[tuple, int] = {}

    def pad(alignment: int) -> None:
        while len(bin_blob) % alignment:
            bin_blob.append(0)

    def view(data: bytes, target: int | None = None) -> int:
        pad(4)
        offset = len(bin_blob)
        bin_blob.extend(data)
        item = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            item["target"] = target
        buffer_views.append(item)
        return len(buffer_views) - 1

    def accessor(view_index: int, count: int, type_name: str, component: int, minimum=None, maximum=None) -> int:
        item = {
            "bufferView": view_index,
            "componentType": component,
            "count": count,
            "type": type_name,
        }
        if minimum is not None:
            item["min"] = minimum
            item["max"] = maximum
        accessors.append(item)
        return len(accessors) - 1

    for prim in primitives:
        if len(prim.positions) < 3 or len(prim.indices) < 3:
            continue
        positions = prim.positions
        indices = [index for index in prim.indices if 0 <= index < len(positions)]
        if len(indices) < 3:
            continue
        if len(indices) % 3:
            indices = indices[: len(indices) - (len(indices) % 3)]
        normals = prim.normals or face_normals(positions, indices)
        pos_bytes = b"".join(struct.pack("<3f", *xyz) for xyz in positions)
        min_p = [min(p[axis] for p in positions) for axis in range(3)]
        max_p = [max(p[axis] for p in positions) for axis in range(3)]
        use_short = max(indices) < 65535
        idx_bytes = b"".join(struct.pack("<H" if use_short else "<I", index) for index in indices)
        nrm_bytes = b"".join(struct.pack("<3f", *xyz) for xyz in normals)
        attributes = {
            "POSITION": accessor(view(pos_bytes, 34962), len(positions), "VEC3", 5126, min_p, max_p),
            "NORMAL": accessor(view(nrm_bytes, 34962), len(normals), "VEC3", 5126),
        }
        if prim.uvs and len(prim.uvs) == len(positions):
            uv_bytes = b"".join(struct.pack("<2f", u, v) for u, v in prim.uvs)
            attributes["TEXCOORD_0"] = accessor(view(uv_bytes, 34962), len(prim.uvs), "VEC2", 5126)
        index_accessor = accessor(view(idx_bytes, 34963), len(indices), "SCALAR", 5123 if use_short else 5125)
        image_index = None
        if prim.png:
            if prim.png not in image_of:
                image_view = view(prim.png)
                images.append({"bufferView": image_view, "mimeType": "image/png"})
                textures.append({"source": len(images) - 1})
                image_of[prim.png] = len(images) - 1
            image_index = image_of[prim.png]
        color = [float(channel) for channel in prim.base_color[:4]]
        if len(color) < 4:
            color = (color + [1.0, 1.0, 1.0, 1.0])[:4]
        alpha = prim.alpha if prim.alpha in ("opaque", "mask", "blend") else "opaque"
        cutoff = round(min(max(float(prim.cutoff), 0.0), 1.0), 4)
        key = (image_index, alpha, round(cutoff, 4), bool(prim.double_sided), tuple(round(channel, 4) for channel in color))
        if key not in material_of:
            pbr = {
                "baseColorFactor": color,
                "metallicFactor": 0,
                "roughnessFactor": 0.85 if image_index is not None else 0.9,
            }
            if image_index is not None:
                pbr["baseColorTexture"] = {"index": image_index}
            material = {"doubleSided": bool(prim.double_sided), "pbrMetallicRoughness": pbr}
            if alpha == "mask":
                material["alphaMode"] = "MASK"
                material["alphaCutoff"] = cutoff
            elif alpha == "blend":
                material["alphaMode"] = "BLEND"
            materials.append(material)
            material_of[key] = len(materials) - 1
        gl_primitives.append(
            {"attributes": attributes, "indices": index_accessor, "material": material_of[key]}
        )

    if not gl_primitives:
        raise ValueError("the model has no triangles")
    document = {
        "asset": {"version": "2.0", "generator": "bancwright"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": gl_primitives}],
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "materials": materials,
    }
    if images:
        document["images"] = images
        document["textures"] = textures
    text = json.dumps(document, separators=(",", ":")).encode()
    while len(text) % 4:
        text += b" "
    while len(bin_blob) % 4:
        bin_blob.append(0)
    document["buffers"][0]["byteLength"] = len(bin_blob)
    text = json.dumps(document, separators=(",", ":")).encode()
    while len(text) % 4:
        text += b" "
    chunks = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(bin_blob), b"BIN\0") + bytes(bin_blob)
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks
