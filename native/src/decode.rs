//! Block decode to RGBA, matching `bntx._decode` and `_decode_swizzle`.
//!
//! BC1–BC5 and BC7 go through `bcdec_rs` (a pure-Rust port of bcdec, MIT).
//! ASTC LDR goes through `astc-decode` (Apache-2.0, no C++ and no ISPC).
//! Both compile to wasm32. BC6 stays on the Python texture2ddecoder path:
//! its half-float rounding is not what this crate claims to match.
//! Output is RGBA, which is what `_bgra_to_rgba` produces from that package.

use crate::swizzle::{self, deswizzle, fill_partial_gob, surface_bytes};

pub fn supported(codec: &str) -> bool {
    matches!(
        codec,
        "rgba8" | "bc1" | "bc2" | "bc3" | "bc4" | "bc5" | "bc7" | "astc4" | "astc5x4" | "astc5" | "astc6x5"
            | "astc6" | "astc8x5" | "astc8x6" | "astc8" | "astc10x5" | "astc10x6" | "astc10x8" | "astc10"
            | "astc12x10" | "astc12"
    )
}

pub fn decode_linear(
    codec: &str,
    width: i32,
    height: i32,
    linear: &[u8],
    block_w: i32,
    block_h: i32,
) -> Option<(Vec<u8>, String)> {
    if width <= 0 || height <= 0 || !supported(codec) {
        return None;
    }
    if codec == "rgba8" {
        let need = (width as usize) * (height as usize) * 4;
        let mut out = vec![0u8; need];
        let n = need.min(linear.len());
        out[..n].copy_from_slice(&linear[..n]);
        return Some((out, String::new()));
    }
    let (bw, bh, bpp) = footprint(codec, block_w, block_h)?;
    let prepared;
    let blocks = if codec.starts_with("astc") {
        prepared = repair_astc_partial_gob(linear, width, height, bw, bh);
        prepared.as_slice()
    } else {
        linear
    };
    let rgba = decode_blocks(codec, width, height, blocks, bw, bh, bpp)?;
    if codec == "bc1" {
        let (punched, note) = bc1_punch(blocks, width, height, rgba);
        return Some((punched, note));
    }
    Some((rgba, String::new()))
}

pub fn decode_swizzle(
    codec: &str,
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
    tile: i32,
    log2: i32,
    raw: &[u8],
    channels: &[u8],
) -> Option<(Vec<u8>, String, i32)> {
    if !supported(codec) || width <= 0 || height <= 0 {
        return None;
    }
    let (rgba, note) = decode_one(
        codec, width, height, block_w, block_h, bytes_per, tile, log2, raw, channels,
    )?;
    if tile == 1 || width < 32 || height < 32 {
        return Some((rgba, note, log2));
    }
    let score = noise(&rgba, width, height);
    if score < 18.0 {
        return Some((rgba, note, log2));
    }
    let expected = surface_bytes(width, height, block_w, block_h, bytes_per, log2);
    let mut best_rgba = rgba;
    let mut best_log = log2;
    let mut best_score = score;
    for alt in 0..6 {
        if alt == log2 {
            continue;
        }
        if surface_bytes(width, height, block_w, block_h, bytes_per, alt) != expected {
            continue;
        }
        let Some((alt_rgba, _alt_note)) = decode_one(
            codec, width, height, block_w, block_h, bytes_per, tile, alt, raw, channels,
        ) else {
            continue;
        };
        let alt_score = noise(&alt_rgba, width, height);
        if quieter(best_score, alt_score) {
            best_rgba = alt_rgba;
            best_log = alt;
            best_score = alt_score;
        }
    }
    let mut chosen_note = note.clone();
    if best_log != log2 {
        chosen_note = strip_dots(&format!("{note}. swizzle log2 {best_log}, stored {log2} looked like noise"));
    }
    if let Some((alt_rgba, alt_note, alt_w, alt_h)) = quieter_footprint(
        codec, width, height, block_w, block_h, bytes_per, tile, best_log, raw, channels, expected, best_score,
    ) {
        let extra = format!("footprint {alt_w}x{alt_h}, {block_w}x{block_h} looked like noise");
        return Some((alt_rgba, strip_dots(&format!("{alt_note}. {extra}")), best_log));
    }
    if best_log != log2 {
        return Some((best_rgba, chosen_note, best_log));
    }
    if score >= 70.0 {
        let extra = format!("swizzle log2 {log2} still looks noisy");
        return Some((best_rgba, strip_dots(&format!("{note}. {extra}")), log2));
    }
    Some((best_rgba, note, log2))
}

fn decode_one(
    codec: &str,
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
    tile: i32,
    log2: i32,
    raw: &[u8],
    channels: &[u8],
) -> Option<(Vec<u8>, String)> {
    let mut linear = deswizzle(width, height, block_w, block_h, bytes_per, tile, log2, raw, None);
    if codec != "rgba8" {
        linear = fill_partial_gob(&linear, width, height, block_w, block_h, bytes_per);
    }
    let (rgba, note) = decode_linear(codec, width, height, &linear, block_w, block_h)?;
    Some((apply_channels(&rgba, width, height, channels), note))
}

fn quieter_footprint(
    codec: &str,
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
    tile: i32,
    log2: i32,
    raw: &[u8],
    channels: &[u8],
    expected: i32,
    score: f64,
) -> Option<(Vec<u8>, String, i32, i32)> {
    if !codec.starts_with("astc") || block_w == block_h || block_w <= 0 || block_h <= 0 || score < 18.0 {
        return None;
    }
    let (alt_w, alt_h) = (block_h, block_w);
    if surface_bytes(width, height, alt_w, alt_h, bytes_per, log2) != expected {
        return None;
    }
    let mut linear = deswizzle(width, height, alt_w, alt_h, bytes_per, tile, log2, raw, None);
    linear = fill_partial_gob(&linear, width, height, alt_w, alt_h, bytes_per);
    let (rgba, note) = decode_linear(codec, width, height, &linear, alt_w, alt_h)?;
    let rgba = apply_channels(&rgba, width, height, channels);
    let alt_score = noise(&rgba, width, height);
    if !quieter(score, alt_score) {
        return None;
    }
    Some((rgba, note, alt_w, alt_h))
}

fn footprint(codec: &str, block_w: i32, block_h: i32) -> Option<(i32, i32, i32)> {
    let pair = match codec {
        "bc1" | "bc4" => (4, 4, 8),
        "bc2" | "bc3" | "bc5" | "bc7" => (4, 4, 16),
        "astc4" => (4, 4, 16),
        "astc5x4" => (5, 4, 16),
        "astc5" => (5, 5, 16),
        "astc6x5" => (6, 5, 16),
        "astc6" => (6, 6, 16),
        "astc8x5" => (8, 5, 16),
        "astc8x6" => (8, 6, 16),
        "astc8" => (8, 8, 16),
        "astc10x5" => (10, 5, 16),
        "astc10x6" => (10, 6, 16),
        "astc10x8" => (10, 8, 16),
        "astc10" => (10, 10, 16),
        "astc12x10" => (12, 10, 16),
        "astc12" => (12, 12, 16),
        _ => return None,
    };
    if block_w > 0 && block_h > 0 && codec.starts_with("astc") {
        return Some((block_w, block_h, 16));
    }
    Some(pair)
}

fn decode_blocks(codec: &str, width: i32, height: i32, linear: &[u8], bw: i32, bh: i32, bpp: i32) -> Option<Vec<u8>> {
    let blocks_x = swizzle::div_up(width, bw).max(1);
    let blocks_y = swizzle::div_up(height, bh).max(1);
    let mut out = vec![0u8; (width as usize) * (height as usize) * 4];
    let footprint = if codec.starts_with("astc") {
        Some(astc_decode::Footprint::new(bw as u32, bh as u32))
    } else {
        None
    };
    for by in 0..blocks_y {
        for bx in 0..blocks_x {
            let src = ((by * blocks_x + bx) * bpp) as usize;
            if src + bpp as usize > linear.len() {
                continue;
            }
            let block = &linear[src..src + bpp as usize];
            if codec.starts_with("astc") {
                let mut pixels = vec![0u8; (bw * bh * 4) as usize];
                let mut input = [0u8; 16];
                input.copy_from_slice(block);
                astc_decode::astc_decode_block(&input, footprint.unwrap(), |x, y, color| {
                    let at = ((y as i32 * bw + x as i32) * 4) as usize;
                    if at + 4 <= pixels.len() {
                        pixels[at..at + 4].copy_from_slice(&color);
                    }
                });
                blit(&mut out, width, height, bx, by, bw, bh, &pixels, (bw * 4) as usize);
            } else {
                blit_bc(&mut out, width, height, bx, by, codec, block);
            }
        }
    }
    Some(out)
}

fn blit_bc(out: &mut [u8], width: i32, height: i32, bx: i32, by: i32, codec: &str, block: &[u8]) {
    match codec {
        "bc1" => {
            let pixels = bc1_block(block, false);
            blit(out, width, height, bx, by, 4, 4, &pixels, 16);
        }
        "bc2" | "bc3" => {
            // This texture2ddecoder build has no BC2 entry point. The Python
            // path decodes BC2 with decode_bc3, and its BC3 colour block follows
            // the BC1 3-colour rule when color0 <= color1.
            let pixels = bc3_block(block);
            blit(out, width, height, bx, by, 4, 4, &pixels, 16);
        }
        "bc7" => {
            let mut pixels = [0u8; 64];
            bcdec_rs::bc7(block, &mut pixels, 16);
            blit(out, width, height, bx, by, 4, 4, &pixels, 16);
        }
        "bc4" => {
            let pixels = bc4_block(block);
            blit_expanded(out, width, height, bx, by, &pixels, 1);
        }
        "bc5" => {
            let mut pixels = [0u8; 32];
            let red = bc4_block(&block[..8]);
            let green = bc4_block(&block[8..]);
            for index in 0..16 {
                pixels[index * 2] = red[index];
                pixels[index * 2 + 1] = green[index];
            }
            blit_expanded(out, width, height, bx, by, &pixels, 2);
        }
        _ => {}
    }
}

fn expand5(value: u32) -> u32 {
    (value << 3) | (value >> 2)
}

fn expand6(value: u32) -> u32 {
    (value << 2) | (value >> 4)
}

fn bc1_block(block: &[u8], opaque_only: bool) -> [u8; 64] {
    let c0 = u16::from_le_bytes([block[0], block[1]]) as u32;
    let c1 = u16::from_le_bytes([block[2], block[3]]) as u32;
    let e0 = [
        expand5((c0 >> 11) & 31) as u8,
        expand6((c0 >> 5) & 63) as u8,
        expand5(c0 & 31) as u8,
    ];
    let e1 = [
        expand5((c1 >> 11) & 31) as u8,
        expand6((c1 >> 5) & 63) as u8,
        expand5(c1 & 31) as u8,
    ];
    let mut pal = [[0u8; 4]; 4];
    pal[0] = [e0[0], e0[1], e0[2], 255];
    pal[1] = [e1[0], e1[1], e1[2], 255];
    if c0 > c1 || opaque_only {
        for channel in 0..3 {
            pal[2][channel] = ((2 * e0[channel] as u32 + e1[channel] as u32) / 3) as u8;
            pal[3][channel] = ((e0[channel] as u32 + 2 * e1[channel] as u32) / 3) as u8;
        }
        pal[2][3] = 255;
        pal[3][3] = 255;
    } else {
        for channel in 0..3 {
            pal[2][channel] = ((e0[channel] as u32 + e1[channel] as u32) / 2) as u8;
        }
        pal[2][3] = 255;
        pal[3] = [0, 0, 0, 255];
    }
    let mut bits = u32::from_le_bytes([block[4], block[5], block[6], block[7]]);
    let mut pixels = [0u8; 64];
    for index in 0..16 {
        let color = pal[(bits & 3) as usize];
        pixels[index * 4..index * 4 + 4].copy_from_slice(&color);
        bits >>= 2;
    }
    pixels
}

fn bc4_block(block: &[u8]) -> [u8; 16] {
    let a0 = block[0] as i32;
    let a1 = block[1] as i32;
    let mut pal = [0u8; 8];
    pal[0] = a0 as u8;
    pal[1] = a1 as u8;
    if a0 > a1 {
        for step in 1..7 {
            pal[(step + 1) as usize] = (((7 - step) * a0 + step * a1) / 7) as u8;
        }
    } else {
        pal[2] = ((4 * a0 + a1) / 5) as u8;
        pal[3] = ((3 * a0 + 2 * a1) / 5) as u8;
        pal[4] = ((2 * a0 + 3 * a1) / 5) as u8;
        pal[5] = ((a0 + 4 * a1) / 5) as u8;
        pal[6] = 0;
        pal[7] = 255;
    }
    let bits = u64::from_le_bytes(block[..8].try_into().unwrap()) >> 16;
    let mut pixels = [0u8; 16];
    for index in 0..16 {
        pixels[index] = pal[((bits >> (3 * index)) & 7) as usize];
    }
    pixels
}

fn bc3_block(block: &[u8]) -> [u8; 64] {
    let alpha = bc4_block(&block[..8]);
    let mut pixels = bc1_block(&block[8..], false);
    for index in 0..16 {
        pixels[index * 4 + 3] = alpha[index];
    }
    pixels
}

fn blit(out: &mut [u8], width: i32, height: i32, bx: i32, by: i32, bw: i32, bh: i32, pixels: &[u8], pitch: usize) {
    for row in 0..bh {
        let py = by * bh + row;
        if py >= height {
            break;
        }
        for col in 0..bw {
            let px = bx * bw + col;
            if px >= width {
                break;
            }
            let src = row as usize * pitch + col as usize * 4;
            let dst = ((py * width + px) * 4) as usize;
            if src + 4 <= pixels.len() && dst + 4 <= out.len() {
                out[dst..dst + 4].copy_from_slice(&pixels[src..src + 4]);
            }
        }
    }
}

fn blit_expanded(out: &mut [u8], width: i32, height: i32, bx: i32, by: i32, pixels: &[u8], step: usize) {
    for row in 0..4 {
        let py = by * 4 + row;
        if py >= height {
            break;
        }
        for col in 0..4 {
            let px = bx * 4 + col;
            if px >= width {
                break;
            }
            let src = (row as usize * 4 + col as usize) * step;
            let dst = ((py * width + px) * 4) as usize;
            if src >= pixels.len() || dst + 4 > out.len() {
                continue;
            }
            out[dst] = pixels[src];
            out[dst + 1] = if step > 1 { pixels[src + 1] } else { 0 };
            out[dst + 2] = 0;
            out[dst + 3] = 255;
        }
    }
}

fn repair_astc_partial_gob(linear: &[u8], width: i32, height: i32, block_w: i32, block_h: i32) -> Vec<u8> {
    let blocks_x = swizzle::div_up(width, block_w).max(1);
    let blocks_y = swizzle::div_up(height, block_h).max(1);
    if blocks_y % 8 == 0 {
        return linear.to_vec();
    }
    let bpp = 16usize;
    let need = (blocks_x * blocks_y) as usize * bpp;
    let mut out = vec![0u8; need];
    let n = need.min(linear.len());
    out[..n].copy_from_slice(&linear[..n]);
    let start = (blocks_y / 8) * 8;
    for y in start.max(1)..blocks_y {
        for x in 0..blocks_x {
            let at = ((y * blocks_x + x) as usize) * bpp;
            let above = (((y - 1) * blocks_x + x) as usize) * bpp;
            if out[at..at + bpp].iter().all(|byte| *byte == 0)
                && out[above..above + bpp].iter().any(|byte| *byte != 0)
            {
                out.copy_within(above..above + bpp, at);
            }
        }
    }
    if linear.len() > need {
        out.extend_from_slice(&linear[need..]);
    }
    out
}

fn bc1_punch(linear: &[u8], width: i32, height: i32, rgba: Vec<u8>) -> (Vec<u8>, String) {
    let blocks_x = swizzle::div_up(width, 4).max(1);
    let blocks_y = swizzle::div_up(height, 4).max(1);
    let mut out = rgba;
    let mut averaged = 0i32;
    let mut punched = 0i32;
    for by in 0..blocks_y {
        for bx in 0..blocks_x {
            let block_at = ((by * blocks_x + bx) * 8) as usize;
            if block_at + 8 > linear.len() {
                continue;
            }
            let color0 = linear[block_at] as u32 | ((linear[block_at + 1] as u32) << 8);
            let color1 = linear[block_at + 2] as u32 | ((linear[block_at + 3] as u32) << 8);
            if color0 > color1 {
                continue;
            }
            let bits = u32::from_le_bytes(linear[block_at + 4..block_at + 8].try_into().unwrap());
            let mut texels = [(0i32, 0i32, 0u32); 16];
            let mut count = 0usize;
            for index in 0..16 {
                let px = bx * 4 + (index & 3);
                let py = by * 4 + (index >> 2);
                if px >= width || py >= height {
                    continue;
                }
                texels[count] = (px, py, (bits >> (2 * index)) & 3);
                count += 1;
            }
            if count == 0 || texels[..count].iter().all(|texel| texel.2 != 3) {
                continue;
            }
            if texels[..count].iter().all(|texel| texel.2 == 3) {
                for &(px, py, _) in &texels[..count] {
                    let dest = ((py * width + px) * 4) as usize;
                    if dest + 3 < out.len() {
                        out[dest + 3] = 0;
                        punched += 1;
                    }
                }
                continue;
            }
            let left = expand_565(color0);
            let right = expand_565(color1);
            let average = [
                (left[0] as i32 + right[0] as i32) / 2,
                (left[1] as i32 + right[1] as i32) / 2,
                (left[2] as i32 + right[2] as i32) / 2,
            ];
            for &(px, py, code) in &texels[..count] {
                if code != 3 {
                    continue;
                }
                let dest = ((py * width + px) * 4) as usize;
                if dest + 3 >= out.len() {
                    continue;
                }
                out[dest] = average[0] as u8;
                out[dest + 1] = average[1] as u8;
                out[dest + 2] = average[2] as u8;
                out[dest + 3] = 255;
                averaged += 1;
            }
        }
    }
    let note = if averaged != 0 || punched != 0 {
        format!(
            "BC1 index 3 is the endpoint average on {averaged} texels; {punched} solid index-3 texels stay transparent"
        )
    } else {
        String::new()
    };
    (out, note)
}

fn expand_565(value: u32) -> [u8; 3] {
    let red = (value >> 11) & 0x1F;
    let green = (value >> 5) & 0x3F;
    let blue = value & 0x1F;
    [
        ((red << 3) | (red >> 2)) as u8,
        ((green << 2) | (green >> 4)) as u8,
        ((blue << 3) | (blue >> 2)) as u8,
    ]
}

pub fn apply_channels(rgba: &[u8], width: i32, height: i32, channels: &[u8]) -> Vec<u8> {
    if channels.len() < 4 || channels == [2, 3, 4, 5] {
        return rgba.to_vec();
    }
    let mut out = vec![0u8; rgba.len()];
    let pixels = (width.max(0) as usize) * (height.max(0) as usize);
    for index in 0..pixels {
        let at = index * 4;
        let pixel = if at + 4 <= rgba.len() {
            [rgba[at], rgba[at + 1], rgba[at + 2], rgba[at + 3]]
        } else {
            [0, 0, 0, 255]
        };
        for dest in 0..4 {
            let source = channels[dest];
            out[at + dest] = if source == 0 {
                0
            } else if source == 1 {
                255
            } else if (2..=5).contains(&source) {
                pixel[(source - 2) as usize]
            } else {
                pixel[dest]
            };
        }
    }
    out
}

pub fn noise(rgba: &[u8], width: i32, height: i32) -> f64 {
    if width < 8 || height < 8 || rgba.len() < (width as usize) * (height as usize) * 4 {
        return 0.0;
    }
    let step = 4i32;
    let mut total = 0i32;
    let mut count = 0i32;
    let mut y = 0i32;
    while y < height - step {
        let mut x = 0i32;
        while x < width - step {
            let left = ((y * width + x) * 4) as usize;
            let right = ((y * width + x + step) * 4) as usize;
            let below = (((y + step) * width + x) * 4) as usize;
            for channel in 0..3 {
                total += (rgba[left + channel] as i32 - rgba[right + channel] as i32).abs();
                total += (rgba[left + channel] as i32 - rgba[below + channel] as i32).abs();
                count += 2;
            }
            x += step;
        }
        y += step;
    }
    total as f64 / (count.max(1) as f64)
}

fn quieter(score: f64, alt_score: f64) -> bool {
    alt_score + 10.0 <= score && alt_score <= score * 0.45
}

fn strip_dots(text: &str) -> String {
    text.trim_matches(['.', ' ']).to_string()
}
