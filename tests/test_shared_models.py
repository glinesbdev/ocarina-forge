from io import BytesIO
import struct

from PIL import Image

from ocarina_forge.formats.gpu import ktx2, pack_texture, split_texture
from ocarina_forge.formats.gltf import join_glb, split_glb
from ocarina_forge.shared_models import externalize_images, reduced_texture, shared_texture_path


def test_external_images_preserve_accessor_and_meshopt_ranges():
    original = join_glb({
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 20}, {"byteLength": 24}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 5},
            {"buffer": 1, "byteLength": 24, "extensions": {"EXT_meshopt_compression": {
                "buffer": 0, "byteOffset": 8, "byteLength": 4, "byteStride": 12, "count": 2, "mode": "ATTRIBUTES",
            }}},
            {"buffer": 0, "byteOffset": 12, "byteLength": 8},
        ],
        "images": [{"bufferView": 0, "mimeType": "image/ktx2", "name": "paint"}],
        "textures": [{"extensions": {"KHR_texture_basisu": {"source": 0}}}],
        "accessors": [{"bufferView": 1}, {"bufferView": 2}],
    }, b"image\0\0\0meshfallback")
    stored = []
    def store(data, suffix):
        stored.append((bytes(data), suffix))
        return "/api/model-textures/paint.ktx2"

    revised, binary = split_glb(externalize_images(original, store))
    assert stored == [(b"image", "ktx2")]
    assert revised["images"] == [{"uri": "/api/model-textures/paint.ktx2", "mimeType": "image/ktx2", "name": "paint"}]
    assert revised["textures"][0]["extensions"]["KHR_texture_basisu"]["source"] == 0
    assert [accessor["bufferView"] for accessor in revised["accessors"]] == [0, 1]
    compressed = revised["bufferViews"][0]["extensions"]["EXT_meshopt_compression"]
    assert binary[compressed["byteOffset"]:compressed["byteOffset"] + compressed["byteLength"]] == b"mesh"
    uncompressed = revised["bufferViews"][1]
    assert binary[uncompressed["byteOffset"]:uncompressed["byteOffset"] + uncompressed["byteLength"]] == b"fallback"
    assert not shared_texture_path("../arbitrary.ktx2")



def test_reduced_packed_texture_keeps_mip_blocks_and_a_smaller_fallback():
    image = BytesIO()
    Image.new("RGBA", (1024, 512), (40, 60, 80, 128)).save(image, format="PNG")
    levels = [b"m" * 100, b"l" * 50, b"initial", b"last"]
    original = pack_texture(ktx2(145, 1024, 512, levels), image.getvalue())

    blocks, bitmap = split_texture(reduced_texture(original, "ktx2"))
    width, height = struct.unpack_from("<II", blocks, 20)
    assert (width, height) == (256, 128)
    offset, length, _ = struct.unpack_from("<QQQ", blocks, 80)
    assert blocks[offset:offset + length] == levels[2]
    with Image.open(BytesIO(bitmap)) as smaller:
        assert smaller.size == (256, 128)
        assert smaller.getpixel((0, 0))[3] == 128
    assert shared_texture_path("a" * 64 + "-mip256.ktx2") is not None
