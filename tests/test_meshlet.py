import struct

from ocarina_forge.formats.bfres import _File, sample_v10_bfres
from ocarina_forge.modeldebug import debug_bytes, render_text


def _meshlet_file(*, varying_colors=False):
    blob = bytearray(sample_v10_bfres("Model", [{"name": "Wall"}]))
    shape = blob.index(b"FSHP")
    gpu_base = _File(bytes(blob)).buffer_base
    descriptor = len(blob)
    info = descriptor + 0x48
    offsets = info + 0x10
    record_size = 56 if varying_colors else 48
    block = bytearray(0x54 + record_size)
    struct.pack_into("<4f", block, 0, 10, 20, 30, 0.5)
    struct.pack_into("<I", block, 0x14, 1)
    # Position and normal bits precede the optional colour payload and UVs.
    color_bits = 16 if varying_colors else 0
    vertex_bits = 16 + color_bits
    struct.pack_into("<QQHBBIII", block, 0x54, 0, 1 | (1 << 21), 0, 1, 3, record_size << 4, vertex_bits, 0)
    struct.pack_into("<IIII", block, 0x54 + 32, 0, 0x0000F00F, 0xFE if varying_colors else 0xFF, 0xFFFF00FF)
    if varying_colors:
        struct.pack_into("<II", block, 0x54 + 48, 0xFE, 0xFF000000)
    vertices = 0
    for index, (position, uv) in enumerate(((0, 0), (1, 1), (2, 2))):
        colors = (23 * index) | ((97 - index) << 8) if varying_colors else 0
        vertex = position | (colors << 14) | (uv << (14 + color_bits))
        vertices |= vertex << (index * vertex_bits)
    indices = (1 << 2) | (2 << 4)
    block.extend((vertices | (indices << (3 * vertex_bits))).to_bytes((3 * vertex_bits + 13) // 8, "little"))
    start = offsets + 8
    blob.extend(bytes(start - descriptor) + block)
    struct.pack_into("<I", blob, 8, 0x000A0400)
    struct.pack_into("<I", blob, shape + 4, 0x12)
    struct.pack_into("<QQ", blob, shape + 0x10, 0, descriptor)
    blob[shape + 0x5A] = 0
    blob[shape + 0x5B] = 0
    struct.pack_into("<QQ", blob, descriptor + 0x10, info, offsets)
    blob[descriptor + 0x44] = 1
    blob[descriptor + 0x45] = 1
    struct.pack_into("<I", blob, info, len(block))
    struct.pack_into("<q", blob, offsets, start - gpu_base)
    return blob


def test_bfres_decodes_quantized_meshlet_positions_and_indices():
    blob = bytes(_meshlet_file())
    part, = _File(blob).models()[0].parts
    assert part.positions == [(10, 20, 30), (10.5, 20, 30), (10, 20.5, 30)]
    assert part.indices == [0, 1, 2]
    assert part.normals == [(0, 0, -1)] * 3
    assert part.uvs == [(0, 0), (0.5, 0), (0, 0.5)]
    assert part.colors == [(1, 0, 1, 1)] * 3
    report = debug_bytes("sample", blob)
    shape = report["models"][0]["shapes"][0]
    assert shape["decoded"] is True
    assert shape["meshlet"] == {"vertices": 3, "triangles": 1}
    assert "meshlet geometry 3 vertices, 1 triangles" in render_text({"files": [report]}, [])


def test_bfres_reads_uvs_after_all_varying_color_payloads():
    part, = _File(bytes(_meshlet_file(varying_colors=True))).models()[0].parts
    assert part.uvs == [(0, 0), (0.5, 0), (0, 0.5)]
    assert part.colors == [(0, 0, 1, 1), (23 / 255, 0, 1, 1), (46 / 255, 0, 1, 1)]


def test_model_debug_rejects_a_truncated_packed_buffer():
    blob = _meshlet_file()
    report = debug_bytes("sample", bytes(blob[:-1]))
    shape = report["models"][0]["shapes"][0]
    assert shape["decoded"] is False
    assert "outside the GPU data" in shape["meshlet"]["error"]
