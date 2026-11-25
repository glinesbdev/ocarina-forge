"""Decode BFRES 10.4 packed meshlets using NUP Explorer's published format notes.

Positions, triangle indices, normals, UVs and the first vertex colour go
through the ordinary Forge material and glTF path.
https://github.com/lunixxx37/NupExplorer/blob/main/nupx/clusters.py
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass


class MeshletError(ValueError):
    pass


def buffer_span(data: bytes, shape: int, gpu_base: int) -> tuple[int, int]:
    """Return the file offset and byte length of a single packed meshlet buffer."""
    if len(data) < 0x0C or struct.unpack_from("<I", data, 0x08)[0] != 0x000A0400:
        raise MeshletError("meshlet decoding supports BFRES 10.4.0")
    if shape < 0 or shape + 0x20 > len(data) or data[shape : shape + 4] != b"FSHP":
        raise MeshletError("meshlet shape header is outside the file or has no FSHP signature")
    if not struct.unpack_from("<I", data, shape + 4)[0] & 0x10:
        raise MeshletError("shape does not declare meshlet data")
    descriptor, = struct.unpack_from("<Q", data, shape + 0x18)
    if not descriptor or descriptor + 0x48 > len(data):
        raise MeshletError("meshlet descriptor is missing or outside the file")
    count = data[descriptor + 0x44]
    if count != 1:
        raise MeshletError(f"meshlet descriptor has {count} buffers; expected one")
    info, offsets = struct.unpack_from("<QQ", data, descriptor + 0x10)
    if not info or info + 4 > len(data) or not offsets or offsets + 8 > len(data):
        raise MeshletError("meshlet buffer tables are missing or outside the file")
    size, = struct.unpack_from("<I", data, info)
    relative, = struct.unpack_from("<q", data, offsets)
    start = gpu_base + relative
    if gpu_base <= 0 or relative < 0 or not size or start + size > len(data):
        raise MeshletError("packed meshlet buffer is empty or outside the GPU data")
    return start, size


_RECORDS = 0x54
_XYZ_SHIFTS = (0, 21, 42)
_QUANTIZED_MASK = (1 << 21) - 1


@dataclass
class Level:
    number: int
    positions: list[tuple[float, float, float]]
    normals: list[tuple[float, float, float]]
    indices: list[int]
    uv_sets: list[list[tuple[float, float]]]
    colors: list[tuple[float, float, float, float]] | None


@dataclass
class _Record:
    corner: int
    widths: tuple[int, ...]
    normal_width: int
    attribute_start: int
    attributes: list[tuple[int, int]]
    triangles: int
    vertices: int
    offset: int
    stride: int


def _bits(word: int, offset: int, width: int) -> int:
    return (word >> offset) & ((1 << width) - 1)


def _color_width(flags: int) -> int:
    return 8 if flags & 0xF0 == 0xF0 else 32


def _color_bits(flags: int) -> int:
    return (4 - (flags & 15).bit_count()) * _color_width(flags)


def _uv_widths(flags: int) -> tuple[int, int]:
    return _bits(flags, 24, 4) + 1, _bits(flags, 28, 4) + 1


def _texture_set_count(records: list[_Record], attributes: int) -> int:
    # UV entries precede colours. The declared vertex stride constrains the split.
    candidates = set(range(attributes + 1))
    uv_hint = 0
    for _, flags in records[0].attributes:
        if flags & 0x00FF0FF0 or not flags & 15 or not _bits(flags, 12, 4):
            break
        uv_hint += 1
    for record in records:
        for count in tuple(candidates):
            uv_bits = sum(sum(_uv_widths(flags)) for _, flags in record.attributes[:count])
            color_bits = sum(_color_bits(flags) for flags, _ in record.attributes[count:])
            if record.attribute_start + uv_bits + color_bits != record.stride:
                candidates.remove(count)
        if not candidates:
            raise MeshletError("meshlet attributes disagree with the vertex bit stride")
    return min(candidates, key=lambda count: (abs(count - uv_hint), count))


def decode(data: bytes, shape: int, gpu_base: int) -> list[Level]:
    start, size = buffer_span(data, shape, gpu_base)
    descriptor, = struct.unpack_from("<Q", data, shape + 0x18)
    levels = data[descriptor + 0x45]
    if not 1 <= levels <= 16 or size < _RECORDS + 32:
        raise MeshletError("invalid meshlet LOD table")
    block = memoryview(data)[start : start + size]
    *origin, step = struct.unpack_from("<4f", block)
    limits = struct.unpack_from(f"<{levels}I", block, 0x14)
    if not all(math.isfinite(value) for value in (*origin, step)) or step <= 0:
        raise MeshletError("invalid meshlet quantization")
    if not limits[0] or any(low > high for low, high in zip(limits, limits[1:])):
        raise MeshletError("invalid cumulative meshlet counts")
    first_payload = struct.unpack_from("<I", block, _RECORDS + 0x14)[0] >> 4
    stride, remainder = divmod(first_payload, limits[-1])
    attribute_count, extra = divmod(stride - 32, 8)
    if remainder or attribute_count < 0 or extra or stride > 160 or _RECORDS + first_payload > size:
        raise MeshletError("invalid meshlet record table")

    # Pick the authored near mesh and a single coarsest nonempty level.
    far = next((level for level in range(levels - 1, 0, -1)
                if 0 < limits[level] - limits[level - 1] < limits[0]), None)
    records = []
    for index in range(limits[-1]):
        record = _RECORDS + index * stride
        corner, extent, _, triangles, vertices, offset, bits, tangent = struct.unpack_from("<QQHBBIII", block, record)
        widths = tuple(((extent >> shift) & _QUANTIZED_MASK).bit_length() for shift in _XYZ_SHIFTS)
        position_bits = sum(widths)
        normal_width = 6 + (offset & 15)
        attribute_start = position_bits + 2 * normal_width + (17 if tangent == 2 else 0)
        attributes = list(struct.iter_unpack("<II", block[record + 32 : record + stride]))
        records.append(_Record(corner, widths, normal_width, attribute_start, attributes, triangles, vertices,
                               offset >> 4, bits & 0x1FF))
    uv_count = _texture_set_count(records, attribute_count)

    result = []
    for number in (0,) if far is None else (0, far):
        level = Level(number, [], [], [], [[] for _ in range(uv_count)], [] if uv_count < attribute_count else None)
        low = limits[number - 1] if number else 0
        for record in records[low : limits[number]]:
            vertices = record.vertices
            triangles = record.triangles
            bits = record.stride
            if not 3 <= vertices <= 64 or not 1 <= triangles <= 124 or record.attribute_start > bits:
                raise MeshletError("invalid meshlet vertex or triangle record")
            index_width = (vertices - 1).bit_length()
            byte_size = (vertices * bits + 3 * triangles * index_width + 7) // 8
            payload_at = _RECORDS + record.offset
            if record.offset < first_payload or payload_at + byte_size > size:
                raise MeshletError("meshlet bitstream is outside the packed buffer")
            payload = int.from_bytes(block[payload_at : payload_at + byte_size], "little")
            base = len(level.positions)
            # Indices are local to the cluster and immediately follow the vertices.
            for index in range(3 * triangles):
                vertex = _bits(payload, vertices * bits + index * index_width, index_width)
                if vertex >= vertices:
                    raise MeshletError("meshlet triangle index is outside its vertex list")
                level.indices.append(base + vertex)
            _decode_vertices(level, payload, record, origin, step)
        result.append(level)
    return result


def _decode_vertices(level: Level, payload: int, record: _Record, origin, scale: float) -> None:
    cluster_origin = [(record.corner >> shift) & _QUANTIZED_MASK for shift in _XYZ_SHIFTS]
    sets = []
    color_start = record.attribute_start
    uv_count = len(level.uv_sets)
    color_descriptors = record.attributes[uv_count:]
    # The descriptors list UVs first, but the vertex payload places every
    # varying colour channel before the UVs, after the normal/tangent frame.
    cursor = color_start + sum(_color_bits(flags) for flags, _ in color_descriptors)
    for packed, flags in record.attributes[:uv_count]:
        offsets = struct.unpack("<hh", packed.to_bytes(4, "little"))
        powers = ((flags & 15) - 16, _bits(flags, 12, 4) - 16)
        uv_widths = _uv_widths(flags)
        sets.append((cursor, offsets, powers, uv_widths))
        cursor += sum(uv_widths)
    if level.colors is not None:
        flags, constants = color_descriptors[0]
        color_width = _color_width(flags)
    normal_scale = 2 / ((1 << record.normal_width) - 1)
    for vertex in range(record.vertices):
        data = _bits(payload, vertex * record.stride, record.stride)
        cursor = 0
        point = []
        for offset, quantized, width in zip(origin, cluster_origin, record.widths):
            point.append(offset + scale * (quantized + _bits(data, cursor, width)))
            cursor += width
        level.positions.append(tuple(point))
        x = _bits(data, cursor, record.normal_width) * normal_scale - 1
        y = _bits(data, cursor + record.normal_width, record.normal_width) * normal_scale - 1
        z = 1 - abs(x) - abs(y)
        if z < 0:
            original_x = x
            x = math.copysign(1 - abs(y), x)
            y = math.copysign(1 - abs(original_x), y)
        length = math.hypot(x, y, z)
        level.normals.append((x / length, y / length, z / length))
        for channel, (offset, (u, v), (eu, ev), (wu, wv)) in zip(level.uv_sets, sets):
            channel.append((math.ldexp(u + _bits(data, offset, wu), eu),
                            math.ldexp(v + _bits(data, offset + wu, wv), ev)))
        if level.colors is not None:
            channels = []
            cursor = color_start
            for channel in range(4):
                if flags & (1 << channel):
                    value = _bits(constants, channel * 8, 8) / 255
                else:
                    raw = _bits(data, cursor, color_width)
                    value = raw / 255 if color_width == 8 else struct.unpack("<f", raw.to_bytes(4, "little"))[0]
                    cursor += color_width
                channels.append(min(1.0, max(0.0, value)))
            level.colors.append(tuple(channels))
