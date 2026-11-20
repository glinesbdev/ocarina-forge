"""BC7 blocks round-trip, and the container is not a Basis file."""

from ocarina_forge.formats.bntx import _bgra_to_rgba
from ocarina_forge.formats.gpu import VK_BC7, encode_bc7, gpu_image, ktx2, split_texture


class _Image:
    def __init__(self, rgba, codec="", blocks=b""):
        self.width = 4
        self.height = 4
        self.rgba = rgba
        self.codec = codec
        self.blocks = blocks
        self.block_w = 4
        self.block_h = 4
        self.bytes_per = 16


def test_mode_6_keeps_a_flat_colour_and_is_not_basis():
    import texture2ddecoder

    rgba = bytes([180, 40, 30, 255]) * 16
    blocks = encode_bc7(4, 4, rgba)
    decoded = _bgra_to_rgba(bytes(texture2ddecoder.decode_bc7(blocks, 4, 4)), 4, 4)
    for channel in range(3):
        assert abs(decoded[channel] - rgba[channel]) <= 1
    file = ktx2(VK_BC7, 4, 4, blocks)
    assert file[12:16] != b"\0\0\0\0"
    assert int.from_bytes(file[12:16], "little") == VK_BC7
    packed = gpu_image(_Image(rgba), "opaque")
    ktx, fallback = split_texture(packed)
    assert ktx[:4] == b"\xabKTX"
    assert fallback[:2] == b"\xff\xd8"
    assert int.from_bytes(ktx[80:88], "little") == 104


def test_a_model_albedo_is_ktx2_and_survives_meshopt(tmp_path, monkeypatch):
    from ocarina_forge.dump import Dump
    from ocarina_forge.fixtures import build_fixture
    from ocarina_forge.models import cache_dir, describe_actor

    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("OCARINA_FORGE_WORKERS", "1")
    info = describe_actor(Dump(build_fixture(tmp_path / "dump")), "Npc_Kokiri_F_01")
    glb = (cache_dir() / info["digest"][:2] / f"{info['digest']}.glb").read_bytes()
    assert b"image/ktx2" in glb
    assert b"KHR_texture_basisu" in glb
    assert b"EXT_meshopt_compression" in glb
    import json
    import struct

    from ocarina_forge.formats.gpu import split_texture

    json_len = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + json_len])
    binary_at = 20 + json_len
    binary_len = struct.unpack_from("<I", glb, binary_at)[0]
    binary = glb[binary_at + 8 : binary_at + 8 + binary_len]
    ktx_images = [image for image in document["images"] if image.get("mimeType") == "image/ktx2"]
    assert ktx_images
    view = document["bufferViews"][ktx_images[0]["bufferView"]]
    blob = binary[view["byteOffset"] : view["byteOffset"] + view["byteLength"]]
    split = split_texture(blob)
    assert split is not None
    ktx, fallback = split
    assert int.from_bytes(ktx[12:16], "little") != 0
    assert fallback[:2] in (b"\xff\xd8", b"\x89P")
