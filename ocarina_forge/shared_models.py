"""Derived GLBs that refer to a content-addressed local texture store."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import struct
import tempfile

from PIL import Image, UnidentifiedImageError

from ocarina_forge.formats.gpu import ktx2, pack_texture, split_texture
from ocarina_forge.formats.gltf import join_glb, split_glb
from ocarina_forge.cache_prune import note_access, note_written
from ocarina_forge.models import cache_dir


VERSION = "shared-v3"
TEXTURE_STORE = "shared-v1"
INITIAL_EDGE = 256
MIME_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/ktx2": "ktx2"}


def shared_model_path(digest: str) -> Path:
    return cache_dir() / VERSION / "models" / digest[:2] / f"{digest}.glb"


def shared_texture_path(name: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{64}(?:-mip256)?\.(?:png|jpg|ktx2)", name):
        return None
    return cache_dir() / TEXTURE_STORE / "textures" / name[:2] / name


def _atomic_write(path: Path, data: bytes | bytearray | memoryview) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    note_written(path)


def _view_references(document):
    if isinstance(document, dict):
        for key, value in document.items():
            if key == "bufferView" and isinstance(value, int):
                yield document
            elif key != "extras":
                yield from _view_references(value)
    elif isinstance(document, list):
        for value in document:
            yield from _view_references(value)


def _payload(binary: bytes, region: dict) -> memoryview:
    start = region.get("byteOffset", 0)
    end = start + region["byteLength"]
    if start < 0 or end > len(binary):
        raise ValueError("GLB buffer range exceeds its BIN chunk.")
    return memoryview(binary)[start:end]


def _reduced_bitmap(data: bytes, suffix: str) -> bytes | None:
    try:
        with Image.open(BytesIO(data)) as image:
            if max(image.size) <= INITIAL_EDGE:
                return None
            image = image.convert("RGB" if suffix == "jpg" else "RGBA")
            image.thumbnail((INITIAL_EDGE, INITIAL_EDGE), Image.Resampling.LANCZOS)
            out = BytesIO()
            image.save(out, format="JPEG" if suffix == "jpg" else "PNG")
            reduced = out.getvalue()
            return reduced if len(reduced) < len(data) * 0.8 else None
    except (OSError, UnidentifiedImageError):
        return None


def reduced_texture(data: bytes, suffix: str) -> bytes | None:
    """Keep the existing compressed mip tail and a reduced bitmap fallback."""
    if suffix != "ktx2":
        return _reduced_bitmap(data, suffix)
    packed = split_texture(data)
    if not packed:
        return None
    blocks, bitmap = packed
    if len(blocks) < 104:
        return None
    vk_format, _, width, height, _, _, faces, count, compression = struct.unpack_from("<9I", blocks, 12)
    if not vk_format or not width or not height or compression or faces != 1 or not 1 < count <= 16:
        return None
    skip = 0
    while skip < count - 1 and max(width, height) > INITIAL_EDGE:
        width, height = max(1, width // 2), max(1, height // 2)
        skip += 1
    if not skip or width % 4 or height % 4 or len(blocks) < 80 + count * 24:
        return None
    levels = []
    for level in range(skip, count):
        offset, size, _ = struct.unpack_from("<QQQ", blocks, 80 + level * 24)
        if not size or offset + size > len(blocks):
            return None
        levels.append(blocks[offset:offset + size])
    fallback = _reduced_bitmap(bitmap, "png" if bitmap.startswith(b"\x89PNG") else "jpg")
    if not fallback:
        return None
    return pack_texture(ktx2(vk_format, width, height, levels), fallback)


def externalize_images(glb: bytes, store_image) -> bytes | None:
    """Keep all glTF semantics. Repack only the remaining binary ranges."""
    document, binary = split_glb(glb)
    views = document.get("bufferViews", [])
    moved = set()
    for image in document.get("images", []):
        index = image.get("bufferView")
        suffix = MIME_TYPES.get(image.get("mimeType"))
        if index is None or not suffix:
            continue
        view = views[index]
        if view.get("buffer", 0) != 0 or view.get("extensions"):
            continue
        stored = store_image(_payload(binary, view), suffix)
        if isinstance(stored, tuple):
            image["uri"], low = stored
            if low:
                image.setdefault("extras", {})["forgeLowTexture"] = low
        else:
            image["uri"] = stored
        del image["bufferView"]
        moved.add(index)
    if not moved:
        return None

    references = list(_view_references(document))
    kept = moved - {ref["bufferView"] for ref in references}
    indices = {}
    rebased = []
    output = bytearray()
    locations = {}

    def copy_range(region):
        if region.get("buffer", 0) != 0:
            return
        source = (region.get("byteOffset", 0), region["byteLength"])
        if source not in locations:
            output.extend(bytes((-len(output)) % 4))
            locations[source] = len(output)
            output.extend(_payload(binary, region))
        region["byteOffset"] = locations[source]

    for index, view in enumerate(views):
        if index in kept:
            continue
        indices[index] = len(rebased)
        copy_range(view)
        compressed = view.get("extensions", {}).get("EXT_meshopt_compression")
        if compressed:
            copy_range(compressed)
        rebased.append(view)
    for reference in references:
        reference["bufferView"] = indices[reference["bufferView"]]
    document["bufferViews"] = rebased
    document["buffers"][0]["byteLength"] = len(output)
    return join_glb(document, bytes(output))


def prepare_shared_model(source: Path, seen_images: dict | None = None) -> Path | None:
    target = shared_model_path(source.stem)
    if target.is_file():
        note_access(target)
        return target

    seen = {} if seen_images is None else seen_images

    def store_image(data, suffix):
        digest = sha256(data).hexdigest()
        name = f"{digest}.{suffix}"
        if name in seen:
            return seen[name]
        path = shared_texture_path(name)
        if not path.is_file():
            _atomic_write(path, data)
        else:
            note_access(path)
        low = f"{digest}-mip256.{suffix}"
        low_path = shared_texture_path(low)
        if not low_path.is_file():
            reduced = reduced_texture(bytes(data), suffix)
            if reduced:
                _atomic_write(low_path, reduced)
            else:
                low = None
        else:
            note_access(low_path)
        result = (f"/api/model-textures/{name}", f"/api/model-textures/{low}" if low else None)
        seen[name] = result
        return result

    compact = externalize_images(source.read_bytes(), store_image)
    if compact is None:
        return None
    _atomic_write(target, compact)
    return target
