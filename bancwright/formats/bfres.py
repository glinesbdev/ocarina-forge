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

import re
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
    material: str = ""
    material_index: int = -1
    # (sampler, texture) in file order, then the albedo candidates best-first.
    binds: list[tuple[str, str]] = field(default_factory=list)
    candidates: list[tuple[str, str]] = field(default_factory=list)
    albedo_sampler: str = ""
    alpha: str = "opaque"
    cutoff: float = 0.5
    double_sided: bool = True
    base_color: tuple[float, float, float, float] | None = None


@dataclass
class Material:
    name: str
    binds: list[tuple[str, str]] = field(default_factory=list)
    albedo: list[tuple[str, str]] = field(default_factory=list)
    alpha: str = "opaque"
    cutoff: float = 0.5
    double_sided: bool = True
    base_color: tuple[float, float, float, float] | None = None
    shader: str = ""
    note: str = ""


@dataclass
class Model:
    name: str
    parts: list[Part] = field(default_factory=list)
    images: dict[str, Texture] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Texture names found in the file but not tied to one shape (string pool).
    extra_textures: list[str] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    # Other identifiers in the file, used when a material names no texture.
    string_pool: list[str] = field(default_factory=list)


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
        pool = _string_pool(self.data)
        found = []
        at = self.model_values
        for key in keys:
            model, at = self._model(at, key, images, pool)
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

    def _model(self, at: int, fallback: str, images: dict[str, Texture], pool: list[str]) -> tuple[Model, int]:
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
        if self.major >= 9:
            # Version 9 and later keep an 8-byte reserved slot between the
            # material array and its dictionary. Reading past it is what
            # made every material texture name come back empty.
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
        if not any(material.binds or material.albedo for material in materials):
            scanned = self._scan_fmats()
            if scanned:
                materials = scanned
        shape_names = self.dict_keys(shape_dict)
        parts: list[Part] = []
        shape_at = shape_values
        for index, shape_name in enumerate(shape_names):
            part, shape_at = self._shape(shape_at, shape_name or f"shape{index}", bones, materials)
            if part is not None:
                parts.append(part)
        model = Model(
            name or fallback,
            parts,
            images,
            list(getattr(self, "_image_notes", [])),
            _texture_strings(self.data),
            materials,
            pool,
        )
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

    def _materials(self, values: int, dictionary: int) -> list[Material]:
        names = self.dict_keys(dictionary)
        # Version 9 and later (including Switch 2 files that report a higher
        # major) keep the texture-name array at a fixed offset. Walking a
        # guessed version-10 header skips that array and the mesh stays grey.
        stride = 0xA8 if self.major >= 9 else 0xB8
        if self.major >= 9 and values and self.data[values : values + 4] == b"FMAT":
            count = len(names) or 1
            found = []
            for index in range(count):
                at = values + index * stride
                if at + stride > len(self.data) or self.data[at : at + 4] != b"FMAT":
                    break
                found.append(self._read_material(at, names[index] if index < len(names) else ""))
            if found:
                return found
        at = values
        found = []
        for name in names:
            if not at or self.data[at : at + 4] != b"FMAT":
                found.append(Material(name))
                continue
            found.append(self._read_material(at, name))
            at += stride
        return found

    def _read_material(self, at: int, fallback: str) -> Material:
        if self.major >= 9:
            if at + 0xA8 > len(self.data):
                return Material(fallback)
            name = self.string(self.u64(at + 0x08)) or fallback
            render_at = self.u64(at + 0x10)
            render_dict = self.u64(at + 0x18)
            assign_at = self.u64(at + 0x20)
            texture_at = self.u64(at + 0x30)
            sampler_dict = self.u64(at + 0x48)
            param_at = self.u64(at + 0x50)
            param_dict = self.u64(at + 0x58)
            param_data = self.u64(at + 0x60)
            render_count = self.u16(at + 0x9A)
            sampler_count = self.data[at + 0x9C]
            texture_count = self.data[at + 0x9D]
            param_count = self.u16(at + 0x9E)
        else:
            if at + 0xB4 > len(self.data):
                return Material(fallback)
            name = self.string(self.u64(at + 0x10)) or fallback
            render_at = self.u64(at + 0x18)
            render_dict = self.u64(at + 0x20)
            assign_at = self.u64(at + 0x28)
            texture_at = self.u64(at + 0x38)
            sampler_dict = self.u64(at + 0x50)
            param_at = self.u64(at + 0x58)
            param_dict = self.u64(at + 0x60)
            param_data = self.u64(at + 0x68)
            render_count = self.u16(at + 0xA6)
            sampler_count = self.data[at + 0xA8]
            texture_count = self.data[at + 0xA9]
            param_count = self.u16(at + 0xAA)
        textures = self._name_list(texture_at, texture_count)
        if not textures and sampler_count:
            textures = self._name_list(texture_at, sampler_count)
        try:
            sampler_names = self.dict_keys(sampler_dict) if sampler_dict else []
        except FresError:
            sampler_names = []
        render: dict = {}
        shader = ""
        assign: dict[str, str] = {}
        options: dict[str, str] = {}
        color = None
        try:
            render = self._render_map(render_at, render_dict, render_count)
        except (FresError, struct.error, IndexError, OSError):
            render = {}
        try:
            shader, assign, options = self._shader_assign(assign_at)
        except (FresError, struct.error, IndexError, OSError):
            shader, assign, options = "", {}, {}
        try:
            color = self._param_color(param_at, param_dict, param_data, param_count)
        except (FresError, struct.error, IndexError, OSError):
            color = None
        role = {target: slot for slot, target in assign.items() if target}
        binds: list[tuple[str, str]] = []
        count = max(len(textures), len(sampler_names))
        for index in range(count):
            texture = textures[index] if index < len(textures) else ""
            sampler = sampler_names[index] if index < len(sampler_names) else ""
            if not texture and not sampler:
                continue
            binds.append((role.get(sampler, sampler), texture))
        albedo = rank_albedo(binds)
        alpha, cutoff, sided = _alpha_state(render, options, shader, name)
        return Material(name, binds, albedo, alpha, cutoff, sided, color, shader)

    def _name_list(self, at: int, count: int) -> list[str]:
        if not at or count <= 0 or count > 64:
            return []
        names = []
        for index in range(count):
            slot = at + index * 8
            if slot + 8 > len(self.data):
                break
            text = self.string(self.u64(slot))
            if not text or not _TEX_NAME.fullmatch(text):
                if index == 0:
                    return []
                break
            names.append(text)
        return names

    def _render_map(self, array: int, dictionary: int, count: int) -> dict:
        if not array or count <= 0 or count > 64:
            return {}
        try:
            labels = self.dict_keys(dictionary) if dictionary else []
        except FresError:
            labels = []
        found = {}
        for index in range(count):
            at = array + index * 0x18
            if at + 0x14 > len(self.data):
                break
            name = self.string(self.u64(at)) or (labels[index] if index < len(labels) else "")
            if not name:
                continue
            data_at = self.u64(at + 8)
            amount = self.u16(at + 0x10)
            kind = self.data[at + 0x12]
            found[name] = _render_values(self, data_at, kind, amount)
        return found

    def _shader_assign(self, at: int) -> tuple[str, dict[str, str], dict[str, str]]:
        if not at or at + 0x48 > len(self.data):
            return "", {}, {}
        archive = self.string(self.u64(at))
        model = self.string(self.u64(at + 8))
        if archive and not _TEX_NAME.fullmatch(archive):
            return "", {}, {}
        if model and not _TEX_NAME.fullmatch(model):
            return "", {}, {}
        samp_arr = self.u64(at + 0x20)
        samp_dict = self.u64(at + 0x28)
        opt_arr = self.u64(at + 0x30)
        opt_dict = self.u64(at + 0x38)
        samp_count = self.data[at + 0x45]
        opt_count = self.u16(at + 0x46)
        samplers: dict[str, str] = {}
        keys = self.dict_keys(samp_dict) if samp_dict else []
        for index, key in enumerate(keys[: max(samp_count, len(keys)) or 0][:64]):
            target = key
            if samp_arr:
                target = self.string(self.u64(samp_arr + index * 8)) or key
            if key:
                samplers[key] = target
        options: dict[str, str] = {}
        okeys = self.dict_keys(opt_dict) if opt_dict else []
        for index, key in enumerate(okeys[: max(opt_count, len(okeys)) or 0][:64]):
            if not opt_arr or not key:
                continue
            options[key] = self.string(self.u64(opt_arr + index * 8))
        return model or archive, samplers, options

    def _param_color(self, array: int, dictionary: int, data: int, count: int) -> tuple[float, float, float, float] | None:
        if not array or not data or count <= 0 or count > 80:
            return None
        try:
            labels = self.dict_keys(dictionary) if dictionary else []
        except FresError:
            labels = []
        for index in range(count):
            at = array + index * 0x20
            if at + 0x14 > len(self.data):
                break
            name = self.string(self.u64(at + 8)) or (labels[index] if index < len(labels) else "")
            kind = self.data[at + 0x10]
            offset = self.u16(at + 0x12)
            if kind not in (0x0E, 0x0F) or not _COLOR_PARAM.search(name) or _COLOR_SKIP.search(name):
                continue
            comps = 4 if kind == 0x0F else 3
            if data + offset + comps * 4 > len(self.data):
                continue
            values = [self.f32(data + offset + comp * 4) for comp in range(comps)]
            if comps == 3:
                values.append(1.0)
            return _sane_color(tuple(values))
        return None

    def _scan_fmats(self) -> list[Material]:
        found = []
        start = 0
        while True:
            at = self.data.find(b"FMAT", start)
            if at < 0:
                break
            material = self._read_material(at, "")
            if material.binds or material.albedo:
                found.append(material)
            start = at + 4
        return found

    def _shape(self, at: int, name: str, bones, materials: list[Material]) -> tuple[Part | None, int]:
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
        material = materials[material_index] if 0 <= material_index < len(materials) else None
        if not positions or not lod["indices"]:
            return None, end
        return _part_from(name, positions, lod["indices"], uvs, material, material_index), end

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


_ALBEDO = {"alb", "albedo", "basecolor", "diffuse", "diff", "paint", "color"}
_SKIP = {
    "nrm",
    "nml",
    "normal",
    "norm",
    "spm",
    "spc",
    "spec",
    "rgh",
    "rough",
    "emm",
    "emit",
    "mtl",
    "orm",
    "msk",
    "mask",
    "blend",
    "splat",
    "height",
}
_TEX_NAME = re.compile(r"[A-Za-z0-9_./+-]{1,120}")
_COLOR_PARAM = re.compile(r"(const_color|albedo_color|base_color|diffuse_color|material_color|color0|gsys_color)", re.I)
_COLOR_SKIP = re.compile(r"(spec|emit|emiss|rim|shadow|fog|light)", re.I)
_SAMPLER_SKIP = re.compile(
    r"^(n|n[0-7]|nrm[0-7]?|normal[0-7]?|s[0-7]|spc[0-7]?|spec[0-7]?|r[0-7]|rgh[0-7]?|rough[0-7]?|"
    r"m[0-7]|mtl[0-7]?|metal[0-7]?|e[0-7]|emm[0-7]?|emit[0-7]?|b[0-7]|blend[0-7]?|splat[0-7]?|"
    r"ao[0-7]?|mask[0-7]?|orm[0-7]?|h[0-7]|height[0-7]?)$"
)
_PRIMARY_SAMPLER = {
    "a0",
    "alb",
    "alb0",
    "albedo",
    "albedo0",
    "basecolor",
    "basecolor0",
    "diffuse",
    "diffuse0",
    "d0",
    "col0",
    "bc0",
    "color0",
    "base",
    "color",
}
_EXTRA_SAMPLER = {"alb1", "alb2", "alb3", "albedo1", "albedo2", "albedo3"}
_POOL = re.compile(rb"([A-Za-z][A-Za-z0-9_]{3,80})\x00")
_POOL_SKIP = {"FRES", "FMDL", "FMAT", "FSHP", "FVTX", "FSKL", "BNTX", "BRTI", "Body"}


_TEXTURE_IN_FILE = re.compile(
    rb"([A-Za-z][A-Za-z0-9_]{2,80}(?:_Alb|_Albedo|_BaseColor|_Diffuse|_Col)[A-Za-z0-9_]*)\x00"
)


def _texture_strings(data: bytes) -> list[str]:
    """Albedo-like names sitting in the string pool when the material table is unreadable."""
    found = []
    for match in _TEXTURE_IN_FILE.finditer(data):
        text = match.group(1).decode("ascii", errors="ignore")
        if text and text not in found:
            found.append(text)
    return found


def _string_pool(data: bytes) -> list[str]:
    found = []
    seen: set[str] = set()
    for match in _POOL.finditer(data):
        text = match.group(1).decode("ascii", "ignore")
        if not text or text in seen or text in _POOL_SKIP:
            continue
        seen.add(text)
        found.append(text)
        if len(found) >= 400:
            break
    return found


def texture_rank(name: str) -> int:
    """0 albedo, 1 anything else, 2 a normal / mask / blend map."""
    stem = (name or "").lower().rsplit("/", 1)[-1]
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
    if bag & _SKIP or any(item.startswith(("nrm", "normal")) for item in bag):
        return 2
    return 1


def _sampler_rank(name: str) -> int | None:
    token = "".join(char for char in (name or "").lower() if char.isalnum())
    if not token:
        return None
    if token in _PRIMARY_SAMPLER:
        return 0
    if token in _EXTRA_SAMPLER or (len(token) == 2 and token[0] == "a" and token[1] in "1234567"):
        return 1
    if _SAMPLER_SKIP.fullmatch(token):
        return 9
    return None


def rank_albedo(binds: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Albedo slots first. ``_a0`` wins even when it is not sampler 0.

    A normal, specular, or blend slot is left out, including when its
    texture name does not say ``_Nrm``. The caller draws a flat colour
    instead of that map.
    """
    ranked = []
    for index, (sampler, texture) in enumerate(binds):
        if not texture:
            continue
        srank = _sampler_rank(sampler)
        trank = texture_rank(texture)
        if srank == 9 or (srank is None and trank == 2):
            continue
        if srank == 0:
            key = (0, trank, index)
        elif srank == 1:
            key = (1, trank, index)
        elif trank == 0:
            key = (2, 0, index)
        else:
            key = (3, trank, index)
        ranked.append((key, sampler, texture))
    ranked.sort(key=lambda item: item[0])
    return [(sampler, texture) for _key, sampler, texture in ranked]


def base_color_name(names: list[str]) -> str:
    """The material texture that should color the mesh.

    Real materials list a normal or mask beside the albedo. Names like
    ``Obj_Rock_A_Alb`` win over ``Obj_Rock_A_Nrm``. A material with one
    texture keeps that texture. A list that is only non-color maps
    returns an empty string.
    """
    chosen = rank_albedo([("", name) for name in names])
    return chosen[0][1] if chosen else ""


def _part_from(name, positions, indices, uvs, material: Material | None, material_index: int) -> Part:
    sampler, texture = ("", "")
    if material and material.albedo:
        sampler, texture = material.albedo[0]
    return Part(
        name,
        positions,
        indices,
        uvs,
        texture,
        material.name if material else "",
        material_index if material else -1,
        list(material.binds) if material else [],
        list(material.albedo) if material else [],
        sampler,
        material.alpha if material else "opaque",
        material.cutoff if material else 0.5,
        material.double_sided if material else True,
        material.base_color if material else None,
    )


def _render_values(reader, data_at: int, kind: int, amount: int):
    if not data_at or amount <= 0:
        return None
    values = []
    for index in range(min(amount, 8)):
        if kind == 0:
            values.append(reader.i32(data_at + index * 4))
        elif kind == 1:
            values.append(reader.f32(data_at + index * 4))
        elif kind == 2:
            values.append(reader.string(reader.u64(data_at + index * 8)))
        else:
            return None
    if len(values) == 1:
        return values[0]
    return values


def _lookup_render(render: dict, *keys):
    for key in keys:
        if key in render:
            return render[key]
        for name, value in render.items():
            if str(name).lower() == key.lower():
                return value
    return None


def _as_text(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


def _truthy(value) -> bool:
    if isinstance(value, list):
        value = value[0] if value else 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "enable", "enabled", "on"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return False


def _sane_color(values) -> tuple[float, float, float, float] | None:
    if not values or len(values) < 3:
        return None
    red, green, blue = float(values[0]), float(values[1]), float(values[2])
    alpha = float(values[3]) if len(values) > 3 else 1.0
    if max(red, green, blue) > 1.5:
        red, green, blue = red / 255.0, green / 255.0, blue / 255.0
        if alpha > 1.5:
            alpha /= 255.0
    if min(red, green, blue, alpha) < 0 or max(red, green, blue) > 1 or alpha > 1:
        return None
    if red > 0.9 and green < 0.15 and blue > 0.9:
        return None
    return (red, green, blue, alpha)


def _alpha_state(render: dict, options: dict, shader: str, material_name: str) -> tuple[str, float, bool]:
    """Return alpha mode, cutoff, and whether both sides are drawn.

    Missing render info stays double-sided and opaque, which is how the
    view already drew meshes. An explicit front face, a mask, or a blend
    comes from the material.
    """
    mode = _as_text(_lookup_render(render, "gsys_render_state_mode", "render_state_mode")).lower()
    face = _as_text(_lookup_render(render, "gsys_render_state_display_face", "display_face")).lower().replace(" ", "")
    blend = _as_text(_lookup_render(render, "gsys_render_state_blend_mode", "blend_mode")).lower()
    func = _as_text(_lookup_render(render, "gsys_alpha_test_func", "alpha_test_func")).lower()
    enabled = _truthy(_lookup_render(render, "gsys_alpha_test_enable", "alpha_test_enable"))
    raw_cut = _lookup_render(render, "gsys_alpha_test_value", "alpha_test_value")
    if isinstance(raw_cut, list):
        raw_cut = raw_cut[0] if raw_cut else None
    cutoff = 0.5
    if isinstance(raw_cut, (int, float)) and not isinstance(raw_cut, bool):
        cutoff = float(raw_cut)
        if cutoff > 1:
            cutoff /= 255.0
        cutoff = min(max(cutoff, 0.0), 1.0)
    if func in {"always", "never"}:
        enabled = False
    option_test = _truthy(options.get("enable_alphatest") or options.get("gsys_alpha_test_enable"))
    option_blend = _truthy(options.get("enable_alphablend") or options.get("enable_blend"))
    alpha = "opaque"
    if any(token in mode for token in ("translucen", "blend")) or option_blend or (
        blend and blend not in {"opaque", "none", "0", "off"}
    ):
        alpha = "blend"
    elif "mask" in mode or "clip" in mode or "cutout" in mode or enabled or option_test:
        alpha = "mask"
    if alpha == "opaque":
        blob = " ".join((shader, material_name)).lower()
        if any(token in blob for token in ("_trs", "alphatest", "cutout", "billboard")):
            alpha = "mask"
    sided = True
    if face:
        if any(token in face for token in ("both", "double", "none", "frontandback")):
            sided = True
        elif "front" in face or "back" in face:
            sided = False
    return alpha, cutoff, sided


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
    samplers: list[tuple[str, str]] | None = None,
    alpha: str = "",
    cutoff: float = 0.5,
    display_face: str = "",
    link_textures: bool = True,
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
    custom_material = samplers is not None or bool(alpha) or bool(display_face) or not link_textures
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
    if not custom_material:
        tex_ptr = len(blob)
        blob.extend(struct.pack("<Q", tex_name))
        render_at = render_dict = 0
        render_count = 0
        sampler_dict_at = 0
        sampler_count = 0
        texture_count = 1
    else:
        pairs = list(samplers) if samplers is not None else [("_a0", texture_name)]
        tex_offs = [add_string(tex) for _sampler, tex in pairs]
        samp_offs = [add_string(sampler) for sampler, _tex in pairs if sampler]
        infos: list[tuple[str, int, list]] = []
        if alpha == "mask":
            infos.append(("gsys_alpha_test_enable", 0, [1]))
            infos.append(("gsys_alpha_test_value", 1, [float(cutoff)]))
        elif alpha == "blend":
            infos.append(("gsys_render_state_mode", 2, ["translucent"]))
        if display_face:
            infos.append(("gsys_render_state_display_face", 2, [display_face]))
        info_names = []
        info_rows = []
        for info_name, kind, values in infos:
            info_names.append(add_string(info_name))
            if kind == 2:
                text_offs = [add_string(str(item)) for item in values]
                align(8)
                data_at = len(blob)
                for item in text_offs:
                    blob.extend(struct.pack("<Q", item))
            else:
                align(4)
                data_at = len(blob)
                pack = "<f" if kind == 1 else "<i"
                for item in values:
                    blob.extend(struct.pack(pack, float(item) if kind == 1 else int(item)))
            info_rows.append((info_names[-1], data_at, len(values), kind))
        if info_rows:
            align(8)
            render_at = len(blob)
            for name_off, data_at, count, kind in info_rows:
                blob.extend(struct.pack("<QQ", name_off, data_at))
                blob.extend(struct.pack("<HB", count, kind))
                blob.extend(b"\0" * 5)
            render_dict = add_dict(info_names)
            render_count = len(info_rows)
        else:
            render_at = render_dict = 0
            render_count = 0
        align(8)
        if link_textures:
            tex_ptr = len(blob)
            for off in tex_offs:
                blob.extend(struct.pack("<Q", off))
            texture_count = len(pairs)
            sampler_count = len(samp_offs)
        else:
            tex_ptr = 0
            texture_count = 0
            sampler_count = 0
        sampler_dict_at = add_dict(samp_offs) if samp_offs and link_textures else 0
    mat_at = len(blob)
    blob.extend(b"FMAT")
    blob.extend(struct.pack("<I", 1))
    blob.extend(struct.pack("<Q", mat_name))
    blob.extend(struct.pack("<QQ", render_at, render_dict))
    blob.extend(struct.pack("<Q", 0))  # shader assign
    blob.extend(struct.pack("<Q", 0))  # runtime textures
    blob.extend(struct.pack("<Q", tex_ptr))
    blob.extend(struct.pack("<Q", 0))  # sampler array
    blob.extend(struct.pack("<QQ", 0, sampler_dict_at))
    blob.extend(struct.pack("<QQ", 0, 0))  # shader params
    blob.extend(struct.pack("<Q", 0))  # param data
    blob.extend(struct.pack("<QQ", 0, 0))  # user data
    blob.extend(struct.pack("<Q", 0))  # volatile
    blob.extend(struct.pack("<Q", 0))  # user pointer
    blob.extend(struct.pack("<QQ", 0, 0))  # slots
    blob.extend(struct.pack("<HH", 0, render_count))
    blob.extend(bytes((sampler_count & 0xFF, texture_count & 0xFF)))
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
