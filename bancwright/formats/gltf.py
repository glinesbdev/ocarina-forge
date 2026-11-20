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
    lod: int = 0
    name: str = ""
    unlit: bool = False
    kind: str = ""
    colors: list[tuple[float, float, float]] | None = None
    uvs2: list[tuple[float, float]] | None = None
    shades: list[tuple[float, float]] | None = None
    marks: list[tuple[float, float]] | None = None
    extras: dict | None = None


def limit_rgba(width: int, height: int, rgba: bytes, limit: int = 1024) -> tuple[int, int, bytes]:
    """Box-filter an image so the long edge is at most ``limit`` pixels."""
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return width, height, rgba
    if width <= limit and height <= limit:
        return width, height, rgba
    scale = limit / max(width, height)
    nw = max(1, int(width * scale))
    nh = max(1, int(height * scale))
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0 = int(y / scale)
        y1 = min(height, max(y0 + 1, int((y + 1) / scale)))
        for x in range(nw):
            x0 = int(x / scale)
            x1 = min(width, max(x0 + 1, int((x + 1) / scale)))
            acc = [0, 0, 0, 0]
            count = 0
            for yy in range(y0, y1):
                row = yy * width
                for xx in range(x0, x1):
                    pixel = (row + xx) * 4
                    acc[0] += rgba[pixel]
                    acc[1] += rgba[pixel + 1]
                    acc[2] += rgba[pixel + 2]
                    acc[3] += rgba[pixel + 3]
                    count += 1
            if count <= 0:
                continue
            dest = (y * nw + x) * 4
            for channel in range(4):
                out[dest + channel] = acc[channel] // count
    return nw, nh, bytes(out)


def encode_albedo(width: int, height: int, rgba: bytes, alpha: str = "opaque") -> tuple[bytes, str]:
    """Cap the long edge at 1024. Opaque images become JPEG; cutouts stay PNG."""
    width, height, rgba = limit_rgba(width, height, rgba, 1024)
    opaque = alpha == "opaque" and not any(rgba[index] < 250 for index in range(3, len(rgba), 4))
    if opaque:
        jpeg = _jpeg(width, height, rgba)
        if jpeg:
            return jpeg, "image/jpeg"
    return png_rgba(width, height, rgba), "image/png"


def _jpeg(width: int, height: int, rgba: bytes) -> bytes | None:
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        return None
    image = Image.frombytes("RGBA", (width, height), rgba).convert("RGB")
    out = BytesIO()
    image.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue()


def cluster_lod(
    positions: list[tuple[float, float, float]],
    indices: list[int],
    uvs: list[tuple[float, float]] | None,
    ratio: float = 0.35,
) -> tuple[list, list, list | None] | None:
    """A far mesh: quantize vertices onto a grid and drop degenerate triangles.

    The grid gets coarser as the source gets larger, so a view of the whole
    forest is not still drawing the near mesh.
    """
    count = len(indices)
    if count < 900 or len(positions) < 40:
        return None
    if count > 80000:
        ratio = min(ratio, 0.06)
    elif count > 15000:
        ratio = min(ratio, 0.12)
    elif count > 4000:
        ratio = min(ratio, 0.2)
    xs = [point[0] for point in positions]
    ys = [point[1] for point in positions]
    zs = [point[2] for point in positions]
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-4)
    cells = max(2, int(round(max(16, len(positions) * ratio) ** (1 / 3))))
    step = span / cells
    origin = (min(xs), min(ys), min(zs))
    vertex_of: dict[tuple[int, int, int], int] = {}
    remap: dict[int, int] = {}
    new_pos: list[tuple[float, float, float]] = []
    new_uv: list[tuple[float, float]] = []
    for index, point in enumerate(positions):
        cell = (
            int((point[0] - origin[0]) / step),
            int((point[1] - origin[1]) / step),
            int((point[2] - origin[2]) / step),
        )
        slot = vertex_of.get(cell)
        if slot is None:
            slot = len(new_pos)
            vertex_of[cell] = slot
            new_pos.append(point)
            if uvs is not None and index < len(uvs):
                new_uv.append(uvs[index])
        remap[index] = slot
    new_idx: list[int] = []
    for index in range(0, len(indices) - 2, 3):
        a = remap.get(indices[index])
        b = remap.get(indices[index + 1])
        c = remap.get(indices[index + 2])
        if a is None or b is None or c is None or a == b or b == c or a == c:
            continue
        new_idx.extend((a, b, c))
    if len(new_idx) < 3 or len(new_idx) >= len(indices) * 0.85:
        return None
    return new_pos, new_idx, new_uv if uvs is not None else None


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
    grouped: dict[int, list] = {}
    image_of: dict[bytes, int] = {}
    material_of: dict[tuple, int] = {}
    used_unlit = False

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
        if prim.uvs2 and len(prim.uvs2) == len(positions):
            uv_bytes = b"".join(struct.pack("<2f", u, v) for u, v in prim.uvs2)
            attributes["TEXCOORD_1"] = accessor(view(uv_bytes, 34962), len(prim.uvs2), "VEC2", 5126)
        if prim.shades and len(prim.shades) == len(positions):
            shade_bytes = b"".join(struct.pack("<2f", weight, shade) for weight, shade in prim.shades)
            attributes["TEXCOORD_2"] = accessor(view(shade_bytes, 34962), len(prim.shades), "VEC2", 5126)
        if prim.marks and len(prim.marks) == len(positions):
            mark_bytes = b"".join(struct.pack("<2f", hue, folder) for hue, folder in prim.marks)
            attributes["TEXCOORD_3"] = accessor(view(mark_bytes, 34962), len(prim.marks), "VEC2", 5126)
        if prim.colors and len(prim.colors) == len(positions):
            color_bytes = b"".join(struct.pack("<3f", red, green, blue) for red, green, blue in prim.colors)
            attributes["COLOR_0"] = accessor(view(color_bytes, 34962), len(prim.colors), "VEC3", 5126)
        index_accessor = accessor(view(idx_bytes, 34963), len(indices), "SCALAR", 5123 if use_short else 5125)
        image_index = None
        if prim.png:
            if prim.png not in image_of:
                image_view = view(prim.png)
                mime = "image/jpeg" if prim.png[:2] == b"\xff\xd8" else "image/png"
                images.append({"bufferView": image_view, "mimeType": mime})
                textures.append({"sampler": 0, "source": len(images) - 1})
                image_of[prim.png] = len(images) - 1
            image_index = image_of[prim.png]
        color = [float(channel) for channel in prim.base_color[:4]]
        if len(color) < 4:
            color = (color + [1.0, 1.0, 1.0, 1.0])[:4]
        alpha = prim.alpha if prim.alpha in ("opaque", "mask", "blend") else "opaque"
        cutoff = round(min(max(float(prim.cutoff), 0.0), 1.0), 4)
        kind = prim.kind or ""
        extras = {key: value for key, value in (prim.extras or {}).items() if isinstance(key, str)}
        extra_key = tuple(sorted((key, repr(value)) for key, value in extras.items()))
        key = (
            image_index,
            alpha,
            round(cutoff, 4),
            bool(prim.double_sided),
            tuple(round(channel, 4) for channel in color),
            bool(prim.unlit),
            kind,
            extra_key,
        )
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
            if prim.unlit:
                material["extensions"] = {"KHR_materials_unlit": {}}
                used_unlit = True
            if kind or extras:
                material["extras"] = {}
                if kind:
                    material["name"] = kind
                    material["extras"]["banc"] = kind
                material["extras"].update(extras)
            materials.append(material)
            material_of[key] = len(materials) - 1
        label = prim.name or f"part{len(grouped)}"
        grouped.setdefault(1 if prim.lod else 0, []).append(
            (label, {"attributes": attributes, "indices": index_accessor, "material": material_of[key]})
        )

    if not grouped:
        raise ValueError("the model has no triangles")
    nodes = []
    meshes = []
    for lod in sorted(grouped):
        for label, primitive in grouped[lod]:
            meshes.append({"name": f"lod{lod}:{label}", "primitives": [primitive]})
            nodes.append({"name": f"lod{lod}:{label}", "mesh": len(meshes) - 1})
    document = {
        "asset": {"version": "2.0", "generator": "bancwright"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "materials": materials,
    }
    if images:
        document["images"] = images
        document["samplers"] = [{"wrapS": 10497, "wrapT": 10497}]
        document["textures"] = textures
    if used_unlit:
        document["extensionsUsed"] = ["KHR_materials_unlit"]
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
