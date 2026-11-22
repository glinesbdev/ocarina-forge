"""The native extension matches the Python encoder and decoder, and falls back."""

import math

import pytest

import ocarina_forge.extension as extension
from ocarina_forge.formats.bntx import _channels, _decode, _decode_swizzle
from ocarina_forge.formats.gpu import _encode_bc7_py, encode_bc7
from ocarina_forge.formats.swizzle import _move, deswizzle, fill_partial_gob, swizzle


def _without_native(fn):
    saved = extension.module()
    extension._MODULE = None
    extension._TRIED = True
    try:
        return fn()
    finally:
        extension._MODULE = saved
        extension._TRIED = True


def _psnr(left: bytes, right: bytes) -> float:
    if left == right:
        return 99.0
    count = min(len(left), len(right)) // 4
    error = 0
    for index in range(count):
        at = index * 4
        for channel in range(4):
            delta = int(left[at + channel]) - int(right[at + channel])
            error += delta * delta
    mse = error / (count * 4)
    if mse <= 0:
        return 99.0
    return 10 * math.log10((255 * 255) / mse)


def test_python_bc7_fallback_matches_the_pure_python_encoder():
    rgba = bytes([40, 90, 20, 255]) * 64
    encoded = _without_native(lambda: encode_bc7(8, 8, rgba))
    assert encoded == _encode_bc7_py(8, 8, rgba)


@pytest.mark.skipif(extension.module() is None, reason="ocarina_forge_native is not built")
def test_rust_bc7_matches_python_and_clears_the_psnr_bar():
    import texture2ddecoder

    from ocarina_forge.formats.bntx import _bgra_to_rgba

    images = [
        bytes([180, 40, 30, 255]) * 16,
        bytes((index * 13) % 256 for index in range(32 * 32 * 4)),
        bytes([0, 1, 254, 255]) * 16,
    ]
    for rgba, width in ((images[0], 4), (images[1], 32), (images[2], 4)):
        rust = encode_bc7(width, width, rgba)
        python = _encode_bc7_py(width, width, rgba)
        assert rust == python
        decoded = _bgra_to_rgba(bytes(texture2ddecoder.decode_bc7(rust, width, width)), width, width)
        reference = _bgra_to_rgba(bytes(texture2ddecoder.decode_bc7(python, width, width)), width, width)
        assert _psnr(decoded, reference) >= 40


@pytest.mark.skipif(extension.module() is None, reason="ocarina_forge_native is not built")
def test_rust_deswizzle_and_partial_gob_match_python():
    samples = (
        (64, 48, 1, 1, 4, 2, None),
        (40, 50, 8, 5, 16, 0, None),
        (32, 32, 4, 4, 16, 0, "tegra"),
        (32, 32, 4, 4, 16, 0, "switch2"),
        (16, 16, 4, 4, 8, 1, None),
    )
    for width, height, block_w, block_h, bytes_per, log2, order in samples:
        count = ((width + block_w - 1) // block_w) * ((height + block_h - 1) // block_h) * bytes_per
        linear = bytes((index * 17) % 251 for index in range(count))
        packed = _move(width, height, block_w, block_h, bytes_per, 0, log2, linear, True, order)
        assert swizzle(width, height, block_w, block_h, bytes_per, 0, log2, linear, order) == packed
        assert deswizzle(width, height, block_w, block_h, bytes_per, 0, log2, packed, order) == _move(
            width, height, block_w, block_h, bytes_per, 0, log2, packed, False, order
        )
    blocks = bytearray(20 * 9 * 8)
    blocks[8 * 8 : 9 * 8] = b"\xab" * 8
    assert fill_partial_gob(bytes(blocks), 80, 36, 4, 4, 8) == _without_native(
        lambda: fill_partial_gob(bytes(blocks), 80, 36, 4, 4, 8)
    )


@pytest.mark.skipif(extension.module() is None, reason="ocarina_forge_native is not built")
def test_rust_decode_matches_python_including_bc1_punch_and_astc():
    green = bytes([0xFC, 0xFD, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 255, 0, 0, 255, 0, 0, 255, 255])
    red = bytes([0xFC, 0xFD, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0, 255, 255, 0, 0, 0, 255, 255])
    cases = [
        ("bc1", 4, 4, bytes([0x00, 0xF8, 0x00, 0xE8, 0x00, 0x00, 0x00, 0x00]), None),
        ("bc1", 4, 4, bytes([0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]), None),
        ("bc1", 4, 4, bytes([0x1F, 0x00, 0x00, 0xF8, 0x0C, 0x00, 0x00, 0x00]), None),
        ("bc3", 4, 4, bytes(range(16)), None),
        ("bc4", 4, 4, bytes([255, 0, 0, 0, 0, 0, 0, 0]), None),
        ("bc7", 8, 8, _encode_bc7_py(8, 8, bytes([20, 30, 40, 255]) * 64), None),
        ("rgba8", 4, 4, bytes([1, 2, 3, 4]) * 16, None),
        ("astc8x5", 16, 10, green + red + green + red, (8, 5)),
    ]
    for codec, width, height, blocks, footprint in cases:
        rust = _decode(codec, width, height, blocks, 0, footprint=footprint)
        python = _without_native(lambda codec=codec, width=width, height=height, blocks=blocks, footprint=footprint: _decode(
            codec, width, height, blocks, 0, footprint=footprint
        ))
        assert rust[1] == python[1]
        assert rust[0] == python[0]
        assert _psnr(rust[0], python[0]) >= 40
    swapped = bytes((4, 3, 2, 1))
    rgba = bytes(range(64))
    assert _channels(rgba, 4, 4, swapped) == _without_native(lambda: _channels(rgba, 4, 4, swapped))
    native = extension.module()
    hit = native.decode_swizzle("rgba8", 4, 4, 1, 1, 4, 1, 0, rgba, b"")
    python = _without_native(lambda: _decode_swizzle("rgba8", 4, 4, 1, 1, 4, 1, 0, rgba, 0x0B01, b""))
    assert bytes(hit[0]) == python[0]
    assert hit[1] == python[1]
    assert hit[2] == python[2]


def test_a_repeated_texture_load_reads_the_file_once(tmp_path, monkeypatch):
    from ocarina_forge.dump import Dump
    from ocarina_forge.fixtures import build_fixture
    from ocarina_forge.models import _LOAD_MEM, _READ_MEM, _load_named

    dump = Dump(build_fixture(tmp_path / "dump"))
    reads = {"n": 0}
    original = dump.read_file

    def counting(relative):
        reads["n"] += 1
        return original(relative)

    monkeypatch.setattr(dump, "read_file", counting)
    load = _LOAD_MEM.set({})
    read = _READ_MEM.set({})
    try:
        raws = []
        first = _load_named(dump, None, "Npc_Kokiri_F_01_Alb", [], "opaque", 0.5, {}, raws)
        second = _load_named(dump, None, "Npc_Kokiri_F_01_Alb", [], "opaque", 0.5, {}, raws)
        _load_named(dump, None, "Npc_Kokiri_F_01_Alb", [], "mask", 0.5, {}, raws)
    finally:
        _LOAD_MEM.reset(load)
        _READ_MEM.reset(read)
    assert first["image"] is not None and first["image"].rgba
    assert second["image"].rgba == first["image"].rgba
    assert reads["n"] == 1
    assert len(raws) == 3

    reads["n"] = 0
    raws = []
    _load_named(dump, None, "Npc_Kokiri_F_01_Alb", [], "opaque", 0.5, {}, raws)
    _load_named(dump, None, "Npc_Kokiri_F_01_Alb", [], "opaque", 0.5, {}, raws)
    assert reads["n"] == 2


def _png_pixels(blob: bytes) -> bytes:
    import struct
    import zlib

    assert blob.startswith(b"\x89PNG")
    at = 8
    width = height = 0
    payload = b""
    while at + 8 <= len(blob):
        size = struct.unpack_from(">I", blob, at)[0]
        tag = blob[at + 4 : at + 8]
        data = blob[at + 8 : at + 8 + size]
        if tag == b"IHDR":
            width, height = struct.unpack_from(">II", data)
        elif tag == b"IDAT":
            payload += data
        elif tag == b"IEND":
            break
        at += 12 + size
    rows = zlib.decompress(payload)
    stride = width * 4
    out = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        assert rows[start] == 0
        out += rows[start + 1 : start + 1 + stride]
    return bytes(out)


def test_python_pixel_fallbacks_match_the_pure_python_bodies():
    from ocarina_forge.formats.gltf import limit_rgba, png_rgba
    from ocarina_forge.formats.gpu import _downsample, encode_bc1
    from ocarina_forge.models import _has_near_magenta

    rgba = bytes((index * 19) % 256 for index in range(8 * 6 * 4))
    shrunk = _without_native(lambda: limit_rgba(8, 6, rgba, 4))
    assert shrunk[0] == 4 and len(shrunk[2]) == shrunk[0] * shrunk[1] * 4
    assert _png_pixels(png_rgba(8, 6, rgba)) == rgba
    assert _without_native(lambda: encode_bc1(8, 8, rgba + rgba[: 8 * 2 * 4]))
    assert _without_native(lambda: _downsample(8, 6, rgba))[0] == 4
    assert _without_native(lambda: _has_near_magenta(rgba)) is False


@pytest.mark.skipif(extension.module() is None, reason="ocarina_forge_native is not built")
def test_rust_pixel_loops_match_python():
    from ocarina_forge.formats.bntx import Texture, _noise
    from ocarina_forge.formats.gltf import limit_rgba, png_rgba
    from ocarina_forge.formats.gpu import _downsample, _mip_chain, encode_bc1
    from ocarina_forge.models import _has_near_magenta
    from ocarina_forge.surface import pack_layer

    images = []
    for width, height, limit in ((8, 4, 2), (17, 13, 8), (100, 60, 30), (64, 40, 1024)):
        rgba = bytes((index * 13 + (index // 7)) % 256 for index in range(width * height * 4))
        images.append((width, height, rgba, limit))
    for width, height, rgba, limit in images:
        rust = limit_rgba(width, height, rgba, limit)
        python = _without_native(lambda width=width, height=height, rgba=rgba, limit=limit: limit_rgba(width, height, rgba, limit))
        assert rust == python
        assert _png_pixels(png_rgba(width, height, rgba)) == rgba
        assert _downsample(width, height, rgba) == _without_native(
            lambda width=width, height=height, rgba=rgba: _downsample(width, height, rgba)
        )

    block = bytes((index * 17) % 256 for index in range(32 * 32 * 4))
    assert encode_bc1(32, 32, block) == _without_native(lambda: encode_bc1(32, 32, block))
    odd = bytes((index * 3) % 256 for index in range(6 * 6 * 4))
    assert encode_bc1(6, 6, odd) == _without_native(lambda: encode_bc1(6, 6, odd))
    image = Texture(name="t", width=32, height=32, rgba=block, fmt=0, codec="bc1", blocks=encode_bc1(32, 32, block))
    rust_chain = _mip_chain(image)
    python_chain = _without_native(lambda: _mip_chain(image))
    assert rust_chain[0] == python_chain[0]
    assert rust_chain[3] == python_chain[3]

    plain = bytes([20, 30, 40, 255]) * 8
    dash = bytearray(plain)
    dash[4:8] = bytes([200, 10, 210, 255])
    assert _has_near_magenta(plain) is False
    assert _has_near_magenta(bytes(dash)) is True
    assert _has_near_magenta(bytes(dash)) == _without_native(lambda: _has_near_magenta(bytes(dash)))
    noisy = bytes((index * 9) % 256 for index in range(32 * 24 * 4))
    assert _noise(noisy, 32, 24) == _without_native(lambda: _noise(noisy, 32, 24))

    rough = Texture(
        name="rough",
        width=6,
        height=4,
        rgba=bytes((index * 11) % 256 for index in range(6 * 4 * 4)),
        fmt=0,
    )
    ao = Texture(name="ao", width=3, height=5, rgba=bytes((80 + index) % 256 for index in range(3 * 5 * 4)), fmt=0)
    packed = pack_layer([("_rgh0", "roughness", rough), ("_ao0", "ao", ao)])
    python_pack = _without_native(lambda: pack_layer([("_rgh0", "roughness", rough), ("_ao0", "ao", ao)]))
    assert packed[0].rgba == python_pack[0].rgba
    assert packed[0].width == python_pack[0].width


def _same_rows(got, expected) -> bool:
    if len(got) != len(expected):
        return False
    for left, right in zip(got, expected):
        if len(left) != len(right):
            return False
        for a, b in zip(left, right):
            if math.isnan(a) and math.isnan(b):
                continue
            if a != b:
                return False
    return True


def _f64s(raw: bytes) -> list[float]:
    import array

    values = array.array("d")
    values.frombytes(raw)
    return list(values)


def _u32s(raw: bytes) -> list[int]:
    import array

    values = array.array("I")
    values.frombytes(raw)
    return list(values)


def test_python_mesh_fallback_still_reads_a_vertex():
    from ocarina_forge.formats.bfres import _F32_3, _decode

    blob = struct_f32(1.0, 2.0, 3.0)
    assert _without_native(lambda: _decode(blob, 0, _F32_3)) == (1.0, 2.0, 3.0)


def struct_f32(*values: float) -> bytes:
    import struct

    return struct.pack("<" + "f" * len(values), *values)


@pytest.mark.skipif(extension.module() is None, reason="ocarina_forge_native is not built")
def test_rust_attributes_and_triangles_match_python():
    import struct

    from ocarina_forge.formats.bfres import (
        _F16,
        _F16_2,
        _F16_4,
        _F32,
        _F32_2,
        _F32_3,
        _F32_4,
        _S16_2,
        _S8_4,
        _SN10,
        _U16_2,
        _U8,
        _U8_2,
        _U8_4,
        _UN10,
        _decode,
        _format_size,
        _triangles,
    )

    native = extension.module()
    formats = (
        _F32,
        _F32_2,
        _F32_3,
        _F32_4,
        _F16,
        _F16_2,
        _F16_4,
        _U8,
        _U8_2,
        _U8_4,
        _S8_4,
        _U16_2,
        _S16_2,
        _SN10,
        _UN10,
    )
    for fmt in formats:
        size = _format_size(fmt)
        count = 6
        stride = size + 3
        offset = 2
        body = bytearray()
        for index in range(count):
            body += bytes((index * 17 + slot * 3) % 256 for slot in range(size))
            body += b"\xab\xcd\xef"
        blob = b"\x11\x22" + bytes(body)
        python = [_decode(blob, index * stride + offset, fmt) for index in range(count)]
        raw = native.decode_attribute(blob, count, stride, offset, fmt)
        flat = _f64s(raw)
        width = len(python[0])
        got = [tuple(flat[index * width : (index + 1) * width]) for index in range(count)]
        assert _same_rows(got, python)
        finite, low, high = native.attribute_bounds(blob, count, stride, offset, fmt)
        kept = [row for row in python if all(math.isfinite(value) for value in row)]
        assert finite == len(kept)
        if kept:
            assert _f64s(low) == [min(row[slot] for row in kept) for slot in range(width)]
            assert _f64s(high) == [max(row[slot] for row in kept) for slot in range(width)]

    signed = bytes([0x80, 0, 127, 1])
    assert native.decode_attribute(signed, 1, 0, 0, _S8_4)
    assert _f64s(native.decode_attribute(signed, 1, 0, 0, _S8_4))[0] == -128 / 127.0
    half = struct.pack("<HHHH", 0x3C00, 0xC000, 0x0001, 0x7C00)
    values = _f64s(native.decode_attribute(half, 1, 0, 0, _F16_4))
    assert values[0] == 1.0
    assert values[1] == -2.0
    assert 0 < values[2] < 1e-6
    assert values[3] == float("inf")
    assert native.decode_attribute(b"\x00\x00", 4, 0, 0, _F32) == b""

    nan = struct.pack("<fff", float("nan"), 1.0, 1.0)
    one = struct.pack("<fff", 2.0, 3.0, 4.0)
    finite, low, high = native.attribute_bounds(nan + one, 2, 12, 0, _F32_3)
    assert finite == 1
    assert _f64s(low) == [2.0, 3.0, 4.0]
    assert _f64s(high) == [2.0, 3.0, 4.0]

    strip = bytes([0, 1, 2, 3, 3])
    assert _u32s(native.decode_triangles(strip, 1, [0, 5], 10, 4)) == [index + 10 for index in _triangles([0, 1, 2, 3, 3], 4)]
    listed = bytes([0, 1, 2, 3, 4, 5, 6])
    assert _u32s(native.decode_triangles(listed, 1, [0, 7], 0, 3)) == _triangles(list(listed), 3)
    assert native.decode_triangles(listed, 1, [0, 3], 0, 1) == b""


def test_face_normals_match_python_and_skip_a_bad_index():
    from ocarina_forge.formats.gltf import face_normals

    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 1.0)]
    indices = [0, 1, 2, 1, 3, 2, 9, 9, 9]
    rust = face_normals(positions, indices)
    python = _without_native(lambda: face_normals(positions, indices))
    assert rust == python
    assert abs(rust[0][1]) == 1.0
    flat = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
    assert face_normals(flat, [0, 1, 2]) == [(0.0, 1.0, 0.0)] * 3


def test_packed_bind_pose_seams_and_bounds_match_python():
    from ocarina_forge.formats.bfres import Vecs, _bind_pose, _close_uv_seams, _euler_quat

    native = extension.module()
    if native is None or not hasattr(native, "transform_positions"):
        pytest.skip("native extension is not built")
    quat = _euler_quat(0.0, math.pi / 2, 0.0)
    bones = [
        (-1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), 0, 0),
        (0, (0.0, 0.0, 0.0), quat, (1.0, 1.0, 1.0), 0x1000, 0),
        (0, (4.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), 0, -1),
    ]
    points = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    rust = _bind_pose(points, bones, [1, 2], 0, [0, 1])
    python = _without_native(lambda: _bind_pose(points, bones, [1, 2], 0, [0, 1]))
    assert isinstance(rust, Vecs)
    for got, want in zip(rust, python, strict=True):
        assert got == pytest.approx(want)
    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    uvs = [(0.02, 0.2), (0.98, 0.2), (0.04, 0.8)]
    colors = [[(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)]]
    rust_seam = _close_uv_seams(positions, uvs, [0, 1, 2], colors)
    colors_py = [[(1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)]]
    python_seam = _without_native(lambda: _close_uv_seams(positions, uvs, [0, 1, 2], colors_py))
    assert rust_seam[2] == python_seam[2]
    assert len(rust_seam[0]) == len(python_seam[0])
    for got, want in zip(rust_seam[0], python_seam[0], strict=True):
        assert got == pytest.approx(want)
    for got, want in zip(rust_seam[1], python_seam[1], strict=True):
        assert got == pytest.approx(tuple(want))
    assert colors[0][-1] == pytest.approx(colors_py[0][-1])
    packed = Vecs.from_bytes(3, Vecs(3, __import__("array").array("d", [1.0, -2.0, 3.5, 4.0, 0.0, -1.0])).bytes())
    assert packed.bounds()[0] == pytest.approx([1.0, -2.0, -1.0])
    assert packed.bounds()[1] == pytest.approx([4.0, 0.0, 3.5])
    assert native.rgba_opaque(bytes([1, 2, 3, 255, 4, 5, 6, 250])) is True
    assert native.rgba_opaque(bytes([1, 2, 3, 249])) is False


def test_glb_float_pack_matches_struct():
    import struct

    from ocarina_forge.formats.gltf import _pack_f32, _pack_indices

    rows = [(1.5, -2.25, 0.1), (math.pi, 1e-8, -0.0)]
    assert _pack_f32(rows, 3) == b"".join(struct.pack("<3f", *row) for row in rows)
    uvs = [(0.0, 1.0), (0.5, -0.25)]
    assert _pack_f32(uvs, 2) == b"".join(struct.pack("<2f", *row) for row in uvs)
    assert _pack_indices([0, 1, 70000], False) == struct.pack("<III", 0, 1, 70000)
    assert _pack_indices([0, 1, 2], True) == struct.pack("<HHH", 0, 1, 2)
