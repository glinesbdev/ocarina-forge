"""Read a Switch BNTX texture container and decode the base mip to RGBA.

The header layout is the public BNTX description (magic, texture table, BRTI).
Block formats are decoded with texture2ddecoder (MIT) when that package is
installed. Channel selectors and the tile mode come from the BRTI header.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ocarina_forge.formats.swizzle import (
    _stored_log2,
    block_log2,
    default_block_log2,
    deswizzle,
    div_up,
    fill_partial_gob,
    gob_order,
    inferred_block_log2,
    layout_words,
    surface_bytes,
)

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


def _format_info(fmt: int) -> tuple[tuple[str, int, int, int] | None, str]:
    """Known NVN id, or the same block codec for an unlisted channel type.

    A new type nibble on a known family (unorm versus sRGB, and the other
    small variants) is still that codec. An unknown family is refused.
    """
    info = _FORMATS.get(fmt)
    if info is not None:
        return info, ""
    family = fmt & 0xFF00
    for known, value in _FORMATS.items():
        if (known & 0xFF00) == family:
            return value, f"format 0x{fmt:04X} decoded as {value[0]} (same block family)"
    return None, ""


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


def texture_layer(data: bytes, index: int, *, flatten_alpha: bool = False) -> Texture:
    """Decode one array layer of the first BRTI.

    The array count is the u32 at BRTI+0x30. The image byte size is the u32
    at BRTI+0x50, and layer ``index`` starts at
    ``image_offset + index * (image_size / count)``. Only mip 0 of that layer
    is decoded. A count of 1 is the whole image, so layer 0 matches
    ``textures``. ``flatten_alpha`` forces every alpha byte to 255.
    """
    if len(data) < 0x40 or data[:4] != b"BNTX":
        raise BntxError("not a BNTX texture container")
    if data[0x0C:0x0E] != b"\xff\xfe":
        raise BntxError("only little-endian BNTX is supported")
    count = _i32(data, 0x24)
    table = _u64(data, 0x28)
    if count <= 0 or table <= 0 or table + 8 > len(data):
        raise BntxError("BNTX has no textures")
    brti = _u64(data, table)
    if not brti:
        raise BntxError("BNTX has no textures")
    return _texture(data, brti, layer=index, flatten_alpha=flatten_alpha)


def texture_headers(data: bytes) -> list[dict]:
    """BRTI fields this decoder reads. Texels are not decoded and the offsets stay put.

    ``depth`` is the i32 at BRTI+0x2C. ``alignmentShift`` is the byte at BNTX+0x0E.
    ``blockHeightLog2`` is the layout-byte reading, before the decoder picks a height.
    """
    if len(data) < 0x40 or data[:4] != b"BNTX":
        return []
    alignment = data[0x0E] if len(data) > 0x0E else 0
    count = _i32(data, 0x24)
    table = _u64(data, 0x28)
    if count <= 0 or count > 4096 or table <= 0:
        return []
    found = []
    for index in range(count):
        at = table + index * 8
        if at + 8 > len(data):
            break
        brti = _u64(data, at)
        if not brti or brti + 0x80 > len(data) or data[brti : brti + 4] != b"BRTI":
            continue
        fmt = _u32(data, brti + 0x1C)
        info, family_note = _format_info(fmt)
        layout = data[brti + 0x34 : brti + 0x3C]
        tile = _u16(data, brti + 0x12)
        texture_layout, size_range = layout_words(layout)
        stored = _stored_log2(layout)
        image = 0
        mip_table = _u64(data, brti + 0x70)
        if mip_table and mip_table + 8 <= len(data):
            image = _u64(data, mip_table)
        width = _i32(data, brti + 0x24)
        height = _i32(data, brti + 0x28)
        mip0 = _mip0_bytes(data, brti, image)
        surface = {}
        block_w = block_h = bytes_per = 0
        if info is not None:
            _codec, block_w, block_h, bytes_per = info
            for log2 in range(6):
                surface[log2] = surface_bytes(width, height, block_w, block_h, bytes_per, log2)
        found.append(
            {
                "name": _string(data, _u64(data, brti + 0x60)),
                "format": fmt,
                "formatName": info[0] if info else "",
                "formatNote": family_note,
                "blockWidth": block_w or None,
                "blockHeightPixels": block_h or None,
                "width": width,
                "height": height,
                "depth": _i32(data, brti + 0x2C) if brti + 0x30 <= len(data) else 0,
                "mips": _mip_count(data, brti),
                "tileMode": tile,
                "flags": data[brti + 0x10] if brti + 0x11 <= len(data) else 0,
                "dim": data[brti + 0x11] if brti + 0x12 <= len(data) else 0,
                "swizzleValue": _u16(data, brti + 0x14) if brti + 0x16 <= len(data) else 0,
                "bytesPer": bytes_per or None,
                "gobOrder": "linear" if tile == 1 else (gob_order(bytes_per) if bytes_per else ""),
                "layout": layout.hex(),
                "textureLayout": texture_layout,
                "sizeRange": size_range,
                "blockHeightLog2": stored,
                "inferredBlockHeightLog2": (
                    inferred_block_log2(width, height, block_w, block_h, bytes_per, mip0)
                    if info is not None
                    else None
                ),
                "blockHeight": (1 << stored) if stored is not None else None,
                "arrayCount": _u32(data, brti + 0x30) if brti + 0x34 <= len(data) else 0,
                "imageSize": _u32(data, brti + 0x50) if brti + 0x54 <= len(data) else 0,
                "mip0Size": mip0,
                "surfaceBytes": surface.get(stored) if stored is not None else None,
                "defaultBlockHeightLog2": default_block_log2(height, info[2]) if info else None,
                "surfaces": surface,
                "alignmentShift": alignment,
            }
        )
    return found


def _mip_count(data: bytes, at: int) -> int:
    if at + 0x78 > len(data):
        return 0
    table = _u64(data, at + 0x70)
    if not table:
        return 0
    count = 0
    previous = 0
    for index in range(17):
        slot = table + index * 8
        if slot + 8 > len(data):
            break
        value = _u64(data, slot)
        if value <= 0 or value > len(data) or (previous and value <= previous):
            break
        previous = value
        count += 1
    return count


def image_sizes(data: bytes) -> list[dict]:
    """Name and pixel size of each image, without decoding the texels."""
    if len(data) < 0x40 or data[:4] != b"BNTX":
        return []
    count = _i32(data, 0x24)
    table = _u64(data, 0x28)
    if count <= 0 or count > 4096 or table <= 0:
        return []
    found = []
    for index in range(count):
        at = table + index * 8
        if at + 8 > len(data):
            break
        brti = _u64(data, at)
        name, fmt = _identity(data, brti) if brti else ("", 0)
        width = height = 0
        if brti and brti + 0x2C <= len(data) and data[brti : brti + 4] == b"BRTI":
            width = _i32(data, brti + 0x24)
            height = _i32(data, brti + 0x28)
        found.append({"name": name, "width": width, "height": height, "format": fmt})
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


def _texture(data: bytes, at: int, *, layer: int | None = None, flatten_alpha: bool = False) -> Texture:
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
    info, family_note = _format_info(fmt)
    if info is None:
        raise BntxError(f"{name or 'texture'}: texture format 0x{fmt:04X} is not decoded")
    codec, block_w, block_h, bytes_per = info
    array_count = _u32(data, at + 0x30) if at + 0x34 <= len(data) else 1
    if array_count <= 0 or array_count > 4096:
        array_count = 1
    layer_index = 0 if layer is None else layer
    if layer_index < 0 or layer_index >= array_count:
        raise BntxError(f"{name or 'texture'}: array layer {layer_index} is outside 0..{array_count - 1}")
    mip_bytes = _mip0_bytes(data, at, image)
    if image <= 0 or image >= len(data):
        raise BntxError(f"{name or 'texture'}: image data is missing")
    layer_note = ""
    if array_count > 1:
        image_size = _u32(data, at + 0x50) if at + 0x54 <= len(data) else 0
        if image_size <= 0 and at + 0x58 <= len(data):
            wide = _u64(data, at + 0x50)
            if 0 < wide <= len(data):
                image_size = wide
        if image_size <= 0 or image_size % array_count != 0:
            raise BntxError(f"{name or 'texture'}: array of {array_count} has no per-layer size")
        stride = image_size // array_count
        if mip_bytes <= 0 or mip_bytes > stride:
            mip_bytes = stride
        start = image + layer_index * stride
        if start < 0 or start + mip_bytes > len(data):
            raise BntxError(f"{name or 'texture'}: array layer {layer_index} is past the image")
        raw = data[start : start + mip_bytes]
        layer_note = f"array layer {layer_index} of {array_count}"
    else:
        raw = data[image:]
    log2 = block_log2(
        height,
        block_h,
        layout,
        width=width,
        block_width=block_w,
        bytes_per=bytes_per,
        mip_bytes=mip_bytes,
    )
    rgba, note, log2 = _decode_swizzle(
        codec, width, height, block_w, block_h, bytes_per, tile, log2, raw, fmt, channels
    )
    if flatten_alpha and rgba:
        buf = bytearray(rgba)
        for pixel in range(3, len(buf), 4):
            buf[pixel] = 255
        rgba = bytes(buf)
        layer_note = f"{layer_note}. alpha forced opaque".strip(". ")
    if layer_note:
        note = f"{note}. {layer_note}".strip(". ")
    if family_note:
        note = f"{note}. {family_note}".strip(". ")
    block_height = 1 << max(0, log2)
    if tile == 1:
        swizzle = "linear"
    else:
        swizzle = f"deswizzled tile {tile} gob {gob_order(bytes_per)}"
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


def _decode_swizzle(
    codec: str,
    width: int,
    height: int,
    block_w: int,
    block_h: int,
    bytes_per: int,
    tile: int,
    log2: int,
    raw: bytes,
    fmt: int,
    channels: bytes,
) -> tuple[bytes, str, int]:
    """Decode one mip. A self-consistent but noisy block height is retried.

    Several log2 values share a surface size on smaller mips, so the mip
    table can agree with a wrong height. That draws as diagonal stripes.
    A much smoother candidate replaces it. Images that stay noisy keep the
    stored height and say so.
    """
    linear = deswizzle(width, height, block_w, block_h, bytes_per, tile, log2, raw)
    if codec != "rgba8":
        linear = fill_partial_gob(linear, width, height, block_w, block_h, bytes_per)
    rgba, note = _decode(codec, width, height, linear, fmt)
    rgba = _channels(rgba, width, height, channels)
    if tile == 1 or width < 32 or height < 32:
        return rgba, note, log2
    score = _noise(rgba, width, height)
    # Several block heights share one surface size. A wrong one of those turns
    # a smooth sheet into stripes. Only a much smoother same-sized height wins,
    # so a busy albedo is left on the stored height.
    if score < 18:
        return rgba, note, log2
    expected = surface_bytes(width, height, block_w, block_h, bytes_per, log2)
    best_rgba, best_log, best_score = rgba, log2, score
    for alt in range(6):
        if alt == log2:
            continue
        if surface_bytes(width, height, block_w, block_h, bytes_per, alt) != expected:
            continue
        alt_linear = deswizzle(width, height, block_w, block_h, bytes_per, tile, alt, raw)
        if codec != "rgba8":
            alt_linear = fill_partial_gob(alt_linear, width, height, block_w, block_h, bytes_per)
        try:
            alt_rgba, _alt_note = _decode(codec, width, height, alt_linear, fmt)
        except BntxError:
            continue
        alt_rgba = _channels(alt_rgba, width, height, channels)
        alt_score = _noise(alt_rgba, width, height)
        if _quieter(best_score, alt_score):
            best_rgba, best_log, best_score = alt_rgba, alt, alt_score
    chosen_note = note
    if best_log != log2:
        chosen_note = f"{note}. swizzle log2 {best_log}, stored {log2} looked like noise".strip(". ")
    swapped = _quieter_footprint(
        codec, width, height, block_w, block_h, bytes_per, tile, best_log, raw, channels, fmt, expected, best_score
    )
    if swapped is not None:
        alt_rgba, alt_note, alt_w, alt_h = swapped
        extra = f"footprint {alt_w}x{alt_h}, {block_w}x{block_h} looked like noise"
        return alt_rgba, f"{alt_note}. {extra}".strip(". "), best_log
    if best_log != log2:
        return best_rgba, chosen_note, best_log
    if score >= 70:
        extra = f"swizzle log2 {log2} still looks noisy"
        return rgba, f"{note}. {extra}".strip(". "), log2
    return rgba, note, log2


def _quieter(score: float, alt_score: float) -> bool:
    """True when the alternate is a real improvement, not a busy image's grain."""
    return alt_score + 10 <= score and alt_score <= score * 0.45


def _quieter_footprint(
    codec: str,
    width: int,
    height: int,
    block_w: int,
    block_h: int,
    bytes_per: int,
    tile: int,
    log2: int,
    raw: bytes,
    channels: bytes,
    fmt: int,
    expected: int,
    score: float,
):
    """Try the other axis of a non-square ASTC block when it uses the same bytes.

    8×5 is 8 pixels wide and 5 tall. At 1024² and log2 0 that surface is the
    same size as 5×8, so a size check cannot tell the axes apart. A much
    smoother decode wins. 6×6 has one axis, so it is left alone. The GOB
    block height stays the textureLayout value.
    """
    if not codec.startswith("astc") or block_w == block_h or block_w <= 0 or block_h <= 0 or score < 18:
        return None
    alt_w, alt_h = block_h, block_w
    if surface_bytes(width, height, alt_w, alt_h, bytes_per, log2) != expected:
        return None
    alt_linear = deswizzle(width, height, alt_w, alt_h, bytes_per, tile, log2, raw)
    alt_linear = fill_partial_gob(alt_linear, width, height, alt_w, alt_h, bytes_per)
    try:
        alt_rgba, alt_note = _decode(codec, width, height, alt_linear, fmt, footprint=(alt_w, alt_h))
    except BntxError:
        return None
    alt_rgba = _channels(alt_rgba, width, height, channels)
    alt_score = _noise(alt_rgba, width, height)
    if not _quieter(score, alt_score):
        return None
    return alt_rgba, alt_note, alt_w, alt_h


def _noise(rgba: bytes, width: int, height: int) -> float:
    """Mean absolute difference between samples four pixels apart."""
    if width < 8 or height < 8 or len(rgba) < width * height * 4:
        return 0.0
    step = 4
    total = 0
    count = 0
    for y in range(0, height - step, step):
        for x in range(0, width - step, step):
            left = (y * width + x) * 4
            right = (y * width + x + step) * 4
            below = ((y + step) * width + x) * 4
            for channel in range(3):
                total += abs(rgba[left + channel] - rgba[right + channel])
                total += abs(rgba[left + channel] - rgba[below + channel])
                count += 2
    return total / max(count, 1)


def edge_scores(
    codec: str,
    width: int,
    height: int,
    block_w: int,
    block_h: int,
    bytes_per: int,
    tile: int,
    log2: int,
    raw: bytes,
    fmt: int,
    channels: bytes = b"",
) -> dict:
    """Edge score of one mip in each GOB order. The lower score is smoother.

    This is the same neighbour difference ``_noise`` uses. It does not pick
    the order the viewer decodes with. A 16-byte element is always the Switch
    2 order. The scores are what ``tex-audit`` prints.
    """
    scores: dict[str, float | None] = {"tegra": None, "switch2": None}
    error = ""
    for order in ("tegra", "switch2"):
        linear = deswizzle(width, height, block_w, block_h, bytes_per, tile, log2, raw, order=order)
        if codec != "rgba8":
            linear = fill_partial_gob(linear, width, height, block_w, block_h, bytes_per)
        try:
            rgba, _note = _decode(codec, width, height, linear, fmt)
        except BntxError as exc:
            error = str(exc)
            continue
        rgba = _channels(rgba, width, height, channels)
        scores[order] = round(_noise(rgba, width, height), 2)
    tegra = scores["tegra"]
    alt = scores["switch2"]
    if tegra is None or alt is None:
        smoother = "unscored"
    elif abs(tegra - alt) < 0.005:
        smoother = "tie"
    elif alt < tegra:
        smoother = "switch2"
    else:
        smoother = "tegra"
    return {"tegra": tegra, "switch2": alt, "smoother": smoother, "error": error}


def score_gob_orders(data: bytes) -> list[dict]:
    """One row per BRTI: header flags and which GOB order decodes smoother.

    Mip 0 of layer 0 is scored. The viewer still uses ``gob_order``, not the
    lower score. A ``disagree`` row is a texture whose smoother order is not
    that rule.
    """
    if len(data) < 0x40 or data[:4] != b"BNTX":
        return []
    count = _i32(data, 0x24)
    table = _u64(data, 0x28)
    if count <= 0 or count > 4096 or table <= 0:
        return []
    rows = []
    for index in range(count):
        at = table + index * 8
        if at + 8 > len(data):
            break
        brti = _u64(data, at)
        if not brti or brti + 0x80 > len(data) or data[brti : brti + 4] != b"BRTI":
            continue
        try:
            rows.append(_score_brti(data, brti))
        except BntxError as exc:
            name, fmt = _identity(data, brti)
            rows.append(
                {
                    "name": name,
                    "format": fmt,
                    "formatName": "",
                    "width": 0,
                    "height": 0,
                    "footprint": "",
                    "bytesPer": None,
                    "flags": 0,
                    "dim": 0,
                    "swizzleValue": 0,
                    "tileMode": 0,
                    "decoderOrder": "",
                    "smoother": "unscored",
                    "tegra": None,
                    "switch2": None,
                    "error": str(exc),
                }
            )
    return rows


def _score_brti(data: bytes, at: int) -> dict:
    tile = _u16(data, at + 0x12)
    fmt = _u32(data, at + 0x1C)
    width = _i32(data, at + 0x24)
    height = _i32(data, at + 0x28)
    name = _string(data, _u64(data, at + 0x60))
    channels = data[at + 0x58 : at + 0x5C]
    layout = data[at + 0x34 : at + 0x3C]
    flags = data[at + 0x10] if at + 0x11 <= len(data) else 0
    dim = data[at + 0x11] if at + 0x12 <= len(data) else 0
    swizzle_value = _u16(data, at + 0x14) if at + 0x16 <= len(data) else 0
    info, _family = _format_info(fmt)
    row = {
        "name": name,
        "format": fmt,
        "formatName": info[0] if info else "",
        "width": width,
        "height": height,
        "footprint": "",
        "bytesPer": None,
        "flags": flags,
        "dim": dim,
        "swizzleValue": swizzle_value,
        "tileMode": tile,
        "decoderOrder": "",
        "smoother": "unscored",
        "tegra": None,
        "switch2": None,
        "error": "",
    }
    if info is None:
        row["error"] = f"texture format 0x{fmt:04X} is not decoded"
        return row
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        row["error"] = f"size {width}x{height} is not usable"
        return row
    codec, block_w, block_h, bytes_per = info
    row["bytesPer"] = bytes_per
    row["footprint"] = f"{block_w}x{block_h}"
    row["decoderOrder"] = "linear" if tile == 1 else gob_order(bytes_per)
    mip_table = _u64(data, at + 0x70)
    image = _u64(data, mip_table) if mip_table and mip_table + 8 <= len(data) else 0
    if image <= 0 or image >= len(data):
        row["error"] = "image data is missing"
        return row
    mip_bytes = _mip0_bytes(data, at, image)
    array_count = _u32(data, at + 0x30) if at + 0x34 <= len(data) else 1
    if array_count <= 0 or array_count > 4096:
        array_count = 1
    if array_count > 1:
        image_size = _u32(data, at + 0x50) if at + 0x54 <= len(data) else 0
        if image_size <= 0 or image_size % array_count != 0:
            row["error"] = f"array of {array_count} has no per-layer size"
            return row
        stride = image_size // array_count
        if mip_bytes <= 0 or mip_bytes > stride:
            mip_bytes = stride
        raw = data[image : image + mip_bytes]
    else:
        raw = data[image : image + mip_bytes] if mip_bytes > 0 else data[image:]
    log2 = block_log2(
        height,
        block_h,
        layout,
        width=width,
        block_width=block_w,
        bytes_per=bytes_per,
        mip_bytes=mip_bytes,
    )
    scored = edge_scores(codec, width, height, block_w, block_h, bytes_per, tile, log2, raw, fmt, channels)
    row.update(scored)
    return row


def _decode(
    codec: str,
    width: int,
    height: int,
    linear: bytes,
    fmt: int,
    footprint: tuple[int, int] | None = None,
) -> tuple[bytes, str]:
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
        bw, bh = footprint or astc[codec]
        linear = _repair_astc_partial_gob(linear, width, height, bw, bh)
        bgra = texture2ddecoder.decode_astc(linear, width, height, bw, bh)
    elif decoder is None:
        raise BntxError(f"texture format 0x{fmt:04X} has no decoder in texture2ddecoder")
    else:
        bgra = decoder(linear, width, height)
    if not bgra:
        raise BntxError(f"texture format 0x{fmt:04X} did not decode")
    rgba = _bgra_to_rgba(bytes(bgra), width, height)
    note = ""
    if codec == "bc1":
        rgba, note = _bc1_punch(linear, width, height, rgba)
    return rgba, note


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


def _expand_565(value: int) -> tuple[int, int, int]:
    """RGB565 widened to 8 bits the way texture2ddecoder does, by repeating the top bits."""
    red = (value >> 11) & 0x1F
    green = (value >> 5) & 0x3F
    blue = value & 0x1F
    return ((red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2))


def _bc1_punch(linear: bytes, width: int, height: int, rgba: bytes) -> tuple[bytes, str]:
    """BC1 blocks with color0 <= color1.

    Index 3 is transparent black in the DXT1 alpha mode, and texture2ddecoder
    leaves that texel opaque black. A block whose texels are all index 3 is a
    cutout, which is the sky on a FarView card, and stays transparent. A block
    that also uses another index is an opaque albedo: index 3 is the average
    of the two endpoints. Leaving it black draws the dotted grid on the castle
    stone. 0x1A01 and 0x1A06 are the same BC1 codec (UNORM and sRGB); neither
    is a no-alpha variant.
    """
    blocks_x = max(1, div_up(width, 4))
    blocks_y = max(1, div_up(height, 4))
    out = bytearray(rgba)
    averaged = 0
    punched = 0
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
            texels = []
            for index in range(16):
                px = bx * 4 + (index & 3)
                py = by * 4 + (index >> 2)
                if px >= width or py >= height:
                    continue
                texels.append((px, py, (bits >> (2 * index)) & 3))
            if not texels or all(code != 3 for _px, _py, code in texels):
                continue
            if all(code == 3 for _px, _py, code in texels):
                for px, py, _code in texels:
                    dest = (py * width + px) * 4
                    if dest + 3 < len(out):
                        out[dest + 3] = 0
                        punched += 1
                continue
            left = _expand_565(color0)
            right = _expand_565(color1)
            average = tuple((left[channel] + right[channel]) // 2 for channel in range(3))
            for px, py, code in texels:
                if code != 3:
                    continue
                dest = (py * width + px) * 4
                if dest + 3 >= len(out):
                    continue
                out[dest] = average[0]
                out[dest + 1] = average[1]
                out[dest + 2] = average[2]
                out[dest + 3] = 255
                averaged += 1
    note = ""
    if averaged or punched:
        note = (
            f"BC1 index 3 is the endpoint average on {averaged} texels; "
            f"{punched} solid index-3 texels stay transparent"
        )
    return bytes(out), note


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
    from ocarina_forge.formats.swizzle import default_block_log2, swizzle

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
