"""A small glTF binary writer for positions, UVs, normals, and one base-color image."""

from __future__ import annotations

import array
import json
import struct
import sys
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
    normal_png: bytes | None = None
    emissive_png: bytes | None = None
    # Moss image when a real emission map already owns the emissive slot.
    moss_png: bytes | None = None
    # One ORM image: R occlusion, G roughness, B metal, A height (not sampled).
    orm_png: bytes | None = None
    orm1_png: bytes | None = None
    normal1_png: bytes | None = None


def _native():
    from ocarina_forge.extension import module

    return module()


def limit_rgba(width: int, height: int, rgba: bytes, limit: int = 1024) -> tuple[int, int, bytes]:
    """Box-filter an image so the long edge is at most ``limit`` pixels."""
    if width <= 0 or height <= 0 or limit <= 0 or len(rgba) < width * height * 4:
        return width, height, rgba
    if width <= limit and height <= limit:
        return width, height, rgba
    native = _native()
    if native is not None:
        try:
            nw, nh, out = native.limit_rgba(int(width), int(height), rgba, int(limit))
            nw, nh = int(nw), int(nh)
            if nw > 0 and nh > 0 and len(out) == nw * nh * 4:
                return nw, nh, bytes(out)
        except Exception:
            pass
    return _limit_rgba_py(width, height, rgba, limit)


def _limit_rgba_py(width: int, height: int, rgba: bytes, limit: int = 1024) -> tuple[int, int, bytes]:
    if width <= 0 or height <= 0 or limit <= 0 or len(rgba) < width * height * 4:
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
    colors: list | None = None,
) -> tuple | None:
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
    vertex_of: dict[tuple, int] = {}
    remap: dict[int, int] = {}
    new_pos: list[tuple[float, float, float]] = []
    new_uv: list[tuple[float, float]] = []
    new_color: list = []
    keep_colors = colors is not None and len(colors) == len(positions)
    for index, point in enumerate(positions):
        # The UV bin keeps a seam (0 beside 1, or a tiled chart) from welding
        # into the vertex that happened to share the position cell. The color
        # bin does the same for a moss edge and a bare edge in one cell.
        if uvs is not None and index < len(uvs):
            uv_bin = (int(round(uvs[index][0] * 4)), int(round(uvs[index][1] * 4)))
        else:
            uv_bin = (0, 0)
        if keep_colors:
            tone = colors[index]
            color_bin = tuple(int(round(float(tone[channel]) * 4)) for channel in range(min(4, len(tone))))
        else:
            color_bin = ()
        cell = (
            int((point[0] - origin[0]) / step),
            int((point[1] - origin[1]) / step),
            int((point[2] - origin[2]) / step),
            uv_bin[0],
            uv_bin[1],
            color_bin,
        )
        slot = vertex_of.get(cell)
        if slot is None:
            slot = len(new_pos)
            vertex_of[cell] = slot
            new_pos.append(point)
            if uvs is not None and index < len(uvs):
                new_uv.append(uvs[index])
            if keep_colors:
                new_color.append(colors[index])
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
    coarse_uv = new_uv if uvs is not None else None
    if keep_colors:
        return new_pos, new_idx, coarse_uv, new_color
    return new_pos, new_idx, coarse_uv


def _image_mime(blob: bytes) -> str:
    if blob[:4] == b"OCFG":
        return "image/ktx2"
    if blob[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/png"


def _texture_entry(source: int, mime: str) -> dict:
    entry = {"sampler": 0, "source": source}
    if mime == "image/ktx2":
        # three r170 loads KTX2 only through this extension, not from the mime alone.
        entry["extensions"] = {"KHR_texture_basisu": {"source": source}}
    return entry


def png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """RGBA PNG. The IDAT is zlib level 1.

    Level 6 matched level 1's size on a detailed image and took about twice
    as long. A compressor that only run-length-encodes zeros was faster and
    then wrote several megabytes for a tiled map, so the browser download
    would grow. Cutouts, normals, and ORM stay PNG. Opaque albedos are JPEG.
    """
    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        rows.extend(rgba[y * stride : (y + 1) * stride])
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(rows), 1)) + chunk(b"IEND", b"")


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


def _pack_f32(rows, width: int) -> bytes:
    """Little-endian f32. The crate walks the tuples. ``array`` is the fallback."""
    native = None
    try:
        from ocarina_forge.extension import module

        native = module()
    except Exception:
        native = None
    if native is not None and width:
        try:
            packed = native.pack_f32(rows, int(width))
        except Exception:
            packed = b""
        if len(packed) == len(rows) * width * 4:
            return bytes(packed)
    flat = array.array("f")
    for row in rows:
        for index in range(width):
            flat.append(float(row[index]))
    if sys.byteorder != "little":
        flat.byteswap()
    return flat.tobytes()


def _pack_indices(indices: list[int], use_short: bool) -> bytes:
    packed = array.array("H" if use_short else "I", indices)
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


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
        pos_bytes = _pack_f32(positions, 3)
        min_p = [min(p[axis] for p in positions) for axis in range(3)]
        max_p = [max(p[axis] for p in positions) for axis in range(3)]
        use_short = max(indices) < 65535
        idx_bytes = _pack_indices(indices, use_short)
        nrm_bytes = _pack_f32(normals, 3)
        attributes = {
            "POSITION": accessor(view(pos_bytes, 34962), len(positions), "VEC3", 5126, min_p, max_p),
            "NORMAL": accessor(view(nrm_bytes, 34962), len(normals), "VEC3", 5126),
        }
        if prim.uvs and len(prim.uvs) == len(positions):
            attributes["TEXCOORD_0"] = accessor(view(_pack_f32(prim.uvs, 2), 34962), len(prim.uvs), "VEC2", 5126)
        if prim.uvs2 and len(prim.uvs2) == len(positions):
            attributes["TEXCOORD_1"] = accessor(view(_pack_f32(prim.uvs2, 2), 34962), len(prim.uvs2), "VEC2", 5126)
        if prim.shades and len(prim.shades) == len(positions):
            attributes["TEXCOORD_2"] = accessor(view(_pack_f32(prim.shades, 2), 34962), len(prim.shades), "VEC2", 5126)
        if prim.marks and len(prim.marks) == len(positions):
            attributes["TEXCOORD_3"] = accessor(view(_pack_f32(prim.marks, 2), 34962), len(prim.marks), "VEC2", 5126)
        if prim.colors and len(prim.colors) == len(positions):
            wide = any(len(item) >= 4 for item in prim.colors)
            if wide:
                padded = [item if len(item) > 3 else (item[0], item[1], item[2], 1.0) for item in prim.colors]
                attributes["COLOR_0"] = accessor(view(_pack_f32(padded, 4), 34962), len(prim.colors), "VEC4", 5126)
            else:
                attributes["COLOR_0"] = accessor(view(_pack_f32(prim.colors, 3), 34962), len(prim.colors), "VEC3", 5126)
        index_accessor = accessor(view(idx_bytes, 34963), len(indices), "SCALAR", 5123 if use_short else 5125)
        def image_index_for(blob: bytes | None) -> int | None:
            if not blob:
                return None
            if blob not in image_of:
                image_view = view(blob)
                mime = _image_mime(blob)
                # The name is how a later meshopt rewrite finds this image. The
                # writer drops textures that no standard slot uses, and the
                # overlay maps live in extras.
                images.append({"name": f"img{len(images)}", "bufferView": image_view, "mimeType": mime})
                textures.append(_texture_entry(len(images) - 1, mime))
                image_of[blob] = len(images) - 1
            return image_of[blob]

        image_index = image_index_for(prim.png)
        normal_index = image_index_for(prim.normal_png)
        emissive_index = image_index_for(prim.emissive_png)
        moss_index = image_index_for(prim.moss_png)
        real_emissive = emissive_index
        if emissive_index is None and moss_index is not None:
            emissive_index = moss_index
            moss_index = None
        orm_index = image_index_for(prim.orm_png)
        orm1_index = image_index_for(prim.orm1_png)
        normal1_index = image_index_for(prim.normal1_png)
        color = [float(channel) for channel in prim.base_color[:4]]
        if len(color) < 4:
            color = (color + [1.0, 1.0, 1.0, 1.0])[:4]
        alpha = prim.alpha if prim.alpha in ("opaque", "mask", "blend") else "opaque"
        cutoff = round(min(max(float(prim.cutoff), 0.0), 1.0), 4)
        kind = prim.kind or ""
        extras = {key: value for key, value in (prim.extras or {}).items() if isinstance(key, str)}
        surface = {}
        if orm1_index is not None:
            surface["orm1"] = orm1_index
            surface["orm1Image"] = images[orm1_index]["name"]
        if normal1_index is not None:
            surface["normal1"] = normal1_index
            surface["normal1Image"] = images[normal1_index]["name"]
        if surface:
            extras = dict(extras)
            extras["surface"] = surface
        if moss_index is not None and extras.get("blend"):
            extras = dict(extras)
            blend = dict(extras["blend"])
            blend["moss"] = moss_index
            blend["mossImage"] = images[moss_index]["name"]
            extras["blend"] = blend
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
            normal_index,
            emissive_index,
            orm_index,
            orm1_index,
            normal1_index,
            moss_index,
        )
        if key not in material_of:
            pbr = {
                "baseColorFactor": color,
                "metallicFactor": 1 if orm_index is not None else 0,
                "roughnessFactor": 1 if orm_index is not None else (0.85 if image_index is not None else 0.9),
            }
            if image_index is not None:
                pbr["baseColorTexture"] = {"index": image_index}
            if orm_index is not None:
                pbr["metallicRoughnessTexture"] = {"index": orm_index}
            material = {"doubleSided": bool(prim.double_sided), "pbrMetallicRoughness": pbr}
            if orm_index is not None:
                material["occlusionTexture"] = {"index": orm_index}
            if normal_index is not None:
                material["normalTexture"] = {"index": normal_index}
            if emissive_index is not None:
                material["emissiveTexture"] = {"index": emissive_index}
                # Moss rides in this slot only when nothing else glows, and then
                # the factor stays 0. A real emission map uses factor 1, and the
                # moss image is extras.blend.moss instead.
                material["emissiveFactor"] = [1.0, 1.0, 1.0] if real_emissive is not None else [0.0, 0.0, 0.0]
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
        "asset": {"version": "2.0", "generator": "ocarina-forge"},
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
        # 9987 is LinearMipmapLinear. The compressed images carry that chain.
        # A sampler that omits minFilter makes three assign the same filter
        # after the upload, which samples black when the chain is missing.
        document["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
        document["textures"] = textures
    extensions = []
    if used_unlit:
        extensions.append("KHR_materials_unlit")
    if any(image.get("mimeType") == "image/ktx2" for image in images):
        extensions.append("KHR_texture_basisu")
    if extensions:
        document["extensionsUsed"] = extensions
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


# extras key → the image-name key written beside it.
_EXTRA_IMAGES = (
    ("surface", "orm1", "orm1Image"),
    ("surface", "normal1", "normal1Image"),
    ("blend", "moss", "mossImage"),
)


def split_glb(glb: bytes) -> tuple[dict, bytes]:
    """JSON document and the BIN chunk. The BIN payload keeps its padding."""
    if len(glb) < 20 or glb[:4] != b"glTF":
        raise ValueError("not a glb")
    version, length = struct.unpack_from("<II", glb, 4)
    if version != 2 or length != len(glb):
        raise ValueError("glb header length does not match the file")
    offset = 12
    document = None
    binary = b""
    while offset + 8 <= len(glb):
        chunk_len, chunk_type = struct.unpack_from("<I4s", glb, offset)
        offset += 8
        if chunk_len < 0 or offset + chunk_len > len(glb):
            raise ValueError("glb chunk runs past the end of the file")
        payload = glb[offset : offset + chunk_len]
        offset += chunk_len
        if chunk_type == b"JSON":
            document = json.loads(payload)
        elif chunk_type == b"BIN\0":
            binary = payload
    if not isinstance(document, dict):
        raise ValueError("glb has no JSON chunk")
    return document, binary


def join_glb(document: dict, binary: bytes) -> bytes:
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * ((4 - len(text) % 4) % 4)
    chunks = struct.pack("<I4s", len(text), b"JSON") + text
    if binary:
        if len(binary) % 4:
            binary += bytes(4 - len(binary) % 4)
        chunks += struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def _texture_for_image(document: dict, image_index: int) -> int:
    textures = document.setdefault("textures", [])
    for slot, texture in enumerate(textures):
        if isinstance(texture, dict) and texture.get("source") == image_index:
            return slot
    samplers = document.setdefault("samplers", [])
    if not samplers:
        samplers.append({"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497})
    textures.append({"sampler": 0, "source": image_index})
    return len(textures) - 1


def _image_index(document: dict, name: str | None, fallback: object) -> int | None:
    images = document.get("images") or []
    if isinstance(name, str) and name:
        for index, image in enumerate(images):
            if isinstance(image, dict) and image.get("name") == name:
                return index
    if isinstance(fallback, int) and 0 <= fallback < len(images):
        # The converter stores the image index. A packed file still has that
        # image, even when no texture points at it.
        textures = document.get("textures") or []
        if not any(isinstance(item, dict) and item.get("source") == fallback for item in textures):
            return fallback
        if 0 <= fallback < len(textures):
            texture = textures[fallback]
            if isinstance(texture, dict) and texture.get("source") == fallback:
                return fallback
    return None


def rebind_extra_textures(glb: bytes) -> bytes:
    """Point overlay extras at textures that exist.

    meshopt writes a texture only when a base color, normal, ORM, or emissive
    slot uses it. ``surface.orm1``, ``surface.normal1``, and ``blend.moss``
    are image indices in extras. three.js then reads ``textures[index].extensions``
    and throws when that slot was dropped. The image name is the stable id;
    the number becomes the texture index a loader can request.
    """
    try:
        document, binary = split_glb(glb)
    except (ValueError, json.JSONDecodeError, struct.error):
        return glb
    changed = False
    for material in document.get("materials") or []:
        if not isinstance(material, dict):
            continue
        extras = material.get("extras")
        if not isinstance(extras, dict):
            continue
        for group_name, index_key, name_key in _EXTRA_IMAGES:
            group = extras.get(group_name)
            if not isinstance(group, dict) or index_key not in group and name_key not in group:
                continue
            image_index = _image_index(document, group.get(name_key) if isinstance(group.get(name_key), str) else None, group.get(index_key))
            if image_index is None:
                if index_key in group:
                    group.pop(index_key, None)
                    changed = True
                continue
            before = len(document.get("textures") or [])
            slot = _texture_for_image(document, image_index)
            if len(document.get("textures") or []) != before or group.get(index_key) != slot:
                group[index_key] = slot
                changed = True
            image = document["images"][image_index]
            image_name = image.get("name") if isinstance(image, dict) else None
            if image_name and group.get(name_key) != image_name:
                group[name_key] = image_name
                changed = True
    if not changed:
        return glb
    buffers = document.get("buffers")
    if isinstance(buffers, list) and buffers and isinstance(buffers[0], dict) and "byteLength" not in buffers[0]:
        buffers[0]["byteLength"] = len(binary)
    return join_glb(document, binary)


def _expect_index(issues: list[str], where: str, index: object, count: int) -> None:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= count:
        issues.append(f"{where} index {index!r} is outside 0..{count - 1}")


def glb_issues(glb: bytes) -> list[str]:
    """Index errors a glTF loader trips over. An empty list is safe to parse.

    This is the check the fixture glbs have to pass. It covers the
    ``reading 'extensions'`` crash: a material or an extras overlay map
    citing a texture that is not in the file.
    """
    issues: list[str] = []
    try:
        document, binary = split_glb(glb)
    except (ValueError, json.JSONDecodeError, struct.error) as exc:
        return [str(exc)]
    if document.get("asset", {}).get("version") != "2.0":
        issues.append("asset.version is not 2.0")
    buffers = document.get("buffers") or []
    views = document.get("bufferViews") or []
    accessors = document.get("accessors") or []
    images = document.get("images") or []
    textures = document.get("textures") or []
    materials = document.get("materials") or []
    meshes = document.get("meshes") or []
    nodes = document.get("nodes") or []
    if buffers and isinstance(buffers[0], dict):
        declared = buffers[0].get("byteLength")
        if isinstance(declared, int) and declared > len(binary):
            issues.append(f"buffer 0 byteLength {declared} is longer than the BIN chunk")
    meshopt = "EXT_meshopt_compression" in (document.get("extensionsUsed") or [])
    for view_index, view in enumerate(views):
        if not isinstance(view, dict):
            issues.append(f"bufferView {view_index} is not an object")
            continue
        extension = (view.get("extensions") or {}).get("EXT_meshopt_compression") if meshopt else None
        if isinstance(extension, dict):
            for key in ("buffer", "byteLength", "byteStride", "count", "mode"):
                if key not in extension:
                    issues.append(f"bufferView {view_index} meshopt extension is missing {key}")
            _expect_index(issues, f"bufferView {view_index} meshopt buffer", extension.get("buffer"), len(buffers) or 1)
            continue
        _expect_index(issues, f"bufferView {view_index} buffer", view.get("buffer", 0), len(buffers) or 1)
        offset = view.get("byteOffset") or 0
        length = view.get("byteLength") or 0
        if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length < 0 or offset + length > len(binary):
            issues.append(f"bufferView {view_index} is outside the BIN chunk")
    for accessor_index, accessor in enumerate(accessors):
        if not isinstance(accessor, dict):
            issues.append(f"accessor {accessor_index} is not an object")
            continue
        if "bufferView" in accessor:
            _expect_index(issues, f"accessor {accessor_index} bufferView", accessor.get("bufferView"), len(views))
        count = accessor.get("count")
        if not isinstance(count, int) or count < 1:
            issues.append(f"accessor {accessor_index} has no count")
    for image_index, image in enumerate(images):
        if isinstance(image, dict) and "bufferView" in image:
            _expect_index(issues, f"image {image_index} bufferView", image.get("bufferView"), len(views))
    for texture_index, texture in enumerate(textures):
        if not isinstance(texture, dict):
            issues.append(f"texture {texture_index} is not an object")
            continue
        source = texture.get("source")
        basis = (texture.get("extensions") or {}).get("KHR_texture_basisu")
        if isinstance(basis, dict) and basis.get("source") is not None:
            source = basis.get("source")
        _expect_index(issues, f"texture {texture_index} source", source, len(images))
        if "sampler" in texture:
            _expect_index(issues, f"texture {texture_index} sampler", texture.get("sampler"), len(document.get("samplers") or []))

    def walk_texture(where: str, info: object) -> None:
        if not isinstance(info, dict) or "index" not in info:
            return
        _expect_index(issues, where, info.get("index"), len(textures))

    for material_index, material in enumerate(materials):
        if not isinstance(material, dict):
            issues.append(f"material {material_index} is not an object")
            continue
        pbr = material.get("pbrMetallicRoughness") or {}
        if isinstance(pbr, dict):
            walk_texture(f"material {material_index} baseColorTexture", pbr.get("baseColorTexture"))
            walk_texture(f"material {material_index} metallicRoughnessTexture", pbr.get("metallicRoughnessTexture"))
        walk_texture(f"material {material_index} normalTexture", material.get("normalTexture"))
        walk_texture(f"material {material_index} occlusionTexture", material.get("occlusionTexture"))
        walk_texture(f"material {material_index} emissiveTexture", material.get("emissiveTexture"))
        extras = material.get("extras") if isinstance(material.get("extras"), dict) else {}
        for group_name, index_key, name_key in _EXTRA_IMAGES:
            group = extras.get(group_name) if isinstance(extras, dict) else None
            if not isinstance(group, dict) or index_key not in group:
                continue
            where = f"material {material_index} extras.{group_name}.{index_key}"
            _expect_index(issues, where, group.get(index_key), len(textures))
            slot = group.get(index_key)
            if isinstance(slot, int) and 0 <= slot < len(textures):
                source = textures[slot].get("source") if isinstance(textures[slot], dict) else None
                named = group.get(name_key)
                if isinstance(named, str):
                    matches = [index for index, image in enumerate(images) if isinstance(image, dict) and image.get("name") == named]
                    if not matches:
                        issues.append(f"{where} names {named}, which is not an image")
                    elif source not in matches:
                        issues.append(f"{where} points at image {source}, not {named}")
    for mesh_index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            issues.append(f"mesh {mesh_index} is not an object")
            continue
        for prim_index, primitive in enumerate(mesh.get("primitives") or []):
            if not isinstance(primitive, dict):
                issues.append(f"mesh {mesh_index} primitive {prim_index} is not an object")
                continue
            if "material" in primitive:
                _expect_index(issues, f"mesh {mesh_index} primitive {prim_index} material", primitive.get("material"), len(materials))
            if "indices" in primitive:
                _expect_index(issues, f"mesh {mesh_index} primitive {prim_index} indices", primitive.get("indices"), len(accessors))
            attributes = primitive.get("attributes") or {}
            if isinstance(attributes, dict):
                for name, accessor_index in attributes.items():
                    _expect_index(issues, f"mesh {mesh_index} primitive {prim_index} {name}", accessor_index, len(accessors))
    for node_index, node in enumerate(nodes):
        if isinstance(node, dict) and "mesh" in node:
            _expect_index(issues, f"node {node_index} mesh", node.get("mesh"), len(meshes))
        if isinstance(node, dict):
            for child in node.get("children") or []:
                _expect_index(issues, f"node {node_index} child", child, len(nodes))
    for scene_index, scene in enumerate(document.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        for node_index in scene.get("nodes") or []:
            _expect_index(issues, f"scene {scene_index} node", node_index, len(nodes))
    return issues
