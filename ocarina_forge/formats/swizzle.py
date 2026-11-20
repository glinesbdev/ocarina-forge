"""Tegra X1 block-linear address calculation.

The GOB address is the one published in the Tegra X1 Technical Reference
Manual (GPU block-linear layout). This is an original implementation of that
formula. It is not taken from the GPL BNTX extractors.
"""

from __future__ import annotations


def div_up(value: int, block: int) -> int:
    return (value + block - 1) // block if block else 0


def round_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return ((value - 1) | (alignment - 1)) + 1


def pow2_round_up(value: int) -> int:
    if value <= 1:
        return 1
    value -= 1
    value |= value >> 1
    value |= value >> 2
    value |= value >> 4
    value |= value >> 8
    value |= value >> 16
    return value + 1


def default_block_log2(height: int, block_height: int) -> int:
    """The block height a Switch 1 texture of this size is usually swizzled with."""
    blocks = div_up(max(height, 1), max(block_height, 1))
    height_blocks = pow2_round_up(max(1, blocks // 8))
    if height_blocks > 16:
        height_blocks = 16
    return max(0, height_blocks.bit_length() - 1)


def surface_bytes(width: int, height: int, block_w: int, block_h: int, bytes_per: int, log2: int) -> int:
    """Byte size of one mip after the block-linear pitch and GOB padding."""
    blocks_x = max(1, div_up(width, max(block_w, 1)))
    blocks_y = max(1, div_up(height, max(block_h, 1)))
    pitch = round_up(blocks_x * bytes_per, 64)
    rows = round_up(blocks_y, (1 << max(0, min(log2, 5))) * 8)
    return pitch * rows


def _stored_log2(layout: bytes) -> int | None:
    """Block-height log2 from the BRTI layout bytes.

    On these Switch 2 textures the value is the low 3 bits of layout byte 0
    (BRTI+0x34). Byte 4 is 0xC9 and is not a log2. Files this reader writes
    still store the log2 at byte 4 and leave byte 0 at 0.
    """
    if not layout:
        return None
    legacy = layout[4] if len(layout) >= 5 else 0xFF
    if layout[0] == 0 and legacy <= 5:
        return legacy
    low = layout[0] & 7
    if low <= 5:
        return low
    if legacy <= 5:
        return legacy
    return None


def _log2_matching_mip(width: int, height: int, block_w: int, block_h: int, bytes_per: int, mip_bytes: int) -> int | None:
    matches = [
        log2
        for log2 in range(6)
        if surface_bytes(width, height, block_w, block_h, bytes_per, log2) == mip_bytes
    ]
    return matches[0] if matches else None


def block_log2(
    height: int,
    pixel_block_height: int,
    layout: bytes,
    *,
    width: int = 0,
    block_width: int = 1,
    bytes_per: int = 0,
    mip_bytes: int = 0,
) -> int:
    """Block height for the base mip.

    Prefer layout byte 0. When that value's padded surface does not match the
    mip-table size, use the log2 that does. Files written here keep the old
    byte-4 field, and a texture with no usable stored value falls back to the
    Switch 1 height rule.
    """
    stored = _stored_log2(layout)
    if mip_bytes > 0 and width > 0 and bytes_per > 0:
        stored_size = (
            surface_bytes(width, height, block_width, pixel_block_height, bytes_per, stored)
            if stored is not None
            else None
        )
        if stored is None or stored_size != mip_bytes:
            derived = _log2_matching_mip(width, height, block_width, pixel_block_height, bytes_per, mip_bytes)
            if derived is not None:
                return derived
    if stored is not None:
        return stored
    return default_block_log2(height, pixel_block_height)


def _gob_address(x: int, y: int, width_blocks: int, bytes_per: int, block_height: int) -> int:
    image_width_in_gobs = div_up(width_blocks * bytes_per, 64)
    gob = (
        (y // (8 * block_height)) * 512 * block_height * image_width_in_gobs
        + (x * bytes_per // 64) * 512 * block_height
        + ((y % (8 * block_height)) // 8) * 512
    )
    xx = x * bytes_per
    return (
        gob
        + ((xx % 64) // 32) * 256
        + ((y % 8) // 2) * 64
        + ((xx % 32) // 16) * 32
        + (y % 2) * 16
        + (xx % 16)
    )


def swizzle(
    width: int,
    height: int,
    block_w: int,
    block_h: int,
    bytes_per: int,
    tile_mode: int,
    log2: int,
    linear: bytes,
) -> bytes:
    """Pack linear blocks into Tegra order. Tile mode 1 is already linear."""
    return _move(width, height, block_w, block_h, bytes_per, tile_mode, log2, linear, to_swizzle=True)


def deswizzle(
    width: int,
    height: int,
    block_w: int,
    block_h: int,
    bytes_per: int,
    tile_mode: int,
    log2: int,
    swizzled: bytes,
) -> bytes:
    """Unpack Tegra block-linear data into linear blocks, one block after another."""
    return _move(width, height, block_w, block_h, bytes_per, tile_mode, log2, swizzled, to_swizzle=False)


def fill_partial_gob(
    linear: bytes,
    width: int,
    height: int,
    block_w: int,
    block_h: int,
    bytes_per: int,
) -> bytes:
    """Copy the last full GOB block into a zero block on a partial edge.

    A GOB is 64 bytes wide and 8 block-rows tall. 455 BC1 blocks is 114, so
    blocks 112 and 113 sit in the last GOB (pitch 120) and are left zero.
    With repeat wrap that black border is the dotted line on the stone.
    A block that already has bytes is left alone. Heightmaps do not call this:
    a zero sample there is a real height.
    """
    if bytes_per <= 0 or bytes_per > 64 or 64 % bytes_per:
        return linear
    blocks_x = max(1, div_up(width, max(block_w, 1)))
    blocks_y = max(1, div_up(height, max(block_h, 1)))
    gob_x = 64 // bytes_per
    need = blocks_x * blocks_y * bytes_per
    if len(linear) < need or blocks_x < gob_x and blocks_y < 8:
        return linear
    out = bytearray(linear[:need])
    full_x = (blocks_x // gob_x) * gob_x
    full_y = (blocks_y // 8) * 8
    empty = b"\0" * bytes_per

    def at(x: int, y: int) -> int:
        return (y * blocks_x + x) * bytes_per

    def take(dst_x: int, dst_y: int, src_x: int, src_y: int) -> None:
        if src_x < 0 or src_y < 0 or src_x >= blocks_x or src_y >= blocks_y:
            return
        src = at(src_x, src_y)
        if out[src : src + bytes_per] == empty:
            return
        dst = at(dst_x, dst_y)
        out[dst : dst + bytes_per] = out[src : src + bytes_per]

    if 0 < full_y < blocks_y:
        for y in range(full_y, blocks_y):
            for x in range(blocks_x):
                if out[at(x, y) : at(x, y) + bytes_per] != empty:
                    continue
                src_x = x if full_x == 0 or x < full_x else full_x - 1
                take(x, y, src_x, full_y - 1)
    if 0 < full_x < blocks_x:
        for y in range(blocks_y):
            for x in range(full_x, blocks_x):
                if out[at(x, y) : at(x, y) + bytes_per] != empty:
                    continue
                take(x, y, full_x - 1, y)
    if len(linear) > need:
        return bytes(out) + linear[need:]
    return bytes(out)


def _move(
    width: int,
    height: int,
    block_w: int,
    block_h: int,
    bytes_per: int,
    tile_mode: int,
    log2: int,
    data: bytes,
    to_swizzle: bool,
) -> bytes:
    blocks_x = max(1, div_up(width, block_w))
    blocks_y = max(1, div_up(height, block_h))
    tight = blocks_x * blocks_y * bytes_per
    if tile_mode == 1:
        return bytes(data[:tight]).ljust(tight, b"\0")
    block_height = 1 << max(0, log2)
    if to_swizzle:
        pitch = round_up(blocks_x * bytes_per, 64)
        surface = pitch * round_up(blocks_y, block_height * 8)
        out = bytearray(surface)
    else:
        out = bytearray(tight)
    for y in range(blocks_y):
        for x in range(blocks_x):
            pos = _gob_address(x, y, blocks_x, bytes_per, block_height)
            linear = (y * blocks_x + x) * bytes_per
            if to_swizzle:
                if pos + bytes_per <= len(out):
                    out[pos : pos + bytes_per] = data[linear : linear + bytes_per]
            elif pos + bytes_per <= len(data):
                out[linear : linear + bytes_per] = data[pos : pos + bytes_per]
    return bytes(out)
