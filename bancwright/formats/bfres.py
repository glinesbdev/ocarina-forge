"""Read Switch BFRES meshes and their embedded BNTX images.

Field order follows the public Switch BFRES description and the loaders in
KillzXGaming/BfresLibrary (MIT): version 8, 9, and 10 headers, FMDL, FVTX,
FSHP, FMAT, and the GPU buffer. Vertex formats are the Switch attribute ids
from that library. This file is an original reader, not a copy of the C#.

Smooth-skinned vertices are already stored in model space, so they are the
bind pose. A shape with no per-vertex skin (skin count 0) is moved by its
single bone. Skeletal animation is not read.

`sample_bfres` writes a tiny file the reader understands. It is synthetic.
Nothing in it comes from a game.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from bancwright.formats.bntx import BntxError, Texture, textures as read_bntx
from bancwright.formats.bntx import write_rgba


class FresError(ValueError):
    pass


# Big-endian ushort ids. See BfresLibrary VertexAttrib.SwitchAttribFormat.
_F32 = 0x0516
_F32_2 = 0x0517
_F32_3 = 0x0518
_F32_4 = 0x0519
_F16 = 0x050A
_F16_2 = 0x0512
_F16_4 = 0x0515
_U8 = 0x0102
_U8_2 = 0x0109
_U8_4 = 0x010B
_S8_4 = 0x020B
_U16_2 = 0x0112
_S16_2 = 0x0212
_SN10 = 0x020E
_UN10 = 0x000B


@dataclass
class Part:
    name: str
    positions: list[tuple[float, float, float]]
    indices: list[int]
    uvs: list[tuple[float, float]] | None
    texture: str


@dataclass
class Model:
    name: str
    parts: list[Part] = field(default_factory=list)
    images: dict[str, Texture] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def parse(data: bytes) -> list[Model]:
    return _File(data).models()


class _File:
    def __init__(self, data: bytes):
        if len(data) < 0x100 or data[:4] != b"FRES":
            raise FresError("not a BFRES file")
        if data[4:8] != b"    ":
            raise FresError("this is not a Switch BFRES (expected four spaces after FRES)")
        if data[0x0C:0x0E] != b"\xff\xfe":
            raise FresError("only little-endian BFRES is supported")
        self.data = data
        version = self.u32(0x08)
        self.major = (version >> 16) & 0xFFFF
        self.file_size = self.u32(0x1C)
        if self.major < 4 or self.major > 12:
            raise FresError(f"BFRES version {self.major} is not one this viewer reads")
        anim = 0x58 if self.major >= 9 else 0x38
        info = self.u64(anim + 88)
        self.buffer_base = self.u64(info + 8) if info else 0
        self.ext_values = self.u64(anim + 96)
        self.ext_dict = self.u64(anim + 104)
        self.model_values = self.u64(0x28)
        self.model_dict = self.u64(0x30)
        flag_at = 0xEE if self.major >= 9 else 0xCA
        self.flag = data[flag_at] if len(data) > flag_at else 0
        if self.flag & 0x08:
            self.buffer_base = self.file_size + 288
        if self.buffer_base and self.buffer_base >= len(data):
            raise FresError(
                "the vertex buffer is an external GPU section past the end of this file "
                f"(flag 0x{self.flag:02X}, buffer at {self.buffer_base:#x})"
            )

    def u16(self, at: int) -> int:
        return struct.unpack_from("<H", self.data, at)[0]

    def i16(self, at: int) -> int:
        return struct.unpack_from("<h", self.data, at)[0]

    def u32(self, at: int) -> int:
        return struct.unpack_from("<I", self.data, at)[0]

    def i32(self, at: int) -> int:
        return struct.unpack_from("<i", self.data, at)[0]

    def u64(self, at: int) -> int:
        if at < 0 or at + 8 > len(self.data):
            raise FresError(f"offset {at:#x} is outside the file")
        return struct.unpack_from("<Q", self.data, at)[0]

    def f32(self, at: int) -> float:
        return struct.unpack_from("<f", self.data, at)[0]

    def string(self, at: int) -> str:
        if at <= 0 or at + 2 > len(self.data):
            return ""
        end = self.data.find(b"\0", at + 2, at + 2 + 1024)
        if end < 0:
            return ""
        return self.data[at + 2 : end].decode("utf-8", "replace")

    def models(self) -> list[Model]:
        keys = self.dict_keys(self.model_dict)
        if not keys or not self.model_values:
            raise FresError("the BFRES has no models")
        images = self._images()
        found = []
        at = self.model_values
        for key in keys:
            model, at = self._model(at, key, images)
            found.append(model)
        if not any(model.parts for model in found):
            raise FresError("the BFRES models have no triangles")
        return found

    def dict_keys(self, at: int) -> list[str]:
        if not at:
            return []
        count = self.i32(at + 4)
        if count < 0 or count > 100000:
            raise FresError("a name dictionary has a count this reader will not walk")
        keys = []
        for index in range(1, count + 1):
            node = at + 8 + index * 16
            keys.append(self.string(self.u64(node + 8)))
        return keys

    def _images(self) -> dict[str, Texture]:
        images: dict[str, Texture] = {}
        notes: list[str] = []
        keys = self.dict_keys(self.ext_dict)
        at = self.ext_values
        for key in keys:
            if not at or at + 16 > len(self.data):
                break
            offset = self.u64(at)
            size = self.u64(at + 8)
            at += 16
            blob = self.data[offset : offset + size] if offset and size else b""
            if not blob.startswith(b"BNTX"):
                continue
            try:
                for image in read_bntx(blob):
                    images[image.name] = image
            except BntxError as exc:
                notes.append(f"{key or 'embedded texture'}: {exc}")
        self._image_notes = notes
        return images

    def _model(self, at: int, fallback: str, images: dict[str, Texture]) -> tuple[Model, int]:
        if self.data[at : at + 4] != b"FMDL":
            raise FresError(f"expected a model at {at:#x}")
        cursor = at + 4
        if self.major >= 9:
            cursor += 4
        else:
            cursor += 12
        name = self.string(self.u64(cursor))
        cursor += 8
        cursor += 8  # path
        skel = self.u64(cursor)
        cursor += 8
        cursor += 8  # vertex-buffer array; each shape points at its own FVTX
        shape_values = self.u64(cursor)
        cursor += 8
        shape_dict = self.u64(cursor)
        cursor += 8
        if self.major == 9:
            mat_values = self.u64(cursor)
            cursor += 8
            maybe = self.u64(cursor)
            cursor += 8
            mat_dict = self.u64(cursor)
            cursor += 8
            if not mat_dict:
                mat_dict = maybe
        else:
            mat_values = self.u64(cursor)
            cursor += 8
            mat_dict = self.u64(cursor)
            cursor += 8
            if self.major >= 10:
                cursor += 8
        cursor += 16  # user data
        cursor += 8  # user pointer
        cursor += 6  # vertex, shape, material counts
        if self.major >= 9:
            cursor += 2 + 2 + 2 + 4
        else:
            cursor += 2 + 4 + 4
        end = cursor
        bones = self._bones(skel) if skel else []
        materials = self._materials(mat_values, mat_dict)
        shape_names = self.dict_keys(shape_dict)
        parts: list[Part] = []
        shape_at = shape_values
        for index, shape_name in enumerate(shape_names):
            part, shape_at = self._shape(shape_at, shape_name or f"shape{index}", bones, materials)
            if part is not None:
                parts.append(part)
        model = Model(name or fallback, parts, images, list(getattr(self, "_image_notes", [])))
        if not parts:
            model.notes.append(f"{model.name or fallback}: no drawable shape")
        return model, end

    def _bones(self, at: int) -> list[tuple[int, tuple, tuple, tuple]]:
        if at + 4 > len(self.data) or self.data[at : at + 4] != b"FSKL":
            return []
        cursor = at + 4
        if self.major >= 9:
            cursor += 4
        else:
            cursor += 12
        dict_at = self.u64(cursor)
        cursor += 8
        array_at = self.u64(cursor)
        names = self.dict_keys(dict_at)
        bones = []
        cursor = array_at
        for _name in names:
            if not cursor:
                break
            cursor += 8  # name
            cursor += 16  # user-data offsets
            if self.major > 9:
                cursor += 8
            elif self.major in (8, 9):
                cursor += 16
            cursor += 2  # index
            parent = self.i16(cursor)
            cursor += 2
            cursor += 8  # smooth, rigid, billboard, user count
            cursor += 4  # flags
            scale = (self.f32(cursor), self.f32(cursor + 4), self.f32(cursor + 8))
            cursor += 12
            rot = (self.f32(cursor), self.f32(cursor + 4), self.f32(cursor + 8), self.f32(cursor + 12))
            cursor += 16
            pos = (self.f32(cursor), self.f32(cursor + 4), self.f32(cursor + 8))
            cursor += 12
            bones.append((parent, pos, rot, scale))
        return bones

    def _materials(self, values: int, dictionary: int) -> list[list[str]]:
        names = self.dict_keys(dictionary)
        at = values
        found = []
        for _name in names:
            if not at or self.data[at : at + 4] != b"FMAT":
                found.append([])
                continue
            cursor = at + 4
            if self.major >= 9:
                cursor += 4
            else:
                cursor += 12
            cursor += 8  # material name
            if self.major >= 10:
                texture_at, texture_count, cursor = self._material_v10(cursor)
            else:
                texture_at, texture_count, cursor = self._material_v9(cursor)
            tex_names = []
            for index in range(texture_count):
                if texture_at and texture_at + (index + 1) * 8 <= len(self.data):
                    tex_names.append(self.string(self.u64(texture_at + index * 8)))
            found.append(tex_names)
            at = cursor
        return found

    def _material_v9(self, cursor: int) -> tuple[int, int, int]:
        cursor += 16  # render info
        cursor += 8  # shader assign
        cursor += 8  # runtime texture pointers
        texture_at = self.u64(cursor)
        cursor += 8
        cursor += 8  # sampler array
        cursor += 16  # samplers
        cursor += 16  # shader params
        cursor += 8  # param data
        cursor += 16  # user data
        cursor += 8  # volatile
        cursor += 8  # user pointer
        cursor += 16  # slots
        if self.major < 9:
            cursor += 4
        cursor += 4  # section index and render-info count
        cursor += 1  # sampler count
        texture_count = self.data[cursor]
        cursor += 1
        cursor += 10  # five uint16 tails
        if self.major < 9:
            cursor += 4
        return texture_at, texture_count, cursor

    def _material_v10(self, cursor: int) -> tuple[int, int, int]:
        cursor += 80  # ShaderInfo header
        cursor += 8  # runtime texture pointers
        texture_at = self.u64(cursor)
        cursor += 8
        cursor += 8  # sampler array
        cursor += 16  # samplers
        cursor += 8 * 5  # render-info tables and shader-param offsets
        cursor += 8  # reserved
        cursor += 16  # user data
        cursor += 8 * 4  # volatile, user, sampler slot, texture slot
        cursor += 2  # section index
        cursor += 1  # sampler count
        texture_count = self.data[cursor]
        cursor += 1
        cursor += 2 + 2 + 2 + 2 + 4
        return texture_at, texture_count, cursor

    def _shape(self, at: int, name: str, bones, materials: list[list[str]]) -> tuple[Part | None, int]:
        if self.data[at : at + 4] != b"FSHP":
            raise FresError(f"expected a shape at {at:#x}")
        cursor = at + 4
        if self.major >= 9:
            cursor += 4
        else:
            cursor += 12
        cursor += 8  # name, already known from the dictionary
        vtx = self.u64(cursor)
        cursor += 8
        mesh_at = self.u64(cursor)
        cursor += 8
        cursor += 8  # skin indices
        cursor += 16  # key shapes
        cursor += 8  # bounds
        cursor += 8  # radius
        cursor += 8  # user pointer
        if self.major < 9:
            cursor += 4
        cursor += 2  # section
        material_index = self.u16(cursor)
        cursor += 2
        bone_index = self.u16(cursor)
        cursor += 2
        cursor += 2  # fvtx index
        cursor += 2  # skin bone count
        skin_count = self.data[cursor]
        cursor += 1
        mesh_count = self.data[cursor]
        cursor += 1
        cursor += 1  # key shapes
        cursor += 1  # target attributes
        cursor += 2 if self.major <= 3 or self.major >= 9 else 6
        end = cursor
        if not vtx or not mesh_count:
            return None, end
        fvtx = self._fvtx(vtx)
        lod = self._lod(mesh_at)
        if fvtx is None or lod is None:
            return None, end
        positions, uvs = self._vertices(fvtx, lod)
        if skin_count == 0 and bones:
            positions = _rigid(positions, bones, bone_index)
        tex = ""
        if 0 <= material_index < len(materials) and materials[material_index]:
            tex = base_color_name(materials[material_index])
        if not positions or not lod["indices"]:
            return None, end
        return Part(name, positions, lod["indices"], uvs, tex), end

    def _fvtx(self, at: int) -> dict | None:
        if at + 0x40 > len(self.data) or self.data[at : at + 4] != b"FVTX":
            raise FresError(f"expected a vertex buffer at {at:#x}")
        cursor = at + 4
        if self.major >= 9:
            cursor += 4
        else:
            cursor += 12
        attr_at = self.u64(cursor)
        cursor += 16  # attribute values, then the dictionary (names are also on each attribute)
        cursor += 8  # memory pool
        cursor += 8  # runtime buffer
        if self.major > 2:
            cursor += 8
        size_at = self.u64(cursor)
        cursor += 8
        stride_at = self.u64(cursor)
        cursor += 8
        cursor += 8
        buffer_off = self.i32(cursor)
        cursor += 4
        num_attr = self.data[cursor]
        cursor += 1
        num_buf = self.data[cursor]
        cursor += 1
        cursor += 2
        count = self.u32(cursor)
        cursor += 4
        cursor += 2  # skin count
        align = self.u16(cursor) if self.major >= 10 else 1
        if align <= 0:
            align = 1
        if num_buf <= 0 or num_buf > 16 or count > 5_000_000:
            raise FresError("vertex buffer counts are not usable")
        attributes = []
        pos = attr_at
        for _index in range(num_attr):
            attributes.append(
                {
                    "name": self.string(self.u64(pos)),
                    "format": struct.unpack_from(">H", self.data, pos + 8)[0],
                    "offset": self.u16(pos + 12),
                    "buffer": self.u16(pos + 14),
                }
            )
            pos += 16
        strides = [self.u32(stride_at + index * 16) for index in range(num_buf)]
        sizes = [self.u32(size_at + index * 16) for index in range(num_buf)]
        buffers = []
        data_at = self.buffer_base + buffer_off
        for index in range(num_buf):
            data_at = (data_at + align - 1) & ~(align - 1)
            size = sizes[index]
            if data_at < 0 or size < 0 or data_at + size > len(self.data):
                raise FresError(
                    "a vertex buffer does not fit in this file. "
                    "The mesh may be stored in a GPU section or a .bfres.mc stream this viewer does not read."
                )
            buffers.append(self.data[data_at : data_at + size])
            data_at += size
        return {"count": count, "attributes": attributes, "strides": strides, "buffers": buffers}

    def _lod(self, at: int) -> dict | None:
        if not at:
            return None
        sub_at = self.u64(at)
        size_at = self.u64(at + 24)
        face_off = self.u32(at + 32)
        primitive = self.u32(at + 36)
        index_format = self.u32(at + 40)
        index_count = self.u32(at + 44)
        first = self.u32(at + 48)
        sub_count = self.u16(at + 52)
        size = self.u32(size_at) if size_at else 0
        start = self.buffer_base + face_off
        if start < 0 or size < 0 or start + size > len(self.data):
            raise FresError("the index buffer does not fit in this file")
        blob = self.data[start : start + size]
        width = {0: 1, 1: 2, 2: 4}.get(index_format)
        if width is None:
            raise FresError(f"index format {index_format} is not decoded")
        ranges = []
        if sub_count and sub_at:
            for index in range(sub_count):
                offset = self.u32(sub_at + index * 8)
                count = self.u32(sub_at + index * 8 + 4)
                ranges.append((offset, count))
        else:
            ranges.append((0, index_count))
        indices: list[int] = []
        for offset, count in ranges:
            for item in range(count):
                pos = offset + item * width
                if pos + width > len(blob):
                    break
                if width == 1:
                    value = blob[pos]
                elif width == 2:
                    value = struct.unpack_from("<H", blob, pos)[0]
                else:
                    value = struct.unpack_from("<I", blob, pos)[0]
                indices.append(value + first)
        triangles = _triangles(indices, primitive)
        return {"indices": triangles}

    def _vertices(self, fvtx: dict, lod: dict) -> tuple[list[tuple[float, float, float]], list[tuple[float, float]] | None]:
        position = _attr(fvtx["attributes"], ("_p0", "p0", "_p"))
        uv = _attr(fvtx["attributes"], ("_u0", "u0", "_u"))
        if position is None:
            raise FresError("the shape has no position attribute (_p0)")
        needed = set(lod["indices"])
        count = fvtx["count"]
        positions = []
        uvs = [] if uv else None
        for index in range(count):
            positions.append(_read_attr(fvtx, position, index)[:3])
            if uv is not None and uvs is not None:
                raw = _read_attr(fvtx, uv, index)
                uvs.append((raw[0], raw[1] if len(raw) > 1 else 0.0))
        if needed and max(needed) >= count:
            raise FresError("an index points past the vertex buffer")
        return positions, uvs


def _attr(attributes: list[dict], names: tuple[str, ...]) -> dict | None:
    for attribute in attributes:
        if attribute["name"] in names:
            return attribute
    return None


def _read_attr(fvtx: dict, attribute: dict, index: int) -> tuple[float, ...]:
    buffer_index = attribute["buffer"]
    if buffer_index >= len(fvtx["buffers"]):
        raise FresError(f"attribute {attribute['name']} names a missing buffer")
    stride = fvtx["strides"][buffer_index] or _format_size(attribute["format"])
    blob = fvtx["buffers"][buffer_index]
    at = index * stride + attribute["offset"]
    return _decode(blob, at, attribute["format"])


def _format_size(fmt: int) -> int:
    return {
        _U8: 1,
        _U8_2: 2,
        _F16: 2,
        _U8_4: 4,
        _S8_4: 4,
        _U16_2: 4,
        _S16_2: 4,
        _F16_2: 4,
        _F32: 4,
        _SN10: 4,
        _UN10: 4,
        _F16_4: 8,
        _F32_2: 8,
        _F32_3: 12,
        _F32_4: 16,
    }.get(fmt, 0)


def _decode(blob: bytes, at: int, fmt: int) -> tuple[float, ...]:
    size = _format_size(fmt)
    if size <= 0 or at + size > len(blob):
        raise FresError(f"vertex format 0x{fmt:04X} does not fit in its buffer")
    if fmt == _F32:
        return (struct.unpack_from("<f", blob, at)[0],)
    if fmt == _F32_2:
        return struct.unpack_from("<2f", blob, at)
    if fmt == _F32_3:
        return struct.unpack_from("<3f", blob, at)
    if fmt == _F32_4:
        return struct.unpack_from("<4f", blob, at)
    if fmt == _F16:
        return (struct.unpack_from("<e", blob, at)[0],)
    if fmt == _F16_2:
        return struct.unpack_from("<2e", blob, at)
    if fmt == _F16_4:
        return struct.unpack_from("<4e", blob, at)
    if fmt == _U8:
        return (blob[at] / 255.0,)
    if fmt == _U8_2:
        return (blob[at] / 255.0, blob[at + 1] / 255.0)
    if fmt in (_U8_4, _S8_4):
        values = []
        for byte in blob[at : at + 4]:
            values.append((byte if fmt == _U8_4 else struct.unpack("b", bytes([byte]))[0]) / (255.0 if fmt == _U8_4 else 127.0))
        return tuple(values)
    if fmt == _U16_2:
        a, b = struct.unpack_from("<2H", blob, at)
        return (a / 65535.0, b / 65535.0)
    if fmt == _S16_2:
        a, b = struct.unpack_from("<2h", blob, at)
        return (a / 32767.0, b / 32767.0)
    if fmt in (_SN10, _UN10):
        packed = struct.unpack_from("<I", blob, at)[0]
        parts = []
        for shift in (0, 10, 20):
            raw = (packed >> shift) & 0x3FF
            if fmt == _SN10 and raw & 0x200:
                raw -= 0x400
            parts.append(raw / (511.0 if fmt == _SN10 else 1023.0))
        return tuple(parts)
    raise FresError(f"vertex format 0x{fmt:04X} is not decoded")


def _triangles(indices: list[int], primitive: int) -> list[int]:
    if primitive == 4:
        out = []
        for index in range(len(indices) - 2):
            a, b, c = indices[index], indices[index + 1], indices[index + 2]
            if a == b or b == c or a == c:
                continue
            out.extend((a, c, b) if index % 2 else (a, b, c))
        return out
    if primitive != 3:
        raise FresError(f"primitive type {primitive} is not triangles")
    return indices[: len(indices) - (len(indices) % 3)]


def _rigid(positions, bones, bone_index: int) -> list[tuple[float, float, float]]:
    """Move a rigid shape by one bone's bind-pose matrix. Each local TRS is applied once."""
    if bone_index < 0 or bone_index >= len(bones):
        return positions
    local = [_trs(pos, rot, scale) for _parent, pos, rot, scale in bones]
    worlds: list[list[list[float]] | None] = [None] * len(bones)
    pending = set(range(len(bones)))
    for _pass in range(len(bones)):
        if not pending:
            break
        ready = []
        for index in pending:
            parent = bones[index][0]
            if parent == index or parent < 0 or parent >= len(bones):
                worlds[index] = local[index]
                ready.append(index)
            elif worlds[parent] is not None:
                worlds[index] = _mul(worlds[parent], local[index])
                ready.append(index)
        if not ready:
            break
        pending.difference_update(ready)
    for index in pending:
        worlds[index] = local[index]
    matrix = worlds[bone_index]
    return [_apply(matrix, point) for point in positions]


def _trs(pos, rot, scale) -> list[list[float]]:
    rx, ry, rz, rw = rot
    n = rx * rx + ry * ry + rz * rz + rw * rw
    if n < 1e-8:
        rx = ry = rz = 0.0
        rw = 1.0
        n = 1.0
    s = 2.0 / n
    xx, yy, zz = rx * rx * s, ry * ry * s, rz * rz * s
    xy, xz, yz = rx * ry * s, rx * rz * s, ry * rz * s
    wx, wy, wz = rw * rx * s, rw * ry * s, rw * rz * s
    r00, r01, r02 = (1 - yy - zz) * scale[0], (xy - wz) * scale[1], (xz + wy) * scale[2]
    r10, r11, r12 = (xy + wz) * scale[0], (1 - xx - zz) * scale[1], (yz - wx) * scale[2]
    r20, r21, r22 = (xz - wy) * scale[0], (yz + wx) * scale[1], (1 - xx - yy) * scale[2]
    return [
        [r00, r01, r02, pos[0]],
        [r10, r11, r12, pos[1]],
        [r20, r21, r22, pos[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _mul(a, b) -> list[list[float]]:
    out = [[0.0] * 4 for _ in range(4)]
    for row in range(4):
        for col in range(4):
            out[row][col] = sum(a[row][k] * b[k][col] for k in range(4))
    return out


def _apply(matrix, point) -> tuple[float, float, float]:
    x, y, z = point
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


_ALBEDO = {"alb", "albedo", "basecolor", "diffuse", "diff"}
_SKIP = {"nrm", "nml", "normal", "norm", "spm", "spc", "spec", "rgh", "rough", "emm", "emit", "mtl", "orm", "msk", "mask"}


def base_color_name(names: list[str]) -> str:
    """The material texture that should color the mesh.

    Real materials list a normal or mask beside the albedo. Names like
    ``Obj_Rock_A_Alb`` win over ``Obj_Rock_A_Nrm``. A material with one
    texture keeps that texture.
    """
    if not names:
        return ""

    def score(name: str) -> int:
        stem = name.lower().rsplit("/", 1)[-1]
        tokens: list[str] = []
        token: list[str] = []
        for char in stem:
            if char.isalnum():
                token.append(char)
            elif token:
                tokens.append("".join(token))
                token = []
        if token:
            tokens.append("".join(token))
        bag = set(tokens)
        if bag & _ALBEDO or any(item.startswith("alb") for item in bag):
            return 0
        if bag & _SKIP:
            return 2
        return 1

    return min(names, key=lambda name: (score(name), names.index(name)))


def sample_bfres(
    name: str,
    positions: list[tuple[float, float, float]],
    indices: list[int],
    uvs: list[tuple[float, float]],
    rgba: bytes,
    width: int,
    height: int,
    *,
    swizzled: bool = False,
    texture_name: str = "alb",
    embed_texture: bool = True,
) -> bytes:
    """A version-9 Switch BFRES with one textured triangle mesh. Synthetic."""
    if len(positions) != len(uvs):
        raise FresError("positions and uvs must match")
    bntx = write_rgba(texture_name, width, height, rgba, swizzled=swizzled) if embed_texture else b""
    stride = 20
    vertex = bytearray()
    for (x, y, z), (u, v) in zip(positions, uvs):
        vertex += struct.pack("<3f2f", x, y, z, u, v)
    index_blob = b"".join(struct.pack("<H", index) for index in indices)

    blob = bytearray(0x100)
    blob[:8] = b"FRES    "
    struct.pack_into("<I", blob, 0x08, 0x00090000)
    blob[0x0C:0x0E] = b"\xff\xfe"

    def align(n: int) -> None:
        while len(blob) % n:
            blob.append(0)

    def add_string(text: str) -> int:
        align(2)
        at = len(blob)
        raw = text.encode()
        blob.extend(struct.pack("<H", len(raw)) + raw + b"\0")
        align(2)
        return at

    def add_dict(key_at: list[int]) -> int:
        align(8)
        at = len(blob)
        blob.extend(struct.pack("<II", 0, len(key_at)))
        blob.extend(struct.pack("<IHHQ", 0xFFFFFFFF, 1, 1, 0))
        for key in key_at:
            blob.extend(struct.pack("<IHHQ", 0, 0, 0, key))
        return at

    model_name = add_string(name)
    shape_name = add_string("Body")
    mat_name = add_string("lambert")
    tex_name = add_string(texture_name)
    pos_name = add_string("_p0")
    uv_name = add_string("_u0")
    file_name = add_string(name)

    align(8)
    attr_at = len(blob)
    blob.extend(struct.pack("<Q", pos_name) + struct.pack(">H", _F32_3) + b"\0\0" + struct.pack("<HH", 0, 0))
    blob.extend(struct.pack("<Q", uv_name) + struct.pack(">H", _F32_2) + b"\0\0" + struct.pack("<HH", 12, 0))
    attr_dict = add_dict([pos_name, uv_name])

    align(8)
    stride_at = len(blob)
    blob.extend(struct.pack("<I", stride) + b"\0" * 12)
    size_at = len(blob)
    blob.extend(struct.pack("<II", len(vertex), 0) + b"\0" * 8)

    align(8)
    fvtx_at = len(blob)
    blob.extend(b"FVTX")
    blob.extend(struct.pack("<I", 0))
    blob.extend(struct.pack("<QQ", attr_at, attr_dict))
    blob.extend(struct.pack("<QQQ", 0, 0, 0))
    blob.extend(struct.pack("<QQ", size_at, stride_at))
    blob.extend(struct.pack("<Q", 0))
    fvtx_buffer_hole = len(blob)
    blob.extend(struct.pack("<i", 0))
    blob.extend(bytes((2, 1)))
    blob.extend(struct.pack("<H", 0))
    blob.extend(struct.pack("<I", len(positions)))
    blob.extend(struct.pack("<HH", 1, 1))

    align(8)
    sub_at = len(blob)
    blob.extend(struct.pack("<II", 0, len(indices)))
    index_info = len(blob)
    blob.extend(struct.pack("<II", len(index_blob), 0) + b"\0" * 40)

    align(8)
    mesh_at = len(blob)
    blob.extend(struct.pack("<QQQQ", sub_at, 0, 0, index_info))
    blob.extend(struct.pack("<IIIII", 0, 3, 1, len(indices), 0))
    blob.extend(struct.pack("<HH", 1, 0))

    align(8)
    shape_at = len(blob)
    blob.extend(b"FSHP")
    blob.extend(struct.pack("<I", 0))
    blob.extend(struct.pack("<Q", shape_name))
    blob.extend(struct.pack("<Q", fvtx_at))
    blob.extend(struct.pack("<Q", mesh_at))
    blob.extend(struct.pack("<Q", 0))
    blob.extend(struct.pack("<QQ", 0, 0))
    blob.extend(struct.pack("<QQQ", 0, 0, 0))
    blob.extend(struct.pack("<HHHH", 0, 0, 0, 0))
    blob.extend(struct.pack("<H", 0))
    blob.extend(bytes((1, 1, 0, 0)))
    blob.extend(b"\0\0")
    shape_dict = add_dict([shape_name])

    align(8)
    tex_array = len(blob)
    blob.extend(struct.pack("<Q", tex_name))
    mat_at = len(blob)
    blob.extend(b"FMAT")
    blob.extend(struct.pack("<I", 1))
    blob.extend(struct.pack("<Q", mat_name))
    blob.extend(struct.pack("<QQ", 0, 0))  # render info
    blob.extend(struct.pack("<Q", 0))  # shader assign
    blob.extend(struct.pack("<Q", 0))  # runtime textures
    blob.extend(struct.pack("<Q", tex_array))
    blob.extend(struct.pack("<Q", 0))  # sampler array
    blob.extend(struct.pack("<QQ", 0, 0))  # samplers
    blob.extend(struct.pack("<QQ", 0, 0))  # shader params
    blob.extend(struct.pack("<Q", 0))  # param data
    blob.extend(struct.pack("<QQ", 0, 0))  # user data
    blob.extend(struct.pack("<Q", 0))  # volatile
    blob.extend(struct.pack("<Q", 0))  # user pointer
    blob.extend(struct.pack("<QQ", 0, 0))  # slots
    blob.extend(struct.pack("<HH", 0, 0))
    blob.extend(bytes((0, 1)))  # samplers, textures
    blob.extend(struct.pack("<HHHHH", 0, 0, 0, 0, 0))
    mat_dict = add_dict([mat_name])

    align(8)
    fmdl_at = len(blob)
    blob.extend(b"FMDL")
    blob.extend(struct.pack("<I", 0))
    blob.extend(struct.pack("<Q", model_name))
    blob.extend(struct.pack("<Q", file_name))
    blob.extend(struct.pack("<Q", 0))  # skeleton
    blob.extend(struct.pack("<Q", fvtx_at))
    blob.extend(struct.pack("<QQ", shape_at, shape_dict))
    blob.extend(struct.pack("<QQQ", mat_at, 0, mat_dict))
    blob.extend(struct.pack("<QQ", 0, 0))  # user data
    blob.extend(struct.pack("<Q", 0))
    blob.extend(struct.pack("<HHH", 1, 1, 1))
    blob.extend(struct.pack("<HHH", 0, 0, 0))
    blob.extend(struct.pack("<I", 0))
    model_dict = add_dict([model_name])

    if embed_texture:
        align(16)
        bntx_at = len(blob)
        blob.extend(bntx)
        align(8)
        ext_at = len(blob)
        blob.extend(struct.pack("<QQ", bntx_at, len(bntx)))
        ext_name = add_string("textures.bntx")
        ext_dict = add_dict([ext_name])
        ext_count = 1
    else:
        ext_at = 0
        ext_dict = 0
        ext_count = 0

    align(8)
    info_at = len(blob)
    # BufferOffset is patched after the GPU bytes are placed.
    blob.extend(struct.pack("<II", 0, len(index_blob) + len(vertex)))
    info_hole = len(blob)
    blob.extend(struct.pack("<q", 0))
    blob.extend(b"\0" * 16)

    align(8)
    gpu_at = len(blob)
    blob.extend(index_blob)
    align(8)
    vert_at = len(blob)
    blob.extend(vertex)
    struct.pack_into("<i", blob, fvtx_buffer_hole, vert_at - gpu_at)
    struct.pack_into("<q", blob, info_hole, gpu_at)

    struct.pack_into("<I", blob, 0x10, file_name + 2)
    struct.pack_into("<Q", blob, 0x20, file_name)
    struct.pack_into("<Q", blob, 0x28, fmdl_at)
    struct.pack_into("<Q", blob, 0x30, model_dict)
    struct.pack_into("<Q", blob, 0xB0, info_at)
    struct.pack_into("<Q", blob, 0xB8, ext_at)
    struct.pack_into("<Q", blob, 0xC0, ext_dict)
    struct.pack_into("<H", blob, 0xDC, 1)
    struct.pack_into("<H", blob, 0xEC, ext_count)
    struct.pack_into("<I", blob, 0x1C, len(blob))
    return bytes(blob)
