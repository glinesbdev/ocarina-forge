//! Tegra X1 block-linear addresses, plus the Switch 2 GOB order.
//!
//! This is the address math in `ocarina_forge/formats/swizzle.py`. 16-byte
//! elements swap bit 5 and bit 6. Everything else stays on the Tegra order.

pub const GOB_TEGRA: &str = "tegra";
pub const GOB_SWITCH2: &str = "switch2";

pub fn div_up(value: i32, block: i32) -> i32 {
    if block == 0 {
        return 0;
    }
    (value + block - 1) / block
}

pub fn round_up(value: i32, alignment: i32) -> i32 {
    if alignment <= 1 {
        return value;
    }
    ((value - 1) | (alignment - 1)) + 1
}

pub fn gob_order(bytes_per: i32) -> &'static str {
    if bytes_per >= 16 {
        GOB_SWITCH2
    } else {
        GOB_TEGRA
    }
}

pub fn surface_bytes(width: i32, height: i32, block_w: i32, block_h: i32, bytes_per: i32, log2: i32) -> i32 {
    let blocks_x = div_up(width, block_w.max(1)).max(1);
    let blocks_y = div_up(height, block_h.max(1)).max(1);
    let pitch = round_up(blocks_x * bytes_per, 64);
    let rows = round_up(blocks_y, (1 << log2.clamp(0, 5)) * 8);
    pitch * rows
}

pub fn swizzle(
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
    tile_mode: i32,
    log2: i32,
    data: &[u8],
    order: Option<&str>,
) -> Vec<u8> {
    move_blocks(
        width, height, block_w, block_h, bytes_per, tile_mode, log2, data, true, order,
    )
}

pub fn deswizzle(
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
    tile_mode: i32,
    log2: i32,
    data: &[u8],
    order: Option<&str>,
) -> Vec<u8> {
    move_blocks(
        width, height, block_w, block_h, bytes_per, tile_mode, log2, data, false, order,
    )
}

pub fn fill_partial_gob(
    linear: &[u8],
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
) -> Vec<u8> {
    if bytes_per <= 0 || bytes_per > 64 || 64 % bytes_per != 0 {
        return linear.to_vec();
    }
    let blocks_x = div_up(width, block_w.max(1)).max(1);
    let blocks_y = div_up(height, block_h.max(1)).max(1);
    let gob_x = 64 / bytes_per;
    let need = (blocks_x * blocks_y * bytes_per) as usize;
    if linear.len() < need || (blocks_x < gob_x && blocks_y < 8) {
        return linear.to_vec();
    }
    let mut out = linear[..need].to_vec();
    let full_x = (blocks_x / gob_x) * gob_x;
    let full_y = (blocks_y / 8) * 8;
    let bpp = bytes_per as usize;

    let at = |x: i32, y: i32| -> usize { ((y * blocks_x + x) * bytes_per) as usize };
    let empty = |buf: &[u8], pos: usize| -> bool { buf[pos..pos + bpp].iter().all(|byte| *byte == 0) };

    let take = |out: &mut [u8], dst_x: i32, dst_y: i32, src_x: i32, src_y: i32| {
        if src_x < 0 || src_y < 0 || src_x >= blocks_x || src_y >= blocks_y {
            return;
        }
        let src = at(src_x, src_y);
        if empty(out, src) {
            return;
        }
        let dst = at(dst_x, dst_y);
        out.copy_within(src..src + bpp, dst);
    };

    if full_y > 0 && full_y < blocks_y {
        for y in full_y..blocks_y {
            for x in 0..blocks_x {
                let pos = at(x, y);
                if !empty(&out, pos) {
                    continue;
                }
                let src_x = if full_x == 0 || x < full_x { x } else { full_x - 1 };
                take(&mut out, x, y, src_x, full_y - 1);
            }
        }
    }
    if full_x > 0 && full_x < blocks_x {
        for y in 0..blocks_y {
            for x in full_x..blocks_x {
                let pos = at(x, y);
                if !empty(&out, pos) {
                    continue;
                }
                take(&mut out, x, y, full_x - 1, y);
            }
        }
    }
    if linear.len() > need {
        out.extend_from_slice(&linear[need..]);
    }
    out
}

fn move_blocks(
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
    tile_mode: i32,
    log2: i32,
    data: &[u8],
    to_swizzle: bool,
    order: Option<&str>,
) -> Vec<u8> {
    let blocks_x = div_up(width, block_w).max(1);
    let blocks_y = div_up(height, block_h).max(1);
    let tight = (blocks_x * blocks_y * bytes_per) as usize;
    if tile_mode == 1 {
        let mut out = vec![0u8; tight];
        let n = tight.min(data.len());
        out[..n].copy_from_slice(&data[..n]);
        return out;
    }
    let block_height = 1i32 << log2.max(0);
    let chosen = match order {
        Some(text) if !text.is_empty() => text,
        _ => gob_order(bytes_per),
    };
    let mut out = if to_swizzle {
        let pitch = round_up(blocks_x * bytes_per, 64);
        let surface = pitch * round_up(blocks_y, block_height * 8);
        vec![0u8; surface.max(0) as usize]
    } else {
        vec![0u8; tight]
    };
    let bpp = bytes_per as usize;
    for y in 0..blocks_y {
        for x in 0..blocks_x {
            let pos = gob_address(x, y, blocks_x, bytes_per, block_height, chosen) as usize;
            let linear = ((y * blocks_x + x) * bytes_per) as usize;
            if to_swizzle {
                if pos + bpp <= out.len() && linear + bpp <= data.len() {
                    out[pos..pos + bpp].copy_from_slice(&data[linear..linear + bpp]);
                }
            } else if pos + bpp <= data.len() && linear + bpp <= out.len() {
                out[linear..linear + bpp].copy_from_slice(&data[pos..pos + bpp]);
            }
        }
    }
    out
}

fn gob_address(x: i32, y: i32, width_blocks: i32, bytes_per: i32, block_height: i32, order: &str) -> i32 {
    let image_width_in_gobs = div_up(width_blocks * bytes_per, 64);
    let gob = (y / (8 * block_height)) * 512 * block_height * image_width_in_gobs
        + (x * bytes_per / 64) * 512 * block_height
        + ((y % (8 * block_height)) / 8) * 512;
    gob + gob_local(x * bytes_per, y, order)
}

fn gob_local(xx: i32, y: i32, order: &str) -> i32 {
    if order == GOB_SWITCH2 {
        return ((xx % 64) / 32) * 256
            + ((y % 8) / 4) * 128
            + ((xx % 32) / 16) * 64
            + ((y % 4) / 2) * 32
            + (y % 2) * 16
            + (xx % 16);
    }
    ((xx % 64) / 32) * 256 + ((y % 8) / 2) * 64 + ((xx % 32) / 16) * 32 + (y % 2) * 16 + (xx % 16)
}
