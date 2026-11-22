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


def ktx2(vk_format: int, width: int, height: int, levels: bytes | list[bytes]) -> bytes:
    """Every mip, no supercompression. Level 0 is the full image.

    three's glTF loader assigns LinearMipmapLinear when the sampler omits
    minFilter. A compressed texture cannot build those mips on the GPU, so a
    one-level file samples black. The chain is stored here instead.
    """
    if isinstance(levels, (bytes, bytearray)):
        levels = [bytes(levels)]
    if not levels:
        raise ValueError("KTX2 needs at least one mip")
    dfd = _dfd()
    # Header ends at 80. Each level index is 24 bytes, then the level bytes, then the DFD.
    data_at = 80 + 24 * len(levels)
    packed = bytearray()
    index = bytearray()
    cursor = data_at
    for level in levels:
        index += struct.pack("<QQQ", cursor, len(level), len(level))
        packed += level
        cursor += len(level)
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
        len(levels),
        0,
    )
    blob = bytearray(header)
    blob += struct.pack("<IIII", cursor, len(dfd), 0, 0)
    blob += struct.pack("<QQ", 0, 0)
    blob += index
    blob += packed
    if len(blob) != cursor:
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
    """OCFG bytes, or None when a compressed upload would be the wrong size.

    WebGL rejects a BC level 0 whose width or height is not a multiple of 4.
    Those images stay JPEG or PNG. Everything else gets a full mip chain.
    """
    chain = _mip_chain(image)
    if chain is None:
        return None
    vk, width, height, levels = chain
    fallback, _mime = encode_albedo(image.width, image.height, image.rgba, alpha if alpha != "png" else "mask")
    return pack_texture(ktx2(vk, width, height, levels), fallback)


def _mip_chain(image) -> tuple[int, int, int, list[bytes]] | None:
    rgba = getattr(image, "rgba", None) or b""
    if not rgba:
        return None
    width, height, pixels = _limit(image.width, image.height, rgba)
    if width <= 0 or height <= 0 or width % 4 or height % 4:
        return None
    native_vk, native_blocks = _native_base(image, width, height)
    vk = native_vk or VK_BC7
    encoder = encode_bc1 if vk == VK_BC1 else encode_bc7
    if encoder is encode_bc7:
        rust_levels = _rust_bc7_chain(width, height, pixels)
        if rust_levels is not None and _chain_ok(width, height, rust_levels):
            if native_blocks is not None:
                rust_levels = [native_blocks, *rust_levels[1:]]
            return vk, width, height, rust_levels
    levels: list[bytes] = []
    w, h, mip = width, height, pixels
    first = True
    while True:
        if first and native_blocks is not None:
            levels.append(native_blocks)
        else:
            block = encoder(w, h, mip)
            if not block:
                return None
            levels.append(block)
        if w == 1 and h == 1:
            break
        w, h, mip = _downsample(w, h, mip)
        first = False
    return vk, width, height, levels


def _native_base(image, width: int, height: int) -> tuple[int | None, bytes | None]:
    """Mip 0 from the dump when it is already BC1 or BC7 at this size.

    Lower mips are re-encoded in that same format. BC3 and ASTC become BC7 for
    the whole chain, because a desktop GPU usually has BPTC and the chain has
    to be one vkFormat.
    """
    if getattr(image, "width", 0) != width or getattr(image, "height", 0) != height:
        return None, None
    blocks = getattr(image, "blocks", b"") or b""
    codec = (getattr(image, "codec", "") or "").lower()
    if codec == "bc1":
        vk, size = VK_BC1, 8
    elif codec == "bc7":
        vk, size = VK_BC7, 16
    else:
        return None, None
    expect = _block_count(width, height) * size
    if len(blocks) < expect:
        return None, None
    return vk, blocks[:expect]


def _downsample(width: int, height: int, rgba: bytes) -> tuple[int, int, bytes]:
    """Box-filter one step. A 1-pixel side stays 1."""
    nw = max(1, width // 2)
    nh = max(1, height // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            acc = [0, 0, 0, 0]
            count = 0
            for oy in (0, 1):
                sy = min(height - 1, y * 2 + oy)
                for ox in (0, 1):
                    sx = min(width - 1, x * 2 + ox)
                    pixel = (sy * width + sx) * 4
                    for channel in range(4):
                        acc[channel] += rgba[pixel + channel]
                    count += 1
            dest = (y * nw + x) * 4
            for channel in range(4):
                out[dest + channel] = acc[channel] // max(1, count)
    return nw, nh, bytes(out)


def _limit(width: int, height: int, rgba: bytes):
    from ocarina_forge.formats.gltf import limit_rgba

    return limit_rgba(width, height, rgba, 1024)


def _block_count(width: int, height: int) -> int:
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4)


def encode_bc1(width: int, height: int, rgba: bytes) -> bytes:
    """BC1, four opaque colours. Used for mip 0 passthrough's lower levels."""
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return b""
    out = bytearray()
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            pixels = _gather(rgba, width, height, x, y)
            out += _bc1_block(pixels)
    return bytes(out)


def _gather(rgba: bytes, width: int, height: int, x: int, y: int) -> list[bytes]:
    pixels = []
    for row in range(4):
        sy = min(height - 1, y + row)
        for col in range(4):
            sx = min(width - 1, x + col)
            at = (sy * width + sx) * 4
            pixels.append(rgba[at : at + 4])
    return pixels


def _to565(pixel: bytes) -> int:
    return ((pixel[0] >> 3) << 11) | ((pixel[1] >> 2) << 5) | (pixel[2] >> 3)


def _from565(value: int) -> tuple[int, int, int]:
    red = (value >> 11) & 31
    green = (value >> 5) & 63
    blue = value & 31
    return (red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2)


def _bc1_block(pixels: list[bytes]) -> bytes:
    def luma(pixel: bytes) -> int:
        return pixel[0] * 2 + pixel[1] * 4 + pixel[2]

    dark = min(pixels, key=luma)
    bright = max(pixels, key=luma)
    color0 = _to565(bright)
    color1 = _to565(dark)
    if color0 <= color1:
        # Equal endpoints select the 3-colour mode, which punches a transparent pixel.
        if color0 < 0xFFFF:
            color0 += 1
        else:
            color1 -= 1
    ends = (_from565(color0), _from565(color1))
    palette = (
        ends[0],
        ends[1],
        tuple((2 * ends[0][channel] + ends[1][channel]) // 3 for channel in range(3)),
        tuple((ends[0][channel] + 2 * ends[1][channel]) // 3 for channel in range(3)),
    )
    indices = 0
    for pixel_index, pixel in enumerate(pixels):
        best = 0
        best_error = 1 << 30
        for choice, colour in enumerate(palette):
            error = sum((pixel[channel] - colour[channel]) ** 2 for channel in range(3))
            if error < best_error:
                best_error = error
                best = choice
        indices |= best << (pixel_index * 2)
    return struct.pack("<HHI", color0, color1, indices)


def encode_bc7(width: int, height: int, rgba: bytes) -> bytes:
    """Mode 6, one subset, 4-bit indices. Good enough that a brick stays a brick.

    `ocarina_forge_native` does this same mode when it is built. The Python
    loop below is the fallback, and the reference the Rust blocks match.
    """
    native = _rust_encode_bc7(width, height, rgba)
    if native is not None:
        return native
    return _encode_bc7_py(width, height, rgba)


def _native_mod():
    """The extension, or None when maturin has not built it."""
    from ocarina_forge.extension import module

    return module()


def _rust_encode_bc7(width: int, height: int, rgba: bytes) -> bytes | None:
    native = _native_mod()
    if native is None or width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return None
    try:
        out = native.encode_bc7(int(width), int(height), rgba)
    except Exception:
        return None
    if out is None or len(out) != _block_count(width, height) * 16:
        return None
    return bytes(out)


def _rust_bc7_chain(width: int, height: int, rgba: bytes) -> list[bytes] | None:
    native = _native_mod()
    if native is None:
        return None
    try:
        levels = native.bc7_mip_chain(int(width), int(height), rgba)
    except Exception:
        return None
    if not levels:
        return None
    return [bytes(level) for level in levels]


def _chain_ok(width: int, height: int, levels: list[bytes]) -> bool:
    w, h = width, height
    for index, level in enumerate(levels):
        if len(level) != _block_count(w, h) * 16:
            return False
        if w == 1 and h == 1:
            return index == len(levels) - 1
        w, h = max(1, w // 2), max(1, h // 2)
    return False


def _encode_bc7_py(width: int, height: int, rgba: bytes) -> bytes:
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return b""
    out = bytearray()
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            out += _mode6(_gather(rgba, width, height, x, y))
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
