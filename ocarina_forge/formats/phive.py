"""Read the per-actor Havok 2025.2 triangle meshes used by terrain fitting."""

import math
import struct


def _chunks(payload):
    offset = 0
    while offset + 8 <= len(payload):
        size = struct.unpack_from(">I", payload, offset)[0] & 0x3fffffff
        if size < 8 or size > len(payload) - offset:
            raise ValueError("Invalid Havok chunk length")
        yield bytes(payload[offset + 4:offset + 8]), payload[offset + 8:offset + size]
        offset += size


def _type_names(types):
    strings = bytes(types[b"TST1"]).split(b"\0")
    names = [None]
    catalog = iter(types[b"TNA1"])

    def varint():
        first = next(catalog)
        extra = 0
        while extra < 5 and first & (0x80 >> extra):
            extra += 1
        if extra >= 5:
            raise ValueError("Unsupported Havok type index")
        value = first & (0x7f >> extra)
        for _ in range(extra):
            value = (value << 8) | next(catalog)
        return value

    for _ in range(1, varint()):
        names.append(strings[varint()])
        for _ in range(varint()):
            varint()
            varint()
    return names


def read_mesh_shape(raw: bytes) -> list[list[float]]:
    """Return actor-local triangles from a 2025.2 Phive hknpMeshShape.

    Return an empty list for other SDKs, shape classes, and malformed files.
    """
    try:
        return _read_mesh_shape(raw)
    except (ValueError, KeyError, IndexError, StopIteration, struct.error):
        return []


def _read_mesh_shape(raw):
    if not raw.startswith(b"Phive\0"):
        return []
    start = struct.unpack_from("<I", raw, 12)[0]
    size = struct.unpack_from("<I", raw, 28)[0]
    if start < 0x30 or start + size > len(raw):
        return []
    kind, tagfile = next(_chunks(memoryview(raw)[start:start + size]))
    if kind != b"TAG0":
        return []
    tags = dict(_chunks(tagfile))
    if tags[b"SDKV"] != b"20250200":
        return []

    data = tags[b"DATA"]
    names = _type_names(dict(_chunks(tags[b"TYPE"])))
    items = dict(_chunks(tags[b"INDX"]))[b"ITEM"]
    views = {(start, count): names[kind & 0xffffff]
             for kind, start, count in struct.iter_unpack("<III", items) if kind}
    if views.get((0, 1)) != b"hknpMeshShape":
        return []

    def relative_view(field, stride, name):
        offset, count = struct.unpack_from("<iI", data, field)
        start = field + offset
        if not 0 <= start <= start + count * stride <= len(data) or views.get((start, count)) != name:
            raise ValueError("Invalid Havok array")
        return start, count

    # The 2025.2 reflected layout puts the inverse quantization scale at
    # 0x50 and the section view at 0x88. Older SDKs use different offsets.
    inverse_scale = struct.unpack_from("<3f", data, 0x50)
    if not all(math.isfinite(value) and value > 0 for value in inverse_scale):
        return []
    section_type = b"hknpMeshShape::GeometrySection"
    section_stride = 0x40
    start, count = relative_view(0x88, section_stride, section_type)
    triangles = []
    for section in range(start, start + count * section_stride, section_stride):
        primitive_start, primitive_count = relative_view(section + 8, 4, section_type + b"::Primitive")
        vertex_start, vertex_bytes = relative_view(section + 16, 1, b"hkUint8")
        origin = struct.unpack_from("<3i", data, section + 32)
        floating = origin[0] == 0x7fffffff
        vertex_format = struct.Struct("<3f" if floating else "<3H")
        vertex_end = vertex_start + vertex_bytes // vertex_format.size * vertex_format.size
        points = list(vertex_format.iter_unpack(data[vertex_start:vertex_end]))
        if floating:
            if any(not math.isfinite(value) for point in points for value in point):
                return []
        else:
            points = [tuple((value + bias) * scale for value, bias, scale in zip(point, origin, inverse_scale))
                      for point in points]
        primitives = data[primitive_start:primitive_start + primitive_count * 4]
        for a, b, c, d in struct.iter_unpack("4B", primitives):
            triangles.append([*points[a], *points[b], *points[c]])
            if c != d:
                triangles.append([*points[a], *points[c], *points[d]])
    return triangles
