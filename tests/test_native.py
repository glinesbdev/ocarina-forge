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
