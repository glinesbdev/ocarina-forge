"""Read Switch BFRES meshes and their embedded BNTX images.

Field order follows the public Switch BFRES description and the loaders in
KillzXGaming/BfresLibrary (MIT): version 8, 9, and 10 headers, FMDL, FVTX,
FSHP, FMAT, and the GPU buffer. Vertex formats are the Switch attribute ids
from that library. This file is an original reader, not a copy of the C#.

Smooth-skinned vertices (skin count 2–4) are already stored in model space,
so they are the bind pose and are not moved again. Skin count 0 is stored in
the shape bone’s local space and is moved by that bone’s world matrix even
when the rigid palette index is -1. Skin count 1 stores each vertex in one
bone’s space; `_i0` indexes the skeleton matrix palette, and only a bone with
a rigid palette slot is applied. Bone flag 0x1000 stores Euler XYZ radians,
composed as Rz · Ry · Rx (X first, the same extrinsic order as a banc Rotate).
Anything else is a quaternion. Flag 0x800000 is segment-scale compensation.
Skeletal animation is not read.

`sample_bfres` writes a tiny file the reader understands. It is synthetic.
Nothing in it comes from a game.
"""

from __future__ import annotations

import math
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
    proxy: bool = False
    # "water" is an unlit translucent stand-in (or a real albedo drawn that way).
    stand_in: str = ""
    # 0 is the near mesh. A higher value is a farther BFRES LOD of the same shape.
    lod: int = 0
    # Helper shapes (water volume, bake, fade, inner mesh, eye cards) are not drawn.
    omit: bool = False
    omit_reason: str = ""
    # "repeat" writes a glTF sampler with wrap REPEAT. Terra UVs tile past 1.
    wrap: str = ""
    normal_texture: str = ""
    emission_texture: str = ""
    # Every `_uN` channel in the vertex buffer, with min/max. Not the channel the mesh is drawn with.
    uv_sets: list[dict] = field(default_factory=list)


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
    # v10 render-state string at index 11 (opaque, mask, translucent, custom).
    render_state: str = ""
    # Shader archive and shading model. Empty when the file has no shader assign.
    archive: str = ""
    options: dict[str, str] = field(default_factory=dict)
    # Sampler slot in the file -> the name the shader uses for that slot.
    assign: dict[str, str] = field(default_factory=dict)
    # Shader attribute -> mesh attribute. `_u0` / `_u1` / `_u2` are the UV channels.
    attributes: dict[str, str] = field(default_factory=dict)
    render_info: list[dict] = field(default_factory=list)
    params: list[dict] = field(default_factory=list)
    # v10 render-state strings in slot order, including the empty ones.
    states: list[str] = field(default_factory=list)
    # One line per unread v10 header qword, set only when no shader assign matched.
    slots: list[str] = field(default_factory=list)


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
    # Model-space AABB from shapes whose geometry this reader does not decode.
    shape_bounds: dict | None = None
    # Shape names whose FSHP flags include 0x10 and whose payload is not an FVTX.
    flag10: list[str] = field(default_factory=list)


def parse(data: bytes) -> list[Model]:
    return _File(data).models()


def inspect(data: bytes) -> dict:
    """Bone and shape report for one BFRES. This does not write a glb."""
    reader = _File(data)
    reader.capture = []
    error = ""
    try:
        reader.models()
    except FresError as exc:
        error = str(exc)
    return {"version": reader.major, "error": error, "models": reader.model_reports}


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
        # Set by inspect(). The converter leaves it empty, so a normal glb does not record bones.
        self.capture: list | None = None
        self.model_reports: list = []
        self._debug_bones: list = []
        self._debug_palette: list = []
        self._debug_worlds: list = []
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
            flagged = any(note.startswith("geometry format not decoded") for model in found for note in model.notes)
            if not flagged:
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
        if self.major >= 10:
            # Version 10 has no reserved qword between the material array and
            # its dictionary. +0x48 is a different pointer; using it as the
            # dictionary made every model look like it had one material.
            mat_values = self.u64(cursor)
            cursor += 8
            mat_dict = self.u64(cursor)
            cursor += 8
            cursor += 8  # keep user data and the counts at +0x68
        elif self.major >= 9:
            # Version 9 keeps an 8-byte reserved slot between the material
            # array and its dictionary.
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
        # +0x68 on version 9 and 10: vtx count, shape count, material count.
        vtx_count = self.u16(cursor)
        shape_count = self.u16(cursor + 2)
        material_count = self.u16(cursor + 4)
        cursor += 6
        if self.major >= 9:
            cursor += 2 + 2 + 2 + 4
        else:
            cursor += 2 + 4 + 4
        end = cursor
        del vtx_count, shape_count
        bones, palette = self._bones(skel) if skel else ([], [])
        shape_from = len(self.capture) if self.capture is not None else 0
        self._debug_bones = bones
        self._debug_palette = palette
        self._debug_worlds = _worlds(bones) if self.capture is not None and bones else []
        try:
            materials = self._materials(mat_values, mat_dict, material_count)
            if not any(material.binds or material.albedo for material in materials):
                scanned = self._scan_fmats()
                if scanned:
                    materials = scanned
            shape_names = self.dict_keys(shape_dict)
            parts: list[Part] = []
            undecoded: list[dict] = []
            shape_at = shape_values
            for index, shape_name in enumerate(shape_names):
                made, shape_at, info = self._shape(shape_at, shape_name or f"shape{index}", bones, palette, materials)
                if made:
                    parts.extend(made)
                elif info.get("flags", 0) & 0x10:
                    undecoded.append(info)
            near = [part for part in parts if part.lod == 0]
            far = [part for part in parts if part.lod]
            parts = _drop_opaque_twins(near) + far
            bounds = _union_bounds([item["bounds"] for item in undecoded if item.get("bounds")])
            model = Model(
                name or fallback,
                parts,
                images,
                list(getattr(self, "_image_notes", [])),
                _texture_strings(self.data),
                materials,
                pool,
                bounds,
            )
            model.flag10 = [str(item.get("name") or "") for item in undecoded]
            if undecoded and not parts:
                model.notes.append("geometry format not decoded (FSHP flag 0x10)")
            elif undecoded:
                model.notes.append(f"{len(undecoded)} shapes use FSHP flag 0x10 and were not decoded")
            elif not parts:
                model.notes.append(f"{model.name or fallback}: no drawable shape")
            return model, end
        finally:
            if self.capture is not None:
                self.model_reports.append(
                    {
                        "name": name or fallback,
                        "bones": _bone_table(bones, self._debug_worlds),
                        "palette": list(palette),
                        "shapes": self.capture[shape_from:],
                    }
                )

    def _bones(self, at: int) -> tuple[list[tuple], list[int]]:
        if at + 4 > len(self.data) or self.data[at : at + 4] != b"FSKL":
            return [], []
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
        for name in names:
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
            smooth = self.i16(cursor)
            cursor += 2
            rigid = self.i16(cursor)
            cursor += 2
            cursor += 4  # billboard, user count
            flags = self.u32(cursor)
            cursor += 4
            scale = (self.f32(cursor), self.f32(cursor + 4), self.f32(cursor + 8))
            cursor += 12
            raw_rot = (self.f32(cursor), self.f32(cursor + 4), self.f32(cursor + 8), self.f32(cursor + 12))
            cursor += 16
            pos = (self.f32(cursor), self.f32(cursor + 4), self.f32(cursor + 8))
            cursor += 12
            # 0x1000: the first three floats are Euler XYZ radians. The fourth is unused.
            rot = _euler_quat(raw_rot[0], raw_rot[1], raw_rot[2]) if flags & 0x1000 else raw_rot
            bones.append((parent, pos, rot, scale, flags, rigid, name, raw_rot, smooth))
        return bones, self._matrix_palette(at, len(bones))

    def _matrix_palette(self, at: int, bone_count: int) -> list[int]:
        """u16 bone index per rigid or smooth matrix. `_i0` indexes this list."""
        if self.major < 9 or bone_count <= 0 or at + 0x40 > len(self.data):
            return []
        palette_at = self.u64(at + 0x18)
        count = self.u16(at + 0x3A) + self.u16(at + 0x3C)
        if not palette_at or count <= 0 or count > 4096 or palette_at + count * 2 > len(self.data):
            return []
        palette = list(struct.unpack_from(f"<{count}H", self.data, palette_at))
        if any(index >= bone_count for index in palette):
            return []
        return palette

    def _materials(self, values: int, dictionary: int, declared: int = 0) -> list[Material]:
        names = self.dict_keys(dictionary)
        # Version 9 materials are 0xA8 bytes. Version 10 (the Switch 2 files)
        # are 0xB0: the texture array, sampler dictionary, and counts moved.
        # A shorter stride lands in the padding of material 0, so every later
        # shape is left with no material.
        if self.major >= 10:
            stride = 0xB0
        elif self.major >= 9:
            stride = 0xA8
        else:
            stride = 0xB8
        if self.major >= 9 and values and self.data[values : values + 4] == b"FMAT":
            count = len(names)
            if declared > count:
                present = 0
                for index in range(min(declared, 256)):
                    at = values + index * stride
                    if at + 4 > len(self.data) or self.data[at : at + 4] != b"FMAT":
                        break
                    present += 1
                if present > count:
                    count = present
            if count <= 0:
                count = 1
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
        if self.major >= 10:
            return self._read_material_v10(at, fallback)
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
        archive = model = ""
        attributes: dict[str, str] = {}
        try:
            archive, model, attributes, assign, options = self._shader_assign(assign_at)
        except (FresError, struct.error, IndexError, OSError):
            archive, model, attributes, assign, options = "", "", {}, {}, {}
        shader = model or archive
        try:
            color = self._param_color(param_at, param_dict, param_data, param_count)
        except (FresError, struct.error, IndexError, OSError):
            color = None
        try:
            params = self._read_params(param_at, param_dict, param_data, param_count)
        except (FresError, struct.error, IndexError, OSError, ValueError):
            params = []
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
        info = [{"name": key, "value": value} for key, value in render.items()]
        return Material(
            name,
            binds,
            albedo,
            alpha,
            cutoff,
            sided,
            color,
            shader,
            archive=archive,
            options=dict(options),
            assign=dict(assign),
            attributes=dict(attributes),
            render_info=info,
            params=params,
        )

    def _read_material_v10(self, at: int, fallback: str) -> Material:
        """Switch 2 FMAT. Offsets are from the FMAT magic, and the block is 0xB0 bytes.

        +0x08 name, +0x20 texture-name pointer array, +0x38 sampler-name
        dictionary, +0x40 render-state string array. +0xA0 is 0 or 1 and is not
        that array's length. +0xA2 sampler count, +0xA3 texture count. Sampler names and texture
        names are in the same order.
        """
        if at + 0xB0 > len(self.data) or self.data[at : at + 4] != b"FMAT":
            return Material(fallback)
        name = self.string(self.u64(at + 0x08)) or fallback
        texture_at = self.u64(at + 0x20)
        sampler_dict = self.u64(at + 0x38)
        state_at = self.u64(at + 0x40)
        render_count = self.u16(at + 0xA0)
        sampler_count = self.data[at + 0xA2]
        texture_count = self.data[at + 0xA3]
        textures = self._name_list(texture_at, texture_count)
        if not textures and sampler_count:
            textures = self._name_list(texture_at, sampler_count)
        try:
            sampler_names = self.dict_keys(sampler_dict) if sampler_dict else []
        except FresError:
            sampler_names = []
        # +0xA0 is 0 or 1 on these files. It is not the length of the string array.
        del render_count
        states = self._state_strings(state_at, 31)
        binds: list[tuple[str, str]] = []
        count = max(len(textures), len(sampler_names), texture_count, sampler_count)
        for index in range(min(count, 64)):
            texture = textures[index] if index < len(textures) else ""
            sampler = sampler_names[index] if index < len(sampler_names) else ""
            if not texture and not sampler:
                continue
            binds.append((sampler, texture))
        albedo = rank_albedo(binds)
        if len(states) > 11:
            alpha, cutoff, sided, mode = _alpha_from_render_strings(states)
        else:
            alpha, cutoff, sided = _alpha_state({}, {}, "", name)
            mode = ""
        note = self._render_info_note(at)
        try:
            archive, model, attributes, assign, options, params, slots = self._probe_v10_shader(at)
        except (FresError, struct.error, IndexError, OSError, ValueError):
            archive, model, attributes, assign, options, params, slots = "", "", {}, {}, {}, [], []
        return Material(
            name,
            binds,
            albedo,
            alpha,
            cutoff,
            sided,
            None,
            model or archive,
            note,
            mode,
            archive,
            options,
            assign,
            attributes,
            [],
            params,
            states,
            slots,
        )

    def _render_info_note(self, at: int) -> str:
        """Why a v10 material has no render-info keys.

        Version 9 stores that dictionary beside the texture list. On version 10
        those offsets are the texture names, and this reader does not treat
        them as render info. Other qwords in the 0xB0 header are checked for a
        real BFRES string dictionary (root reference 0xFFFFFFFF). A dictionary
        whose keys do not decode is reported. Anything else stays blank, and
        the sand material then uses a labeled layer guess.
        """
        slots = (0x10, 0x18, 0x28, 0x30, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90, 0x98)
        saw_empty = False
        try:
            for slot in slots:
                ptr = self.u64(at + slot)
                if not ptr or ptr + 24 > len(self.data):
                    continue
                if self.u32(ptr + 8) != 0xFFFFFFFF:
                    continue
                count = self.i32(ptr + 4)
                if count <= 0 or count > 64:
                    continue
                keys = self.dict_keys(ptr)
                usable = [key for key in keys if key and _RENDER_KEY.match(key)]
                if usable:
                    return "render info: " + ", ".join(usable[:8])
                if not any(keys):
                    saw_empty = True
        except (FresError, struct.error, IndexError, ValueError):
            return ""
        if saw_empty:
            return "render info keys empty"
        return ""

    def _state_strings(self, at: int, count: int) -> list[str]:
        """Fixed render-state slots. Stop at a null or a pointer that is not a string."""
        if not at or count <= 0 or count > 64:
            return []
        found = []
        for index in range(count):
            slot = at + index * 8
            if slot + 8 > len(self.data):
                break
            ptr = self.u64(slot)
            if not ptr or not self._is_string(ptr):
                break
            found.append(self.string(ptr))
        return found

    def _is_string(self, at: int) -> bool:
        if at <= 0 or at + 3 > len(self.data):
            return False
        length = self.u16(at)
        if length > 160:
            return False
        end = at + 2 + length
        if end >= len(self.data) or self.data[end] != 0:
            return False
        try:
            self.data[at + 2 : end].decode("ascii")
        except UnicodeDecodeError:
            return False
        return True

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

    def _shader_assign(self, at: int) -> tuple[str, str, dict[str, str], dict[str, str], dict[str, str]]:
        """Archive, shading model, attribute map, sampler map, and option strings.

        Attribute pairs are at +0x10 / +0x18 (count byte +0x44). Sampler pairs
        are at +0x20 / +0x28 (count byte +0x45). Option pairs are at +0x30 /
        +0x38 (count u16 +0x46). The dictionary key is the shader name and the
        array string is the mesh or material name. `_u0`, `_u1`, and `_u2` in
        either side are UV channels.
        """
        if not at or at + 0x48 > len(self.data):
            return "", "", {}, {}, {}
        archive = self.string(self.u64(at))
        model = self.string(self.u64(at + 8))
        if archive and not _TEX_NAME.fullmatch(archive):
            return "", "", {}, {}, {}
        if model and not _TEX_NAME.fullmatch(model):
            return "", "", {}, {}, {}
        attributes = self._string_pairs(self.u64(at + 0x10), self.u64(at + 0x18), self.data[at + 0x44])
        samplers = self._string_pairs(self.u64(at + 0x20), self.u64(at + 0x28), self.data[at + 0x45])
        options = self._string_pairs(self.u64(at + 0x30), self.u64(at + 0x38), self.u16(at + 0x46))
        return archive, model, attributes, samplers, options

    def _string_pairs(self, array: int, dictionary: int, count: int) -> dict[str, str]:
        if not dictionary and not array:
            return {}
        try:
            keys = self.dict_keys(dictionary) if dictionary else []
        except FresError:
            keys = []
        found = {}
        limit = max(count, len(keys)) if (count or keys) else 0
        for index, key in enumerate(keys[: min(limit, 64)]):
            target = key
            if array:
                slot = array + index * 8
                if slot + 8 <= len(self.data):
                    target = self.string(self.u64(slot)) or key
            if key:
                found[key] = target
        return found

    def _probe_v10_shader(self, header: int) -> tuple[str, str, dict[str, str], dict[str, str], dict[str, str], list[dict]]:
        """Find a shader-assign block and shader-param array in a v10 header.

        Texture names, the sampler dictionary, and the render-state strings
        stay at +0x20, +0x38, and +0x40. The other qwords, and a relative
        offset from each of them, are tried as a version-9 assign. A block
        whose count bytes disagree with its dictionaries is still kept when
        the dictionaries name UV channels or shader options. The dictionary
        length is the count in that case. A complete miss returns one line
        per header qword so the next dump shows the real slot.
        """
        slots = (0x10, 0x18, 0x28, 0x30, 0x48, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78, 0x80, 0x88, 0x90, 0x98, 0xA8)
        pointers: list[int] = []
        for slot in slots:
            ptr = self.u64(header + slot) if header + slot + 8 <= len(self.data) else 0
            for candidate in self._pointer_candidates(header, slot, ptr):
                if candidate not in pointers:
                    pointers.append(candidate)
        best = self._best_assign(pointers)
        nested: list[int] = []
        if best is None:
            for ptr in pointers:
                if not ptr or ptr + 0x40 > len(self.data) or self._dict_ok(ptr) or self._is_string(ptr):
                    continue
                for inner in range(0, 0x40, 8):
                    child = self.u64(ptr + inner)
                    if child and child not in pointers and child not in nested:
                        nested.append(child)
            best = self._best_assign(nested)
        if best is None:
            archive, model, attributes, assign, options = "", "", {}, {}, {}
        else:
            archive, model, attributes, assign, options = best
        hint = self._param_count_hint(header)
        params = self._probe_params(pointers, hint)
        if not params and nested:
            params = self._probe_params(nested, hint)
        slots_text = [] if archive or model else self._header_lines(header)
        return archive, model, attributes, assign, options, params, slots_text

    def _pointer_candidates(self, header: int, slot: int, ptr: int) -> list[int]:
        found = []
        if ptr and ptr + 0x48 <= len(self.data):
            found.append(ptr)
        low = ptr & 0xFFFFFFFF
        relative = header + slot + low
        if low and relative != ptr and relative + 0x48 <= len(self.data) and relative not in found:
            found.append(relative)
        return found

    def _best_assign(self, pointers: list[int]):
        best = None
        best_score = 0
        for ptr in pointers:
            found = self._assign_block(ptr)
            if found is None:
                continue
            score = _assign_score(*found)
            if score > best_score:
                best = found
                best_score = score
        return best

    def _assign_block(self, at: int) -> tuple[str, str, dict[str, str], dict[str, str], dict[str, str]] | None:
        strict = self._assign_strict(at)
        if strict is not None:
            return strict
        return self._assign_loose(at)

    def _assign_strict(self, at: int) -> tuple[str, str, dict[str, str], dict[str, str], dict[str, str]] | None:
        if not at or at + 0x48 > len(self.data):
            return None
        attr_dict = self.u64(at + 0x18)
        samp_dict = self.u64(at + 0x28)
        opt_dict = self.u64(at + 0x38)
        if not any(self._dict_ok(ptr) for ptr in (attr_dict, samp_dict, opt_dict)):
            return None
        archive_at = self.u64(at)
        model_at = self.u64(at + 8)
        if archive_at and not self._is_string(archive_at):
            return None
        if model_at and not self._is_string(model_at):
            return None
        archive, model, attributes, assign, options = self._shader_assign(at)
        if not archive and not model:
            return None
        attr_count = self.data[at + 0x44]
        samp_count = self.data[at + 0x45]
        opt_count = self.u16(at + 0x46)
        if attr_count > 32 or samp_count > 32 or opt_count > 64:
            return None
        if self._dict_ok(attr_dict):
            if len(self.dict_keys(attr_dict)) != attr_count:
                return None
        elif attr_count:
            return None
        if self._dict_ok(samp_dict):
            if len(self.dict_keys(samp_dict)) != samp_count:
                return None
        elif samp_count:
            return None
        if self._dict_ok(opt_dict):
            if len(self.dict_keys(opt_dict)) != opt_count:
                return None
        elif opt_count:
            return None
        if not attr_count and not samp_count and not opt_count:
            return None
        return archive, model, attributes, assign, options

    def _assign_loose(self, at: int) -> tuple[str, str, dict[str, str], dict[str, str], dict[str, str]] | None:
        """Same record as the strict reader, but the dictionary length is the count.

        Retail v10 files keep the archive, the shading model, and the three
        dictionaries, and the bytes at +0x44 do not match those lengths. The
        maps still have to name a UV channel, a sampler slot, or a shader option.
        """
        if not at or at + 0x48 > len(self.data):
            return None
        attr_dict = self.u64(at + 0x18)
        samp_dict = self.u64(at + 0x28)
        opt_dict = self.u64(at + 0x38)
        if not any(self._dict_ok(ptr) for ptr in (attr_dict, samp_dict, opt_dict)):
            return None
        archive = self._shader_label(self.u64(at))
        model = self._shader_label(self.u64(at + 8))
        if archive is None or model is None or (not archive and not model):
            return None
        attributes = self._string_pairs(self.u64(at + 0x10), attr_dict, 0)
        assign = self._string_pairs(self.u64(at + 0x20), samp_dict, 0)
        options = self._string_pairs(self.u64(at + 0x30), opt_dict, 0)
        if not _shader_symbols(attributes, assign, options):
            return None
        return archive, model, attributes, assign, options

    def _shader_label(self, at: int) -> str | None:
        """A printable shader name, or '' when the pointer is null. None if it is not a string."""
        if not at:
            return ""
        if not self._is_string(at):
            return None
        text = self.string(at)
        if text and not _SHADER_TEXT.fullmatch(text):
            return None
        return text

    def _param_count_hint(self, header: int) -> int:
        if header + 0xA6 > len(self.data):
            return 0
        count = self.u16(header + 0xA4)
        if 0 < count <= 80:
            return count
        return 0

    def _header_lines(self, header: int) -> list[str]:
        """What each unread qword in the 0xB0 material header points at."""
        if header + 0xB0 > len(self.data):
            return []
        lines = []
        known = {0x20: "texture names", 0x38: "sampler dictionary", 0x40: "render states"}
        for slot in range(0x10, 0xB0, 8):
            if slot in known:
                lines.append(f"+0x{slot:02X} {known[slot]}")
                continue
            ptr = self.u64(header + slot)
            lines.append(f"+0x{slot:02X} {self._describe_pointer(ptr)}")
        lines.append("tail " + self.data[header + 0xA0 : header + 0xB0].hex())
        return lines

    def _describe_pointer(self, ptr: int) -> str:
        if not ptr:
            return "null"
        if ptr + 16 > len(self.data):
            return f"{ptr:#x} outside the file"
        if self._is_string(ptr):
            return f"string {self.string(ptr)!r}"
        if self._dict_ok(ptr):
            try:
                keys = [key for key in self.dict_keys(ptr) if key][:8]
            except FresError:
                keys = []
            shown = ", ".join(keys) if keys else "(empty)"
            extra = ""
            try:
                count = self.i32(ptr + 4)
            except (struct.error, IndexError):
                count = 0
            if count > len(keys):
                extra = f" +{count - len(keys)}"
            return f"dict {shown}{extra}"
        names = self._name_list(ptr, 6)
        if len(names) >= 2:
            return "strings " + ", ".join(names)
        return f"{ptr:#x} " + self.data[ptr : ptr + 24].hex()

    def _probe_params(self, pointers: list[int], hint: int = 0) -> list[dict]:
        best: list[dict] = []
        useful: list[dict] = []
        for dictionary in pointers:
            if not self._dict_ok(dictionary):
                continue
            try:
                keys = [key for key in self.dict_keys(dictionary) if key]
            except FresError:
                continue
            if not keys or len(keys) > 80 or all(_sampler_key(key) for key in keys):
                continue
            counts = {len(keys)}
            if hint:
                counts.add(hint)
            for array in pointers:
                if array == dictionary:
                    continue
                for data in pointers:
                    if data in (array, dictionary):
                        continue
                    for count in counts:
                        try:
                            rows = self._read_params(array, dictionary, data, count)
                        except (FresError, struct.error, IndexError, OSError, ValueError):
                            continue
                        if len(rows) == count and len(rows) > len(best):
                            best = rows
                        elif rows and _params_useful(rows) and len(rows) > len(useful):
                            useful = rows
        return best or useful

    def _dict_ok(self, at: int) -> bool:
        if not at or at + 24 > len(self.data):
            return False
        if self.u32(at + 8) != 0xFFFFFFFF:
            return False
        count = self.i32(at + 4)
        return 0 < count <= 80

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

    def _read_params(self, array: int, dictionary: int, data: int, count: int) -> list[dict]:
        """Every shader uniform whose type and bytes are in the public list.

        Kinds are the Switch parameter ids: 0x0C–0x0F are floats, and 0x1E /
        0x1F are a texture SRT (mode, scale, rotation, translation). A row
        whose name is not the dictionary key, or whose floats are not finite,
        is left out. The caller drops a probe that does not decode every key.
        """
        if not array or not data or count <= 0 or count > 80:
            return []
        try:
            labels = self.dict_keys(dictionary) if dictionary else []
        except FresError:
            labels = []
        found = []
        for index in range(count):
            at = array + index * 0x20
            if at + 0x14 > len(self.data):
                break
            expected = labels[index] if index < len(labels) else ""
            name = self.string(self.u64(at + 8)) or expected
            if expected and name != expected:
                continue
            if not name:
                continue
            kind = self.data[at + 0x10]
            offset = self.u16(at + 0x12)
            row = _decode_param(self, name, kind, data, offset)
            if row is not None:
                found.append(row)
        return found

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

    def _shape(self, at: int, name: str, bones, palette, materials: list[Material]) -> tuple[list[Part], int, dict]:
        if self.data[at : at + 4] != b"FSHP":
            raise FresError(f"expected a shape at {at:#x}")
        flags = self.u32(at + 4) if self.major >= 9 else 0
        info = {"flags": flags, "bounds": None, "name": name}
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
        # Flag 0x10 (seen as flags 0x12) is a meshlet/mesh-codec shape. The
        # FVTX pointer does not land on an FVTX, and the mesh-count byte is 0.
        # Public Switch 1 notes have no layout for that payload.
        standard = bool(vtx and mesh_count and vtx + 4 <= len(self.data) and self.data[vtx : vtx + 4] == b"FVTX")
        if not standard:
            if flags & 0x10:
                info["bounds"] = self._shape_bounds(at)
            if self.capture is not None:
                self.capture.append(
                    {
                        "name": name,
                        "lod": 0,
                        "decoded": False,
                        "flags": flags,
                        "skinCount": skin_count,
                        "boneIndex": bone_index,
                        "reason": "shape is not a standard vertex buffer",
                        "fileBounds": _bounds_json(info.get("bounds")),
                    }
                )
            return [], end, info
        fvtx = self._fvtx(vtx)
        lod = self._lod(mesh_at)
        if fvtx is None or lod is None:
            if flags & 0x10:
                info["bounds"] = self._shape_bounds(at)
            return [], end, info
        positions, uvs = self._vertices(fvtx, lod)
        before = _point_bounds(positions) if self.capture is not None else None
        slots = None
        if bones and skin_count in (0, 1):
            slots = self._skin_slots(fvtx, len(positions)) if skin_count == 1 else None
            positions = _bind_pose(positions, bones, palette, bone_index, slots)
        if self.capture is not None:
            self.capture.append(
                _shape_report(
                    name,
                    0,
                    skin_count,
                    bone_index,
                    slots,
                    bones,
                    palette,
                    self._debug_worlds,
                    before,
                    _point_bounds(positions),
                    material_index,
                    materials,
                    flags,
                )
            )
        material = materials[material_index] if 0 <= material_index < len(materials) else None
        if not positions or not lod["indices"]:
            if flags & 0x10:
                info["bounds"] = self._shape_bounds(at)
            return [], end, info
        uv_sets = _uv_ranges(fvtx)
        made = [_part_from(name, positions, lod["indices"], uvs, material, material_index, 0)]
        made[0].uv_sets = uv_sets
        # LOD meshes are a contiguous array of 0x38-byte headers. Drawing every
        # level at once stacks the coarse mesh on the fine one. Keep LOD 0 for
        # near, and the coarsest smaller level for far.
        if mesh_count > 1:
            best = None
            best_level = 0
            for level in range(1, min(mesh_count, 4)):
                try:
                    coarser = self._lod(mesh_at + level * 0x38)
                except (FresError, struct.error, IndexError, OSError):
                    break
                if not coarser or not coarser["indices"]:
                    continue
                if len(coarser["indices"]) >= len(lod["indices"]):
                    continue
                try:
                    far_positions, far_uvs = self._vertices(fvtx, coarser)
                except (FresError, struct.error, IndexError, OSError):
                    continue
                far_before = _point_bounds(far_positions) if self.capture is not None else None
                far_slots = None
                if bones and skin_count in (0, 1):
                    far_slots = self._skin_slots(fvtx, len(far_positions)) if skin_count == 1 else None
                    far_positions = _bind_pose(far_positions, bones, palette, bone_index, far_slots)
                if self.capture is not None:
                    self.capture.append(
                        _shape_report(
                            name,
                            level,
                            skin_count,
                            bone_index,
                            far_slots,
                            bones,
                            palette,
                            self._debug_worlds,
                            far_before,
                            _point_bounds(far_positions),
                            material_index,
                            materials,
                            flags,
                        )
                    )
                if not far_positions:
                    continue
                best = (far_positions, far_uvs, coarser["indices"])
                best_level = level
            if best is not None:
                far = _part_from(name, best[0], best[2], best[1], material, material_index, best_level)
                far.uv_sets = uv_sets
                made.append(far)
        return made, end, info

    def _shape_bounds(self, at: int) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """One shape AABB. Version 9+ stores center and extent at the +0x38 pointer."""
        if self.major < 9 or at + 0x48 > len(self.data):
            return None
        ptr = self.u64(at + 0x38)
        if ptr and ptr + 24 <= len(self.data):
            values = struct.unpack_from("<6f", self.data, ptr)
            if all(math.isfinite(item) and abs(item) < 5000 for item in values):
                center, extent = values[:3], values[3:]
                if all(item >= 0 for item in extent) and max(extent) > 0.01:
                    return (
                        tuple(center[i] - extent[i] for i in range(3)),
                        tuple(center[i] + extent[i] for i in range(3)),
                    )
                if all(center[i] < extent[i] for i in range(3)):
                    return (center, extent)
        radius_at = self.u64(at + 0x40)
        if radius_at and radius_at + 4 <= len(self.data):
            radius = self.f32(radius_at)
            if math.isfinite(radius) and 0.05 < radius < 500:
                return ((-radius, -radius, -radius), (radius, radius, radius))
        return None

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

    def _skin_slots(self, fvtx: dict, count: int) -> list[int] | None:
        """Raw `_i0` palette slots. The attribute is an index, not a 0–1 colour."""
        attribute = _attr(fvtx["attributes"], ("_i0", "i0", "_i"))
        if attribute is None:
            return None
        slots = []
        for index in range(count):
            try:
                slots.append(_read_index(fvtx, attribute, index))
            except FresError:
                return None
        return slots


def _uv_ranges(fvtx: dict) -> list[dict]:
    """Min and max of every `_uN` channel. The drawn mesh still uses `_u0`."""
    found = []
    for attribute in fvtx.get("attributes") or []:
        name = attribute.get("name") or ""
        if not _UV_ATTR.fullmatch(name):
            continue
        umin = vmin = math.inf
        umax = vmax = -math.inf
        count = 0
        for index in range(int(fvtx.get("count") or 0)):
            try:
                raw = _read_attr(fvtx, attribute, index)
            except (FresError, struct.error, IndexError):
                break
            if len(raw) < 2 or not math.isfinite(raw[0]) or not math.isfinite(raw[1]):
                continue
            umin = min(umin, raw[0])
            umax = max(umax, raw[0])
            vmin = min(vmin, raw[1])
            vmax = max(vmax, raw[1])
            count += 1
        if not count:
            continue
        found.append(
            {
                "name": name,
                "min": [round(umin, 5), round(vmin, 5)],
                "max": [round(umax, 5), round(vmax, 5)],
                "count": count,
            }
        )
    return found


def _assign_score(archive: str, model: str, attributes: dict, assign: dict, options: dict) -> int:
    score = (2 if archive else 0) + (2 if model else 0)
    for mapping in (attributes, assign):
        for key, value in mapping.items():
            if _UV_ATTR.fullmatch(str(key)) or _UV_ATTR.fullmatch(str(value)):
                score += 3
            elif _SAMPLER_SLOT.fullmatch(str(key)):
                score += 1
    if options:
        score += 2
    return score


def _shader_symbols(attributes: dict, assign: dict, options: dict) -> bool:
    for key, value in {**attributes, **assign}.items():
        if _UV_ATTR.fullmatch(str(key)) or _UV_ATTR.fullmatch(str(value)):
            return True
        if _SAMPLER_SLOT.fullmatch(str(key)) and str(key).startswith("_"):
            return True
    return any(_SHADER_OPTION.search(str(key)) for key in options)


def _params_useful(rows: list[dict]) -> bool:
    for row in rows:
        if row.get("kind") == "texsrt":
            return True
        if _PARAM_USEFUL.search(str(row.get("name") or "")):
            return True
    return False


def _attr(attributes: list[dict], names: tuple[str, ...]) -> dict | None:
    for attribute in attributes:
        if attribute["name"] in names:
            return attribute
    return None


def _read_index(fvtx: dict, attribute: dict, index: int) -> int:
    buffer_index = attribute["buffer"]
    if buffer_index >= len(fvtx["buffers"]):
        raise FresError(f"attribute {attribute['name']} names a missing buffer")
    stride = fvtx["strides"][buffer_index] or _format_size(attribute["format"])
    blob = fvtx["buffers"][buffer_index]
    at = index * stride + attribute["offset"]
    fmt = attribute["format"]
    size = _format_size(fmt)
    if size <= 0 or at + size > len(blob):
        raise FresError(f"vertex format 0x{fmt:04X} does not fit in its buffer")
    if fmt == _U8:
        return blob[at]
    if fmt in (_U8_2, _U8_4, _S8_4):
        return blob[at]
    if fmt == _U16_2:
        return struct.unpack_from("<H", blob, at)[0]
    raw = _decode(blob, at, fmt)
    return int(raw[0]) if raw else 0


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


def _point_bounds(positions) -> dict | None:
    if not positions:
        return None
    mins = [min(point[axis] for point in positions) for axis in range(3)]
    maxs = [max(point[axis] for point in positions) for axis in range(3)]
    return {"min": mins, "max": maxs}


def _bounds_json(bounds) -> dict | None:
    if not bounds or len(bounds) != 2:
        return None
    return {"min": list(bounds[0]), "max": list(bounds[1])}


def _finite(value: float) -> float | None:
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _vec(values) -> list:
    return [_finite(float(item)) for item in values]


def _matrix_json(matrix) -> list | None:
    if not matrix:
        return None
    return [[_finite(float(item)) for item in row] for row in matrix]


def _bone_chain(bones, index: int) -> list[int]:
    chain = []
    seen = set()
    while isinstance(index, int) and 0 <= index < len(bones) and index not in seen:
        seen.add(index)
        chain.append(index)
        parent = bones[index][0]
        index = parent
    chain.reverse()
    return chain


def _bone_row(bones, worlds, index: int) -> dict:
    bone = bones[index]
    flags = bone[4] if len(bone) > 4 else 0
    bits = []
    if flags & 1:
        bits.append("visible")
    bits.append("euler-xyz" if flags & 0x1000 else "quaternion")
    if flags & 0x800000:
        bits.append("segment-scale")
    return {
        "index": index,
        "name": bone[6] if len(bone) > 6 else "",
        "parent": bone[0],
        "scale": _vec(bone[3]),
        "rotation": _vec(bone[7] if len(bone) > 7 else bone[2]),
        "translation": _vec(bone[1]),
        "flags": flags,
        "flagText": " ".join(bits),
        "rigidIndex": bone[5] if len(bone) > 5 else 0,
        "smoothIndex": bone[8] if len(bone) > 8 else None,
        "quaternion": _vec(bone[2]),
        "world": _matrix_json(worlds[index] if index < len(worlds) else None),
    }


def _bone_table(bones, worlds) -> list[dict]:
    return [_bone_row(bones, worlds, index) for index in range(len(bones))]


def _shape_report(name, lod, skin_count, bone_index, slots, bones, palette, worlds, before, after, material_index, materials, flags) -> dict:
    counts: dict[int, int] = {}
    if slots is None:
        if 0 <= bone_index < len(bones) or not bones:
            counts[bone_index] = 0
    else:
        for slot in slots:
            if palette and 0 <= slot < len(palette):
                index = palette[slot]
            else:
                index = slot
            counts[index] = counts.get(index, 0) + 1
    used = []
    for index in sorted(counts):
        chain = _bone_chain(bones, index) if bones else []
        used.append(
            {
                "bone": index,
                "vertices": counts[index],
                "chain": [_bone_row(bones, worlds, item) for item in chain] if bones else [],
            }
        )
    material = ""
    if materials and 0 <= material_index < len(materials):
        material = materials[material_index].name
    return {
        "name": name,
        "lod": lod,
        "decoded": True,
        "flags": flags,
        "material": material,
        "skinCount": skin_count,
        "boneIndex": bone_index,
        "paletteSlots": None if slots is None else sorted(set(slots))[:32],
        "bones": used,
        "before": before,
        "after": after,
    }


def _rigid(positions, bones, bone_index: int) -> list[tuple[float, float, float]]:
    """Move a rigid shape by one bone's bind-pose matrix. Each local TRS is applied once."""
    return _bind_pose(positions, bones, [], bone_index, None)


def _bind_pose(positions, bones, palette, bone_index: int, slots: list[int] | None):
    """Bake skin count 0 or 1 into model space. Smooth skin is left where it is.

    `slots` is one palette index per vertex. None means every vertex uses the
    shape bone. A skin-count-0 shape is in that bone's local space, so the
    bone's world matrix applies even when its rigid index is -1. A skin-count-1
    vertex on a bone with no rigid slot is already in model space.
    """
    if not bones:
        return positions
    worlds = _worlds(bones)

    def matrix_for(slot: int | None):
        index = bone_index
        if slot is not None:
            if palette and 0 <= slot < len(palette):
                index = palette[slot]
            elif 0 <= slot < len(bones):
                index = slot
        if index < 0 or index >= len(bones) or index >= len(worlds):
            return None
        if slot is not None:
            rigid = bones[index][5] if len(bones[index]) > 5 else 0
            if rigid < 0:
                return None
        return worlds[index]

    if slots is None:
        matrix = matrix_for(None)
        if matrix is None:
            return positions
        return [_apply(matrix, point) for point in positions]
    out = []
    for index, point in enumerate(positions):
        matrix = matrix_for(slots[index] if index < len(slots) else None)
        out.append(_apply(matrix, point) if matrix is not None else point)
    return out


def _worlds(bones) -> list:
    worlds: list = [None] * len(bones)
    pending = set(range(len(bones)))
    for _pass in range(len(bones) + 1):
        if not pending:
            break
        ready = []
        for index in pending:
            parent, pos, rot, scale = bones[index][0], bones[index][1], bones[index][2], bones[index][3]
            flags = bones[index][4] if len(bones[index]) > 4 else 0
            spinning = _trs((0.0, 0.0, 0.0), rot, scale)
            if flags & 0x800000 and 0 <= parent < len(bones) and parent != index:
                # T · inverse(parent scale) · R · S, then the parent world.
                spinning = _mul(_scale_inverse(bones[parent][3]), spinning)
            transform = _mul(_trs(pos, (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)), spinning)
            if parent == index or parent < 0 or parent >= len(bones):
                worlds[index] = transform
                ready.append(index)
            elif worlds[parent] is not None:
                worlds[index] = _mul(worlds[parent], transform)
                ready.append(index)
        if not ready:
            break
        pending.difference_update(ready)
    for index in pending:
        bone = bones[index]
        worlds[index] = _trs(bone[1], bone[2], bone[3])
    return worlds


def _scale_inverse(scale) -> list[list[float]]:
    def inv(value: float) -> float:
        return 1.0 / value if abs(value) > 1e-8 else 1.0

    sx, sy, sz = (scale + (1.0, 1.0, 1.0))[:3]
    return [
        [inv(sx), 0.0, 0.0, 0.0],
        [0.0, inv(sy), 0.0, 0.0],
        [0.0, 0.0, inv(sz), 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _euler_quat(x: float, y: float, z: float) -> tuple[float, float, float, float]:
    """Extrinsic XYZ radians. The matrix is Rz · Ry · Rx, so X is applied first.

    That is three.js Euler order ``ZYX``. A pure yaw matches order ``XYZ``;
    a combined X and Y, such as the ivy sheets, does not.
    """
    cx, sx = math.cos(x * 0.5), math.sin(x * 0.5)
    cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
    cz, sz = math.cos(z * 0.5), math.sin(z * 0.5)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


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
    "fx",
    "ind",
}
_TEX_NAME = re.compile(r"[A-Za-z0-9_./+-]{1,120}")
def bind_role(sampler: str, texture: str = "") -> str:
    """albedo, normal, emission, mask, or other. The slot name wins over the texture name."""
    token = "".join(char for char in (sampler or "").lower() if char.isalnum())
    if token in _PRIMARY_SAMPLER or token in _EXTRA_SAMPLER or (len(token) == 2 and token[0] == "a" and token[1].isdigit()):
        return "albedo"
    if token in {"n", "n0", "n1", "n2", "n3", "nrm", "nrm0", "normal", "normal0"} or token.startswith(("normal", "nrm")):
        return "normal"
    if "emiss" in token or token.startswith(("emm", "emit")) or token in {"e", "e0", "e1", "e2", "e3"}:
        return "emission"
    if "mask" in token or token.startswith("msk"):
        return "mask"
    if token and _SAMPLER_SKIP.fullmatch(token):
        return "other"
    if not token:
        rank = texture_rank(texture)
        if rank == 0:
            return "albedo"
        if rank == 2:
            return "other"
    return "other"


def _sampler_key(name: str) -> bool:
    """A sampler slot such as ``_a0``, not a uniform such as ``albedo_color``."""
    return bool(re.fullmatch(r"_[A-Za-z]{1,12}\d{0,2}", name or ""))


_PARAM_FLOATS = {0x0C: 1, 0x0D: 2, 0x0E: 3, 0x0F: 4}
_PARAM_INTS = {0x00: 1, 0x01: 2, 0x02: 3, 0x03: 4, 0x04: 1, 0x05: 2, 0x06: 3, 0x07: 4}
_PARAM_UINTS = {0x08: 1, 0x09: 2, 0x0A: 3, 0x0B: 4}
_SRT_MODE = {0: "maya", 1: "max", 2: "softimage"}


def _decode_param(reader, name: str, kind: int, data: int, offset: int) -> dict | None:
    if kind in (0x1E, 0x1F):
        at = data + offset
        if at < 0 or at + 24 > len(reader.data):
            return None
        mode = reader.u32(at)
        scale = (reader.f32(at + 4), reader.f32(at + 8))
        rotate = reader.f32(at + 12)
        translate = (reader.f32(at + 16), reader.f32(at + 20))
        numbers = (*scale, rotate, *translate)
        if not all(math.isfinite(item) for item in numbers):
            return None
        return {
            "name": name,
            "kind": "texsrt",
            "mode": _SRT_MODE.get(mode, mode),
            "scale": [round(item, 6) for item in scale],
            "rotate": round(rotate, 6),
            "translate": [round(item, 6) for item in translate],
        }
    if kind in _PARAM_FLOATS:
        count = _PARAM_FLOATS[kind]
        at = data + offset
        if at < 0 or at + count * 4 > len(reader.data):
            return None
        values = [reader.f32(at + index * 4) for index in range(count)]
        if not all(math.isfinite(item) for item in values):
            return None
        return {"name": name, "kind": "float", "value": [round(item, 6) for item in values]}
    if kind in _PARAM_INTS or kind in _PARAM_UINTS:
        count = _PARAM_INTS.get(kind) or _PARAM_UINTS[kind]
        at = data + offset
        if at < 0 or at + count * 4 > len(reader.data):
            return None
        read = reader.u32 if kind in _PARAM_UINTS else reader.i32
        label = "uint" if kind in _PARAM_UINTS else ("bool" if kind <= 0x03 else "int")
        return {"name": name, "kind": label, "value": [read(at + index * 4) for index in range(count)]}
    return None


def _param_bytes(item: dict) -> bytes:
    kind = int(item["kind"]) if item.get("kind") is not None else 0x0C
    if kind in (0x1E, 0x1F):
        scale = list(item.get("scale") or [1.0, 1.0])
        translate = list(item.get("translate") or [0.0, 0.0])
        return struct.pack(
            "<I5f",
            int(item.get("mode") or 0),
            float(scale[0]),
            float(scale[1]),
            float(item.get("rotate") or 0),
            float(translate[0]),
            float(translate[1]),
        )
    values = list(item.get("values") or [0])
    if kind < 0x0C:
        return struct.pack("<" + "i" * len(values), *[int(item) for item in values])
    return struct.pack("<" + "f" * len(values), *[float(item) for item in values])


def _pack_shader(blob: bytearray, add_string, add_dict, shader: dict) -> tuple[int, int, int, int, int]:
    """Append a version-9 shader assign and its uniforms. Returns the five pointers and the param count."""
    archive = add_string(str(shader.get("archive") or ""))
    model = add_string(str(shader.get("model") or ""))

    def align(n: int) -> None:
        while len(blob) % n:
            blob.append(0)

    def pack_map(mapping: dict) -> tuple[int, int, int]:
        items = list(mapping.items())
        if not items:
            return 0, 0, 0
        keys = [add_string(str(key)) for key, _value in items]
        values = [add_string(str(value)) for _key, value in items]
        align(8)
        array = len(blob)
        for off in values:
            blob.extend(struct.pack("<Q", off))
        return array, add_dict(keys), len(items)

    attr_arr, attr_dict, attr_count = pack_map(dict(shader.get("attributes") or {}))
    samp_arr, samp_dict, samp_count = pack_map(dict(shader.get("samplers") or {}))
    opt_arr, opt_dict, opt_count = pack_map({str(key): str(value) for key, value in (shader.get("options") or {}).items()})
    align(8)
    assign_at = len(blob)
    block = bytearray(0x48)
    struct.pack_into("<Q", block, 0, archive)
    struct.pack_into("<Q", block, 8, model)
    struct.pack_into("<Q", block, 0x10, attr_arr)
    struct.pack_into("<Q", block, 0x18, attr_dict)
    struct.pack_into("<Q", block, 0x20, samp_arr)
    struct.pack_into("<Q", block, 0x28, samp_dict)
    struct.pack_into("<Q", block, 0x30, opt_arr)
    struct.pack_into("<Q", block, 0x38, opt_dict)
    block[0x44] = attr_count & 0xFF
    block[0x45] = samp_count & 0xFF
    struct.pack_into("<H", block, 0x46, opt_count)
    blob.extend(block)
    params = list(shader.get("params") or [])
    if not params:
        return assign_at, 0, 0, 0, 0
    names = [add_string(str(item.get("name") or f"param{index}")) for index, item in enumerate(params)]
    chunks = [_param_bytes(item) for item in params]
    align(8)
    data_at = len(blob)
    offsets = []
    for chunk in chunks:
        offsets.append(len(blob) - data_at)
        blob.extend(chunk)
        align(4)
    align(8)
    array_at = len(blob)
    for index, item in enumerate(params):
        kind = int(item["kind"]) if item.get("kind") is not None else 0x0C
        blob.extend(struct.pack("<QQ", 0, names[index]))
        blob.extend(struct.pack("<BBH", kind & 0xFF, len(chunks[index]) & 0xFF, offsets[index] & 0xFFFF))
        blob.extend(struct.pack("<iHH", -1, 0, 0))
        blob.extend(struct.pack("<I", 0))
    return assign_at, array_at, add_dict(names), data_at, len(params)


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
_RENDER_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,48}$")
_SHADER_TEXT = re.compile(r"(?=.*[A-Za-z])[ -~]{1,160}")
_UV_ATTR = re.compile(r"^_?u\d+$", re.I)
_SHADER_OPTION = re.compile(r"enable|alpha|proj|uv|coord|gsys|tile|macro|world|planar", re.I)
_SAMPLER_SLOT = re.compile(r"^_?[A-Za-z]{1,12}\d{0,2}$")
_PARAM_USEFUL = re.compile(r"srt|tile|uv|color|albedo|scale|tex", re.I)


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
    if bag & _SKIP or any(item.startswith(("nrm", "normal")) for item in bag):
        return 2
    # BarkFx never splits into a token, so the Alb check used to win on
    # TreeAlbFx. A separated _Fx / _Msk / _Ind is already in the bag above.
    # "ind" is not a raw suffix: that would catch Wind.
    if len(stem) > 2 and stem.endswith(("fx", "msk", "nrm")):
        return 2
    if bag & _ALBEDO or any(item.startswith("alb") for item in bag):
        return 0
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


_LOCKED_EXTRA = {"a1", "a2", "alb1", "alb2", "albedo1", "albedo2"}


def rank_albedo(binds: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Albedo slots first. ``_a0`` wins even when it is not sampler 0.

    When the material names its samplers, only ``_a0`` then ``_a1`` / ``_a2``
    are albedo. A normal, roughness, bake, or runtime slot (``tc``, ``ta``,
    ``MaterialAlb``) is not a fallback, and neither is the string pool.
    ``Mt_TerraMaterial_NNN`` is the exception, and the model loader reads
    layer NNN of ``MaterialAlb`` itself rather than treating that name as albedo.
    A texture whose name is a normal, mask, or blend map is skipped even
    when the slot is ``_a0``.
    A material with no sampler names still ranks texture names, which is
    how the version-9 samples are written.
    """
    if any(sampler for sampler, _texture in binds):
        return _rank_locked_samplers(binds)
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


def _rank_locked_samplers(binds: list[tuple[str, str]]) -> list[tuple[str, str]]:
    ranked = []
    for index, (sampler, texture) in enumerate(binds):
        if not texture:
            continue
        if texture_rank(texture) >= 2:
            continue
        token = "".join(char for char in sampler.lower() if char.isalnum())
        if token in _PRIMARY_SAMPLER:
            key = 0
        elif token in _LOCKED_EXTRA:
            key = 1
        else:
            continue
        ranked.append((key, index, sampler, texture))
    ranked.sort()
    return [(sampler, texture) for _key, _index, sampler, texture in ranked]


def base_color_name(names: list[str]) -> str:
    """The material texture that should color the mesh.

    Real materials list a normal or mask beside the albedo. Names like
    ``Obj_Rock_A_Alb`` win over ``Obj_Rock_A_Nrm``. A material with one
    texture keeps that texture. A list that is only non-color maps
    returns an empty string.
    """
    chosen = rank_albedo([("", name) for name in names])
    return chosen[0][1] if chosen else ""


def _close_uv_seams(positions, uvs, indices):
    """Split a triangle whose UVs jump across the texture.

    A trunk unwrap puts u=0.98 and u=0.02 on one triangle. The GPU then sees
    a huge derivative, picks a coarse mip, and the mip averages the magenta
    gutter into a line down the seam. Moving the wrapped corner by 1 makes
    that triangle continuous. The other triangles keep the original vertex.
    """
    if not uvs or len(uvs) != len(positions) or len(indices) < 3:
        return positions, uvs, indices
    pos = list(positions)
    uv = [(float(u), float(v)) for u, v in uvs]
    idx = list(indices)
    limit = 0.5 - 1e-4
    for tri in range(0, len(idx) - 2, 3):
        for axis in (0, 1):
            ids = [idx[tri], idx[tri + 1], idx[tri + 2]]
            vals = [uv[j][axis] for j in ids]
            if max(vals) - min(vals) <= 0.5:
                continue
            med = sorted(vals)[1]
            for slot, (vertex, value) in enumerate(zip(ids, vals)):
                shifted = value
                while shifted - med > limit:
                    shifted -= 1.0
                while med - shifted > limit:
                    shifted += 1.0
                if abs(shifted - value) <= 1e-6:
                    continue
                others = [item for index, item in enumerate(vals) if index != slot]
                if abs(others[0] - others[1]) > 0.25:
                    continue
                moved = list(vals)
                moved[slot] = shifted
                if max(moved) - min(moved) < 0.02:
                    continue
                pos.append(pos[vertex])
                pair = [uv[vertex][0], uv[vertex][1]]
                pair[axis] = shifted
                uv.append((pair[0], pair[1]))
                idx[tri + slot] = len(pos) - 1
                ids[slot] = idx[tri + slot]
                vals[slot] = shifted
    return pos, uv, idx


def _part_from(name, positions, indices, uvs, material: Material | None, material_index: int, lod: int = 0) -> Part:
    sampler, texture = ("", "")
    if material and material.albedo:
        sampler, texture = material.albedo[0]
    if uvs and indices:
        positions, uvs, indices = _close_uv_seams(positions, uvs, indices)
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
        lod=lod,
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


def _alpha_from_render_strings(states: list[str]) -> tuple[str, float, bool, str]:
    """v10 render state is a fixed string array, not the named gsys records.

    Index 10 is cull (``front`` / ``both``), 11 is the alpha mode
    (``opaque`` / ``mask`` / ``translucent`` / ``custom``), and 15 is the
    alpha test (``true`` on a mask).
    """

    def at(index: int) -> str:
        if index >= len(states) or not states[index]:
            return ""
        return states[index].strip().lower()

    cull = at(10)
    mode = at(11)
    test = at(15)
    sided = True
    if cull:
        if any(token in cull for token in ("both", "double", "none", "frontandback")):
            sided = True
        elif "front" in cull or "back" in cull:
            sided = False
    alpha = "opaque"
    if "translucen" in mode or mode == "blend":
        alpha = "blend"
    elif any(token in mode for token in ("mask", "clip", "cutout")) or test in {"true", "1", "yes"}:
        alpha = "mask"
    return alpha, 0.5, sided, mode


def _shape_key(part: Part) -> tuple:
    points = tuple((round(x, 4), round(y, 4), round(z, 4)) for x, y, z in part.positions)
    triangles = []
    for index in range(0, len(part.indices) - 2, 3):
        triangles.append(tuple(sorted(part.indices[index : index + 3])))
    return points, tuple(sorted(triangles))


def _union_bounds(boxes: list[tuple]) -> dict | None:
    usable = [box for box in boxes if box and len(box) == 2]
    if not usable:
        return None
    return {
        "min": [min(box[0][axis] for box in usable) for axis in range(3)],
        "max": [max(box[1][axis] for box in usable) for axis in range(3)],
    }


def _drop_opaque_twins(parts: list[Part]) -> list[Part]:
    """Drop an opaque shape that sits on the same vertices as a masked one.

    FarView cards are stored twice: an opaque material and a masked twin
    (the shaking card). The opaque copy draws the punch-through sky as black.
    """
    masked = {_shape_key(part) for part in parts if part.alpha == "mask" and part.positions}
    if not masked:
        return parts
    kept = [part for part in parts if not (part.alpha == "opaque" and _shape_key(part) in masked)]
    return kept or parts


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
    shader: dict | None = None,
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
    assign_ptr = param_array = param_dict_at = param_data = 0
    param_count = 0
    if shader:
        assign_ptr, param_array, param_dict_at, param_data, param_count = _pack_shader(blob, add_string, add_dict, shader)
    mat_at = len(blob)
    blob.extend(b"FMAT")
    blob.extend(struct.pack("<I", 1))
    blob.extend(struct.pack("<Q", mat_name))
    blob.extend(struct.pack("<QQ", render_at, render_dict))
    blob.extend(struct.pack("<Q", assign_ptr))  # shader assign
    blob.extend(struct.pack("<Q", 0))  # runtime textures
    blob.extend(struct.pack("<Q", tex_ptr))
    blob.extend(struct.pack("<Q", 0))  # sampler array
    blob.extend(struct.pack("<QQ", 0, sampler_dict_at))
    blob.extend(struct.pack("<QQ", param_array, param_dict_at))  # shader params
    blob.extend(struct.pack("<Q", param_data))  # param data
    blob.extend(struct.pack("<QQ", 0, 0))  # user data
    blob.extend(struct.pack("<Q", 0))  # volatile
    blob.extend(struct.pack("<Q", 0))  # user pointer
    blob.extend(struct.pack("<QQ", 0, 0))  # slots
    blob.extend(struct.pack("<HH", 0, render_count))
    blob.extend(bytes((sampler_count & 0xFF, texture_count & 0xFF)))
    blob.extend(struct.pack("<HHHHH", param_count, 0, 0, 0, 0))
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


def sample_v10_bfres(
    name: str,
    materials: list[dict],
    *,
    shapes: list[dict] | None = None,
    positions: list[tuple[float, float, float]] | None = None,
    indices: list[int] | None = None,
    with_uvs: bool = True,
    pool_name: str = "",
    decoy_dict: bool = False,
    far_lod: bool = False,
) -> bytes:
    """A version-10 BFRES with stride-0xB0 materials. Synthetic, for tests.

    Each material dict may set ``name``, ``samplers`` (``(slot, texture)`` in
    file order), ``mode`` (``opaque`` / ``mask`` / ``translucent``), and
    ``cull`` (``front`` / ``both``). Shapes are ``{name, material}`` and share
    one triangle unless ``positions`` is passed. ``with_uvs`` false writes only
    ``_p0``, which is the dummy terrain layout.
    """
    if not materials:
        raise FresError("a v10 sample needs at least one material")
    if positions is None:
        positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    if indices is None:
        indices = [0, 1, 2]
    if shapes is None:
        shapes = [{"name": str(item.get("name") or f"shape{index}"), "material": index} for index, item in enumerate(materials)]
    uvs = [(0.0, 0.0) for _ in positions] if with_uvs else []
    if with_uvs and len(uvs) >= 3:
        uvs[1] = (1.0, 0.0)
        uvs[2] = (0.0, 1.0)

    blob = bytearray(0x100)
    blob[:8] = b"FRES    "
    struct.pack_into("<I", blob, 0x08, 0x000A0000)
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
    file_name = add_string(name)
    if pool_name:
        add_string(pool_name)
    pos_name = add_string("_p0")
    uv_name = add_string("_u0") if with_uvs else 0
    shape_names = [add_string(str(item.get("name") or f"shape{index}")) for index, item in enumerate(shapes)]
    mat_names = [add_string(str(item.get("name") or f"material{index}")) for index, item in enumerate(materials)]

    prepared = []
    for item, mat_name in zip(materials, mat_names):
        pairs = list(item.get("samplers") or [])
        tex_offs = [add_string(str(texture)) for _slot, texture in pairs]
        samp_offs = [add_string(str(slot)) for slot, _texture in pairs]
        mode = str(item.get("mode") or "opaque")
        cull = str(item.get("cull") or "both")
        words = [""] * 16
        words[10] = cull
        words[11] = mode
        words[15] = "true" if mode == "mask" else "false"
        state_offs = [add_string(word) for word in words]
        prepared.append((mat_name, pairs, tex_offs, samp_offs, state_offs, item.get("shader") or None))

    align(8)
    attr_at = len(blob)
    blob.extend(struct.pack("<Q", pos_name) + struct.pack(">H", _F32_3) + b"\0\0" + struct.pack("<HH", 0, 0))
    if with_uvs:
        blob.extend(struct.pack("<Q", uv_name) + struct.pack(">H", _F32_2) + b"\0\0" + struct.pack("<HH", 12, 0))
        attr_dict = add_dict([pos_name, uv_name])
        attr_count = 2
        stride = 20
    else:
        attr_dict = add_dict([pos_name])
        attr_count = 1
        stride = 12

    vertex = bytearray()
    if with_uvs:
        for (x, y, z), (u, v) in zip(positions, uvs):
            vertex += struct.pack("<3f2f", x, y, z, u, v)
    else:
        for x, y, z in positions:
            vertex += struct.pack("<3f", x, y, z)
    index_blob = b"".join(struct.pack("<H", index) for index in indices)

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
    blob.extend(bytes((attr_count, 1)))
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
    lod_levels = 2 if far_lod and len(indices) > 3 else 1
    for level in range(lod_levels):
        count = len(indices) if level == 0 else 3
        # The near mesh uses its submesh range. The far mesh has no submesh
        # list, so the reader takes the shorter index count on the header.
        blob.extend(struct.pack("<QQQQ", sub_at, 0, 0, index_info))
        blob.extend(struct.pack("<IIIII", 0, 3, 1, count, 0))
        blob.extend(struct.pack("<HH", 1 if level == 0 else 0, 0))

    align(8)
    shape_at = len(blob)
    for index, item in enumerate(shapes):
        material_index = int(item.get("material", index))
        blob.extend(b"FSHP")
        blob.extend(struct.pack("<I", 0))
        blob.extend(struct.pack("<Q", shape_names[index]))
        blob.extend(struct.pack("<Q", fvtx_at))
        blob.extend(struct.pack("<Q", mesh_at))
        blob.extend(struct.pack("<Q", 0))
        blob.extend(struct.pack("<QQ", 0, 0))
        blob.extend(struct.pack("<QQQ", 0, 0, 0))
        blob.extend(struct.pack("<HHHH", 0, material_index, 0, 0))
        blob.extend(struct.pack("<H", 0))
        blob.extend(bytes((1, lod_levels, 0, 0)))
        blob.extend(b"\0\0")
    shape_dict = add_dict(shape_names)

    built = []
    for mat_name, pairs, tex_offs, samp_offs, state_offs, shader in prepared:
        align(8)
        tex_ptr = 0
        if tex_offs:
            tex_ptr = len(blob)
            for off in tex_offs:
                blob.extend(struct.pack("<Q", off))
        state_ptr = 0
        if state_offs:
            align(8)
            state_ptr = len(blob)
            for off in state_offs:
                blob.extend(struct.pack("<Q", off))
        sampler_dict = add_dict(samp_offs) if samp_offs else 0
        assign_ptr = param_array = param_dict_at = param_data = 0
        if shader:
            assign_ptr, param_array, param_dict_at, param_data, _count = _pack_shader(blob, add_string, add_dict, shader)
        built.append(
            (mat_name, tex_ptr, sampler_dict, state_ptr, len(samp_offs), len(tex_offs), len(state_offs), assign_ptr, param_array, param_dict_at, param_data)
        )

    align(8)
    mat_at = len(blob)
    for mat_name, tex_ptr, sampler_dict, state_ptr, sampler_count, texture_count, state_count, assign_ptr, param_array, param_dict_at, param_data in built:
        header = bytearray(0xB0)
        header[0:4] = b"FMAT"
        struct.pack_into("<I", header, 0x04, 1)
        struct.pack_into("<Q", header, 0x08, mat_name)
        struct.pack_into("<Q", header, 0x10, assign_ptr)
        struct.pack_into("<Q", header, 0x20, tex_ptr)
        struct.pack_into("<Q", header, 0x38, sampler_dict)
        struct.pack_into("<Q", header, 0x40, state_ptr)
        struct.pack_into("<Q", header, 0x48, param_array)
        struct.pack_into("<Q", header, 0x50, param_dict_at)
        struct.pack_into("<Q", header, 0x58, param_data)
        # Real files store 0 or 1 here. It is not the render-state array length.
        struct.pack_into("<H", header, 0xA0, 1 if state_count else 0)
        header[0xA2] = sampler_count & 0xFF
        header[0xA3] = texture_count & 0xFF
        blob.extend(header)
    mat_dict = add_dict(mat_names)
    # A one-name dictionary in the qword after the real dict. Readers that still
    # take FMDL+0x48 as the material dictionary stop after the first material.
    decoy_at = add_dict(mat_names[:1]) if decoy_dict and mat_names else 0

    align(8)
    fmdl_at = len(blob)
    blob.extend(b"FMDL")
    blob.extend(struct.pack("<I", 0))
    blob.extend(struct.pack("<Q", model_name))
    blob.extend(struct.pack("<Q", file_name))
    blob.extend(struct.pack("<Q", 0))
    blob.extend(struct.pack("<Q", fvtx_at))
    blob.extend(struct.pack("<QQ", shape_at, shape_dict))
    blob.extend(struct.pack("<QQQ", mat_at, mat_dict, decoy_at))
    blob.extend(struct.pack("<QQ", 0, 0))
    blob.extend(struct.pack("<Q", 0))
    blob.extend(struct.pack("<HHH", 1, len(shapes), len(materials)))
    blob.extend(struct.pack("<HHH", 0, 0, 0))
    blob.extend(struct.pack("<I", 0))
    model_dict = add_dict([model_name])

    align(8)
    info_at = len(blob)
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
    struct.pack_into("<H", blob, 0xDC, 1)
    struct.pack_into("<I", blob, 0x1C, len(blob))
    return bytes(blob)
