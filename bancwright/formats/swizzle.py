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
    """The block height a texture of this size is usually swizzled with."""
    blocks = div_up(max(height, 1), max(block_height, 1))
    height_blocks = pow2_round_up(max(1, blocks // 8))
    if height_blocks > 16:
        height_blocks = 16
    return max(0, height_blocks.bit_length() - 1)


def block_log2(height: int, pixel_block_height: int, layout: bytes) -> int:
    """Prefer the log2 stored in the BNTX texture layout when it is in range.

    Byte 4 of the 8-byte layout is that log2 on files this reader writes, and
    on the textures where the stored value is 0..5. Otherwise the height rule
    above is what the hardware uses for the base level.
    """
    if len(layout) >= 5 and layout[4] <= 5:
        return layout[4]
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
