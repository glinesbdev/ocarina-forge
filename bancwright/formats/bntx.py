"""Read a Switch BNTX texture container and decode the base mip to RGBA.

The header layout is the public BNTX description (magic, texture table, BRTI).
Block formats are decoded with texture2ddecoder (MIT) when that package is
installed. Channel selectors and the tile mode come from the BRTI header.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from bancwright.formats.swizzle import block_log2, deswizzle, div_up

try:
    import texture2ddecoder
except ImportError:  # pragma: no cover - the requirements file installs it
    texture2ddecoder = None


class BntxError(ValueError):
    pass


# format id -> (codec, block width, block height, bytes per block)
# Ids are the NVN surface formats stored in BRTI. 0x0B01 is RGBA8.
_FORMATS: dict[int, tuple[str, int, int, int]] = {
    0x0B01: ("rgba8", 1, 1, 4),
    0x0B02: ("rgba8", 1, 1, 4),
    0x0B06: ("rgba8", 1, 1, 4),
    0x0B05: ("rgba8", 1, 1, 4),
    0x1A01: ("bc1", 4, 4, 8),
    0x1A06: ("bc1", 4, 4, 8),
    0x1B01: ("bc2", 4, 4, 16),
    0x1B06: ("bc2", 4, 4, 16),
    0x1C01: ("bc3", 4, 4, 16),
    0x1C06: ("bc3", 4, 4, 16),
    0x1D01: ("bc4", 4, 4, 8),
    0x1E01: ("bc5", 4, 4, 16),
    0x1F05: ("bc6", 4, 4, 16),
    0x1F0A: ("bc6", 4, 4, 16),
    0x2001: ("bc7", 4, 4, 16),
    0x2006: ("bc7", 4, 4, 16),
    0x2D01: ("astc4", 4, 4, 16),
    0x2D06: ("astc4", 4, 4, 16),
    0x2E01: ("astc5x4", 5, 4, 16),
    0x2E06: ("astc5x4", 5, 4, 16),
    0x2F01: ("astc5", 5, 5, 16),
    0x2F06: ("astc5", 5, 5, 16),
    0x3001: ("astc6x5", 6, 5, 16),
    0x3006: ("astc6x5", 6, 5, 16),
    0x3101: ("astc6", 6, 6, 16),
    0x3106: ("astc6", 6, 6, 16),
    0x3201: ("astc8x5", 8, 5, 16),
    0x3206: ("astc8x5", 8, 5, 16),
    0x3301: ("astc8x6", 8, 6, 16),
    0x3306: ("astc8x6", 8, 6, 16),
    0x3401: ("astc8", 8, 8, 16),
    0x3406: ("astc8", 8, 8, 16),
    0x3501: ("astc10x5", 10, 5, 16),
    0x3506: ("astc10x5", 10, 5, 16),
    0x3601: ("astc10x6", 10, 6, 16),
    0x3606: ("astc10x6", 10, 6, 16),
    0x3701: ("astc10x8", 10, 8, 16),
    0x3706: ("astc10x8", 10, 8, 16),
    0x3801: ("astc10", 10, 10, 16),
    0x3806: ("astc10", 10, 10, 16),
    0x3901: ("astc12x10", 12, 10, 16),
    0x3906: ("astc12x10", 12, 10, 16),
    0x3A01: ("astc12", 12, 12, 16),
    0x3A06: ("astc12", 12, 12, 16),
}


@dataclass
class Texture:
    name: str
    width: int
    height: int
    rgba: bytes
    fmt: int
    note: str = ""
    codec: str = ""
    tile: int = 0
    swizzle: str = ""
    block_height: int = 0


def _u16(data: bytes, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def _i32(data: bytes, at: int) -> int:
    return struct.unpack_from("<i", data, at)[0]


def _u64(data: bytes, at: int) -> int:
    return struct.unpack_from("<Q", data, at)[0]


def _string(data: bytes, at: int) -> str:
    if at <= 0 or at + 2 > len(data):
        return ""
    end = data.find(b"\0", at + 2)
    if end < 0:
        return ""
    return data[at + 2 : end].decode("utf-8", "replace")


def textures(data: bytes) -> list[Texture]:
    if len(data) < 0x40 or data[:4] != b"BNTX":
        raise BntxError("not a BNTX texture container")
    if data[0x0C:0x0E] != b"\xff\xfe":
        raise BntxError("only little-endian BNTX is supported")
    count = _i32(data, 0x24)
    table = _u64(data, 0x28)
    if count <= 0 or count > 4096 or table <= 0:
        return []
    found: list[Texture] = []
    for index in range(count):
        at = table + index * 8
        if at + 8 > len(data):
            break
        brti = _u64(data, at)
        if not brti:
            continue
        try:
            found.append(_texture(data, brti))
        except BntxError as exc:
            name, fmt = _identity(data, brti)
            found.append(Texture(name=name, width=0, height=0, rgba=b"", fmt=fmt, note=str(exc)))
    return found


def image_names(data: bytes) -> list[str]:
    """Texture names in a BNTX, without decoding the images."""
    if len(data) < 0x40 or data[:4] != b"BNTX":
        return []
    count = _i32(data, 0x24)
    table = _u64(data, 0x28)
    if count <= 0 or count > 4096 or table <= 0:
        return []
    names = []
    for index in range(count):
        at = table + index * 8
        if at + 8 > len(data):
            break
        brti = _u64(data, at)
        name, _fmt = _identity(data, brti) if brti else ("", 0)
        names.append(name)
    return names


def _identity(data: bytes, at: int) -> tuple[str, int]:
    try:
        if at <= 0 or at + 0x68 > len(data) or data[at : at + 4] != b"BRTI":
            return "", 0
        return _string(data, _u64(data, at + 0x60)), _u32(data, at + 0x1C)
    except (struct.error, IndexError):
        return "", 0


def _texture(data: bytes, at: int) -> Texture:
    if at + 0x80 > len(data) or data[at : at + 4] != b"BRTI":
        raise BntxError(f"texture header at {at:#x} is not BRTI")
    tile = _u16(data, at + 0x12)
    fmt = _u32(data, at + 0x1C)
    width = _i32(data, at + 0x24)
    height = _i32(data, at + 0x28)
    name = _string(data, _u64(data, at + 0x60))
    layout = data[at + 0x34 : at + 0x3C]
    channels = data[at + 0x58 : at + 0x5C]
    mip_table = _u64(data, at + 0x70)
    image = _u64(data, mip_table) if mip_table and mip_table + 8 <= len(data) else 0
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise BntxError(f"{name or 'texture'}: size {width}x{height} is not usable")
    info = _FORMATS.get(fmt)
    if info is None:
        raise BntxError(f"{name or 'texture'}: texture format 0x{fmt:04X} is not decoded")
    codec, block_w, block_h, bytes_per = info
    mip_bytes = _mip0_bytes(data, at, image)
    log2 = block_log2(
        height,
        block_h,
        layout,
        width=width,
        block_width=block_w,
        bytes_per=bytes_per,
        mip_bytes=mip_bytes,
    )
    if image <= 0 or image >= len(data):
        raise BntxError(f"{name or 'texture'}: image data is missing")
    raw = data[image:]
    linear = deswizzle(width, height, block_w, block_h, bytes_per, tile, log2, raw)
    rgba, note = _decode(codec, width, height, linear, fmt)
    rgba = _channels(rgba, width, height, channels)
    block_height = 1 << max(0, log2)
    swizzle = "linear" if tile == 1 else f"deswizzled tile {tile}"
    return Texture(
        name=name,
        width=width,
        height=height,
        rgba=rgba,
        fmt=fmt,
        note=note,
        codec=codec,
        tile=tile,
        swizzle=swizzle,
        block_height=block_height,
    )


def _mip0_bytes(data: bytes, at: int, image: int) -> int:
    """Size of mip 0 from the offset table, or through EOF for a one-mip file."""
    if at + 0x78 > len(data):
        return 0
    table = _u64(data, at + 0x70)
    offsets = []
    if table:
        for index in range(17):
            slot = table + index * 8
            if slot + 8 > len(data):
                break
            value = _u64(data, slot)
            if value <= 0 or value > len(data):
                break
            if offsets and value <= offsets[-1]:
                break
            offsets.append(value)
    if len(offsets) >= 2:
        return offsets[1] - offsets[0]
    start = offsets[0] if offsets else image
    if start and 0 < start < len(data):
        return len(data) - start
    return 0


def _decode(codec: str, width: int, height: int, linear: bytes, fmt: int) -> tuple[bytes, str]:
    if codec == "rgba8":
        need = width * height * 4
        return bytes(linear[:need]).ljust(need, b"\0"), ""
    if texture2ddecoder is None:
        raise BntxError(
            f"texture format 0x{fmt:04X} needs the texture2ddecoder package "
            "(pip install -r requirements.txt)"
        )
    decoder = {
        "bc1": texture2ddecoder.decode_bc1,
        "bc3": texture2ddecoder.decode_bc3,
        "bc4": getattr(texture2ddecoder, "decode_bc4", None),
        "bc5": getattr(texture2ddecoder, "decode_bc5", None),
        "bc6": getattr(texture2ddecoder, "decode_bc6", None),
        "bc7": getattr(texture2ddecoder, "decode_bc7", None),
    }.get(codec)
    astc = {
        "astc4": (4, 4),
        "astc5x4": (5, 4),
        "astc5": (5, 5),
        "astc6x5": (6, 5),
        "astc6": (6, 6),
        "astc8x5": (8, 5),
        "astc8x6": (8, 6),
        "astc8": (8, 8),
        "astc10x5": (10, 5),
        "astc10x6": (10, 6),
        "astc10x8": (10, 8),
        "astc10": (10, 10),
        "astc12x10": (12, 10),
        "astc12": (12, 12),
    }
    if codec == "bc2":
        decoder = getattr(texture2ddecoder, "decode_bc2", None) or getattr(texture2ddecoder, "decode_bc3", None)
    if codec in astc:
        bw, bh = astc[codec]
        linear = _repair_astc_partial_gob(linear, width, height, bw, bh)
        bgra = texture2ddecoder.decode_astc(linear, width, height, bw, bh)
    elif decoder is None:
        raise BntxError(f"texture format 0x{fmt:04X} has no decoder in texture2ddecoder")
    else:
        bgra = decoder(linear, width, height)
    if not bgra:
        raise BntxError(f"texture format 0x{fmt:04X} did not decode")
    rgba = _bgra_to_rgba(bytes(bgra), width, height)
    if codec == "bc1":
        rgba = _bc1_punch(linear, width, height, rgba)
    return rgba, ""


def _repair_astc_partial_gob(linear: bytes, width: int, height: int, block_w: int, block_h: int) -> bytes:
    """Copy the block above into an all-zero block on the last partial GOB row.

    A height that is not a multiple of 8 block-rows leaves the tail of that
    GOB unwritten. Those blocks decode as empty (the odd columns on the last
    rows of a house albedo). The block directly above is the same texel row
    the hardware was padding.
    """
    blocks_x = max(1, div_up(width, block_w))
    blocks_y = max(1, div_up(height, block_h))
    if blocks_y % 8 == 0:
        return linear
    bpp = 16
    need = blocks_x * blocks_y * bpp
    out = bytearray(linear[:need]).ljust(need, b"\0")
    start = (blocks_y // 8) * 8
    for y in range(max(start, 1), blocks_y):
        for x in range(blocks_x):
            at = (y * blocks_x + x) * bpp
            above = ((y - 1) * blocks_x + x) * bpp
            if out[at : at + bpp] == b"\0" * bpp and out[above : above + bpp] != b"\0" * bpp:
                out[at : at + bpp] = out[above : above + bpp]
    if len(linear) > need:
        return bytes(out) + linear[need:]
    return bytes(out)


def _bc1_punch(linear: bytes, width: int, height: int, rgba: bytes) -> bytes:
    """BC1 blocks with color0 <= color1 use index 3 as punch-through alpha.

    texture2ddecoder leaves that index opaque. The sky above a FarView card
    is this case, and an opaque alpha draws it black.
    """
    blocks_x = max(1, div_up(width, 4))
    blocks_y = max(1, div_up(height, 4))
    out = bytearray(rgba)
    for by in range(blocks_y):
        for bx in range(blocks_x):
            block_at = (by * blocks_x + bx) * 8
            if block_at + 8 > len(linear):
                continue
            color0 = linear[block_at] | (linear[block_at + 1] << 8)
            color1 = linear[block_at + 2] | (linear[block_at + 3] << 8)
            if color0 > color1:
                continue
            bits = int.from_bytes(linear[block_at + 4 : block_at + 8], "little")
            for index in range(16):
                if ((bits >> (2 * index)) & 3) != 3:
                    continue
                px = bx * 4 + (index & 3)
                py = by * 4 + (index >> 2)
                if px >= width or py >= height:
                    continue
                dest = (py * width + px) * 4 + 3
                if dest < len(out):
                    out[dest] = 0
    return bytes(out)


def _bgra_to_rgba(bgra: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    for index in range(width * height):
        at = index * 4
        if at + 3 >= len(bgra):
            break
        out[at] = bgra[at + 2]
        out[at + 1] = bgra[at + 1]
        out[at + 2] = bgra[at]
        out[at + 3] = bgra[at + 3]
    return bytes(out)


def _channels(rgba: bytes, width: int, height: int, channels: bytes) -> bytes:
    if len(channels) < 4 or channels == bytes((2, 3, 4, 5)):
        return rgba
    out = bytearray(len(rgba))
    for index in range(width * height):
        at = index * 4
        pixel = rgba[at : at + 4] if at + 4 <= len(rgba) else b"\0\0\0\xff"
        for dest in range(4):
            source = channels[dest]
            if source == 0:
                out[at + dest] = 0
            elif source == 1:
                out[at + dest] = 255
            elif 2 <= source <= 5:
                out[at + dest] = pixel[source - 2]
            else:
                out[at + dest] = pixel[dest]
    return bytes(out)


def write_rgba(
    name: str,
    width: int,
    height: int,
    rgba: bytes,
    *,
    swizzled: bool = False,
    log2: int | None = None,
    layout0: int | None = None,
    layout4: int | None = None,
) -> bytes:
    """A one-texture BNTX. Tests and the sample dump use this. It is not a game file."""
    if len(rgba) < width * height * 4:
        raise BntxError("rgba buffer is shorter than the image")
    from bancwright.formats.swizzle import default_block_log2, swizzle

    chosen = default_block_log2(height, 1) if log2 is None else log2
    tile = 0 if swizzled else 1
    image = swizzle(width, height, 1, 1, 4, tile, chosen, rgba) if swizzled else rgba[: width * height * 4]
    blob = bytearray(0x100)
    blob[:8] = b"BNTX\0\0\0\0"
    struct.pack_into("<I", blob, 0x08, 0x00040000)
    blob[0x0C:0x0E] = b"\xff\xfe"
    blob[0x0E] = 8
    blob[0x0F] = 64
    blob[0x20:0x24] = b"NX  "
    struct.pack_into("<i", blob, 0x24, 1)

    def add_string(text: str) -> int:
        if len(blob) % 2:
            blob.append(0)
        at = len(blob)
        raw = text.encode()
        blob.extend(struct.pack("<H", len(raw)) + raw + b"\0")
        if len(blob) % 2:
            blob.append(0)
        return at

    name_at = add_string(name)
    file_name = add_string("textures.bntx")
    struct.pack_into("<I", blob, 0x10, file_name + 2)

    def align(n: int) -> None:
        while len(blob) % n:
            blob.append(0)

    align(8)
    table_at = len(blob)
    blob.extend(b"\0" * 8)
    dict_at = len(blob)
    blob.extend(struct.pack("<II", 0, 1))
    blob.extend(struct.pack("<IHHQ", 0xFFFFFFFF, 1, 1, 0))
    blob.extend(struct.pack("<IHHQ", 0, 0, 0, name_at))
    brti_at = len(blob)
    blob.extend(b"\0" * 0xA0)
    blob[brti_at : brti_at + 4] = b"BRTI"
    struct.pack_into("<H", blob, brti_at + 0x12, tile)
    struct.pack_into("<H", blob, brti_at + 0x16, 1)
    struct.pack_into("<I", blob, brti_at + 0x1C, 0x0B01)
    struct.pack_into("<i", blob, brti_at + 0x24, width)
    struct.pack_into("<i", blob, brti_at + 0x28, height)
    struct.pack_into("<i", blob, brti_at + 0x2C, 1)
    struct.pack_into("<I", blob, brti_at + 0x30, 1)
    blob[brti_at + 0x34 + 4] = (chosen if layout4 is None else layout4) & 0xFF
    if layout0 is not None:
        blob[brti_at + 0x34] = layout0 & 0xFF
    blob[brti_at + 0x58 : brti_at + 0x5C] = bytes((2, 3, 4, 5))
    struct.pack_into("<Q", blob, brti_at + 0x60, name_at)
    align(8)
    mip_at = len(blob)
    blob.extend(b"\0" * 8)
    struct.pack_into("<Q", blob, brti_at + 0x70, mip_at)
    align(8)
    image_at = len(blob)
    blob.extend(image)
    struct.pack_into("<Q", blob, mip_at, image_at)
    struct.pack_into("<Q", blob, table_at, brti_at)
    struct.pack_into("<Q", blob, 0x28, table_at)
    struct.pack_into("<Q", blob, 0x38, dict_at)
    struct.pack_into("<I", blob, 0x1C, len(blob))
    return bytes(blob)
