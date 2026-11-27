from io import BytesIO
import os
import struct
import threading

from PIL import Image

from ocarina_forge.formats.gpu import ktx2, pack_texture, split_texture
from ocarina_forge.formats.gltf import join_glb, split_glb
from ocarina_forge.shared_models import externalize_images, publish_texture_bytes, read_shared_texture, reduced_texture, shared_texture_path


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


def test_concurrent_publishes_of_one_texture_share_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path))
    import ocarina_forge.shared_models as shared

    payload = b"OCFG" + bytes(range(64))
    real = os.replace
    state = {"calls": 0}

    def flaky(src, dst):
        state["calls"] += 1
        if state["calls"] == 1:
            error = PermissionError(13, "The process cannot access the file because it is being used by another process", src, 32, dst)
            error.winerror = 32
            raise error
        return real(src, dst)

    monkeypatch.setattr(shared.os, "replace", flaky)
    barrier = threading.Barrier(8)
    errors = []
    urls = []

    def work():
        try:
            barrier.wait()
            urls.append(shared.publish_texture_bytes(payload))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(set(urls)) == 1
    path = shared.shared_texture_path(urls[0].rsplit("/", 1)[-1])
    assert path is not None and path.read_bytes() == payload
    assert not list(path.parent.glob("*.tmp"))
    before = state["calls"]
    assert shared.publish_texture_bytes(payload) == urls[0]
    assert state["calls"] == before


def _denied(src, dst):
    error = PermissionError(
        13,
        "The process cannot access the file because it is being used by another process",
        src,
        32,
        dst,
    )
    error.winerror = 32
    raise error


def test_an_open_destination_with_the_same_bytes_is_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path))
    import ocarina_forge.shared_models as shared

    payload = b"OCFG-held-open"
    url = shared.publish_texture_bytes(payload)
    name = url.rsplit("/", 1)[-1]
    path = shared.shared_texture_path(name)
    holder = path.open("rb")
    calls = {"n": 0}

    def blocked(src, dst):
        calls["n"] += 1
        _denied(src, dst)

    monkeypatch.setattr(shared.os, "replace", blocked)
    try:
        assert shared.publish_texture_bytes(payload) == url
        assert calls["n"] == 0
        assert shared.read_shared_texture(name) == payload
        assert not holder.closed
    finally:
        holder.close()


def test_a_locked_destination_still_serves_the_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path))
    import ocarina_forge.shared_models as shared

    payload = b"OCFG-stat-locked"
    url = shared.publish_texture_bytes(payload)
    name = url.rsplit("/", 1)[-1]
    path = shared.shared_texture_path(name)
    holder = path.open("rb")
    real_stat = os.stat

    def locked_stat(target, *args, **kwargs):
        if os.path.abspath(target) == os.path.abspath(path):
            error = PermissionError(13, "Access is denied", target)
            error.winerror = 32
            raise error
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(shared.os, "replace", _denied)
    monkeypatch.setattr(shared.os, "stat", locked_stat)
    try:
        assert shared.publish_texture_bytes(payload) == url
        assert shared.read_shared_texture(name) == payload
        assert not holder.closed
    finally:
        holder.close()


def test_a_rename_that_never_lands_still_serves_the_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("OCARINA_FORGE_CACHE", str(tmp_path))
    import ocarina_forge.shared_models as shared

    def locked_unlink(self, missing_ok=False):
        error = PermissionError(13, "Permission denied", str(self))
        error.winerror = 32
        raise error

    monkeypatch.setattr(shared.os, "replace", _denied)
    monkeypatch.setattr(shared.Path, "unlink", locked_unlink)
    payload = b"OCFG-never-renamed"
    url = shared.publish_texture_bytes(payload)
    name = url.rsplit("/", 1)[-1]
    path = shared.shared_texture_path(name)
    assert path is not None and not path.is_file()
    assert shared.read_shared_texture(name) == payload
