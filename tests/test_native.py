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
