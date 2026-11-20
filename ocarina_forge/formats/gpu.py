"""GPU-ready images: native blocks when we still have them, otherwise BC7.

three r170's KTX2 loader only raw-uploads a few vkFormats (uncompressed, ASTC
6x6, and one float ASTC). A Basis file is vkFormat 0, so it goes through the
transcoder, and that transcoder returned an empty image for the earlier
encode. These files keep a real vkFormat and the block bytes. The browser
uploads them with CompressedTexture when the matching WebGL extension exists.
A JPEG or PNG sits after the KTX2 so a GPU without that extension can decode
off the main thread instead.
"""

from __future__ import annotations

import struct

from ocarina_forge.formats.gltf import encode_albedo

KTX2_MAGIC = bytes((0xAB, 0x4B, 0x54, 0x58, 0x20, 0x32, 0x30, 0xBB, 0x0D, 0x0A, 0x1A, 0x0A))
# Vulkan block formats. three's own loader does not list the BC ones.
VK_BC1 = 133  # BC1_RGBA_UNORM_BLOCK
VK_BC3 = 137  # BC3_UNORM_BLOCK
VK_BC7 = 145  # BC7_UNORM_BLOCK
VK_ASTC = {
    (4, 4): 157,
    (5, 4): 159,
    (5, 5): 161,
    (6, 5): 163,
    (6, 6): 165,
    (8, 5): 167,
    (8, 6): 169,
    (8, 8): 171,
    (10, 5): 173,
    (10, 6): 175,
    (10, 8): 177,
    (10, 10): 179,
    (12, 10): 181,
    (12, 12): 183,
}
_BC7_WEIGHTS = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)


def pack_texture(ktx: bytes, fallback: bytes) -> bytes:
    """KTX2 first, then the JPEG or PNG the browser uses when the GPU cannot."""
    return b"OCFG" + struct.pack("<I", len(ktx)) + ktx + fallback


def split_texture(blob: bytes) -> tuple[bytes, bytes] | None:
    if not blob.startswith(b"OCFG") or len(blob) < 8:
        return None
    size = struct.unpack_from("<I", blob, 4)[0]
    if size < 48 or 8 + size > len(blob):
        return None
    return blob[8 : 8 + size], blob[8 + size :]


def ktx2(vk_format: int, width: int, height: int, blocks: bytes) -> bytes:
    """One mip, no supercompression. The DFD says sRGB BT.709 so three can label it."""
    header = struct.pack(
        "<12sIIIIIIIII",
        KTX2_MAGIC,
        vk_format,
        1,
        width,
        height,
        0,
        0,
        1,
        1,
        0,
    )
    dfd = _dfd()
    # 12 byte magic + 9 uint32 = 48. Then dfd/kvd as uint32 and sgd as uint64 = 32.
    # The header ends at 80. One level index is 24 bytes, so mip 0 starts at 104.
    data_at = 104
    dfd_at = data_at + len(blocks)
    blob = bytearray(header)
    blob += struct.pack("<IIII", dfd_at, len(dfd), 0, 0)
    blob += struct.pack("<QQ", 0, 0)
    blob += struct.pack("<QQQ", data_at, len(blocks), len(blocks))
    blob += blocks
    if len(blob) != dfd_at:
        raise RuntimeError("KTX2 layout does not match the header")
    blob += dfd
    return bytes(blob)


def _dfd() -> bytes:
    """A short sRGB descriptor. Compressed vkFormats do not need sample rows to upload."""
    # totalSize, vendorId, descriptorType, versionNumber, descriptorBlockSize,
    # colorModel, colorPrimaries, transferFunction, flags, texelBlockDimension0-3,
    # bytesPlane0-7
    body = struct.pack(
        "<IHHIIBBBBBBBB8s",
        8 + 24,
        0,
        0,
        2,
        24,
        1,  # KHR_DF_MODEL_RGBSDA
        1,  # BT.709
        2,  # sRGB
        0,
        3,
        3,
        0,
        0,
        b"\0\0\0\0\0\0\0\0",
    )
    return struct.pack("<I", len(body) + 4) + body


def gpu_image(image, alpha: str) -> bytes | None:
    """OCFG bytes, or None when there is nothing better than a plain JPEG or PNG."""
    native = _native_ktx(image)
    if native is None and getattr(image, "rgba", None):
        native = _bc7_ktx(image)
    if native is None:
        return None
    fallback, _mime = encode_albedo(image.width, image.height, image.rgba, alpha if alpha != "png" else "mask")
    return pack_texture(native, fallback)


def _native_ktx(image) -> bytes | None:
    blocks = getattr(image, "blocks", b"") or b""
    codec = (getattr(image, "codec", "") or "").lower()
    width = getattr(image, "width", 0) or 0
    height = getattr(image, "height", 0) or 0
    if not blocks or width <= 0 or height <= 0:
        return None
    if codec == "bc1":
        vk = VK_BC1
        size = 8
    elif codec == "bc3":
        vk = VK_BC3
        size = 16
    elif codec == "bc7":
        vk = VK_BC7
        size = 16
    elif codec.startswith("astc"):
        # Kept for a GPU that has ASTC. The browser falls back to the BC7
        # re-encode below when this file is not what we ship. See gpu_image.
        return None
    else:
        return None
    expect = _block_count(width, height) * size
    if len(blocks) < expect:
        return None
    return ktx2(vk, width, height, blocks[:expect])


def _bc7_ktx(image) -> bytes | None:
    width, height, rgba = _limit(image.width, image.height, image.rgba)
    blocks = encode_bc7(width, height, rgba)
    if not blocks:
        return None
    return ktx2(VK_BC7, width, height, blocks)


def _limit(width: int, height: int, rgba: bytes):
    from ocarina_forge.formats.gltf import limit_rgba

    return limit_rgba(width, height, rgba, 1024)


def _block_count(width: int, height: int) -> int:
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4)


def encode_bc7(width: int, height: int, rgba: bytes) -> bytes:
    """Mode 6, one subset, 4-bit indices. Good enough that a brick stays a brick."""
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return b""
    out = bytearray()
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            pixels = []
            for row in range(4):
                sy = min(height - 1, y + row)
                for col in range(4):
                    sx = min(width - 1, x + col)
                    at = (sy * width + sx) * 4
                    pixels.append(rgba[at : at + 4])
            out += _mode6(pixels)
    return bytes(out)


def _mode6(pixels: list[bytes]) -> bytes:
    low = [min(pixel[channel] for pixel in pixels) for channel in range(4)]
    high = [max(pixel[channel] for pixel in pixels) for channel in range(4)]
    # One shared p-bit per endpoint. Snap every channel to that LSB.
    low, low_p = _snap(low)
    high, high_p = _snap(high)
    if low == high:
        indices = [0] * 16
    else:
        indices = [_index(pixel, low, high) for pixel in pixels]
        if indices[0] >= 8:
            low, high = high, low
            low_p, high_p = high_p, low_p
            indices = [15 - index for index in indices]
    bits: list[int] = []

    def put(value: int, count: int) -> None:
        for shift in range(count):
            bits.append((value >> shift) & 1)

    put(0b1000000, 7)
    # Per channel, low endpoint then high. Mode 6 is seven bits plus a shared p-bit.
    for channel in range(4):
        put(low[channel] >> 1, 7)
        put(high[channel] >> 1, 7)
    put(low_p, 1)
    put(high_p, 1)
    put(indices[0], 3)
    for index in indices[1:]:
        put(index, 4)
    if len(bits) != 128:
        raise RuntimeError(f"BC7 mode 6 wrote {len(bits)} bits")
    raw = bytearray(16)
    for bit, value in enumerate(bits):
        if value:
            raw[bit >> 3] |= 1 << (bit & 7)
    return bytes(raw)


def _snap(color: list[int]) -> tuple[list[int], int]:
    bit = color[0] & 1
    snapped = []
    for channel in color:
        value = channel if (channel & 1) == bit else (channel - 1 if channel and bit == 0 else channel + 1 if channel < 255 else channel - 1)
        snapped.append(max(0, min(255, value)))
    return snapped, bit


def _index(pixel: bytes, low: list[int], high: list[int]) -> int:
    best_index = 0
    best_error = 1 << 30
    for index, weight in enumerate(_BC7_WEIGHTS):
        error = 0
        for channel in range(4):
            mixed = ((64 - weight) * low[channel] + weight * high[channel] + 32) >> 6
            delta = pixel[channel] - mixed
            error += delta * delta
        if error < best_error:
            best_error = error
            best_index = index
    return best_index
