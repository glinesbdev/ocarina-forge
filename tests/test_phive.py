"""Exercise the Havok shape layout without relying on a retail file."""

import struct

from ocarina_forge.formats.phive import read_mesh_shape


def _chunk(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _shape(version=b"20250200", primitive=b"\0\1\2\3"):
    data = bytearray(256)
    struct.pack_into("<3f", data, 0x50, 0.5, 0.5, 0.5)
    struct.pack_into("<iI", data, 0x88, 160 - 0x88, 1)
    struct.pack_into("<iI", data, 168, 224 - 168, 1)
    struct.pack_into("<iI", data, 176, 228 - 176, 24)
    struct.pack_into("<3i", data, 192, -2, 0, 4)
    data[224:228] = primitive
    struct.pack_into("<12H", data, 228, 2, 0, 0, 2, 0, 4, 6, 0, 4, 6, 0, 0)
    strings = b"hknpMeshShape\0hknpMeshShape::GeometrySection\0hknpMeshShape::GeometrySection::Primitive\0hkUint8\0"
    types = _chunk(b"TST1", strings) + _chunk(b"TNA1", bytes([5, 0, 0, 1, 0, 2, 0, 3, 0]))
    items = b"".join(struct.pack("<III", kind, offset, count)
                     for kind, offset, count in ((1, 0, 1), (2, 160, 1), (3, 224, 1), (4, 228, 24)))
    tag = _chunk(b"TAG0", _chunk(b"SDKV", version) + _chunk(b"DATA", data)
                 + _chunk(b"TYPE", types) + _chunk(b"INDX", _chunk(b"ITEM", items)))
    wrapper = bytearray(48)
    wrapper[:6] = b"Phive\0"
    struct.pack_into("<I", wrapper, 12, len(wrapper))
    struct.pack_into("<I", wrapper, 28, len(tag) + 8)
    return bytes(wrapper) + tag + bytes(8)


def test_shape_applies_section_origin_and_splits_quads():
    assert read_mesh_shape(_shape()) == [
        [0, 0, 2, 0, 0, 4, 2, 0, 4],
        [0, 0, 2, 2, 0, 4, 2, 0, 2],
    ]
    assert len(read_mesh_shape(_shape(primitive=b"\0\1\2\2"))) == 1


def test_shape_ignores_unsupported_or_invalid_collision():
    assert read_mesh_shape(_shape(version=b"20230100")) == []
    assert read_mesh_shape(_shape()[:-9]) == []
    assert read_mesh_shape(_shape(primitive=b"\0\1\2\xff")) == []
