//! BC7 mode 6, one subset, 4-bit indices.
//!
//! This is the encoder in `ocarina_forge/formats/gpu.py`: min and max
//! endpoints, one shared p-bit, then a 16-weight index. The bit layout matches
//! that function, including the tie break (the first weight wins). No subset
//! partition and no target-specific SIMD, so the same code can compile to
//! `wasm32-unknown-unknown`.

const WEIGHTS: [i32; 16] = [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64];

pub fn encode_bc7(width: u32, height: u32, rgba: &[u8]) -> Vec<u8> {
    if width == 0 || height == 0 {
        return Vec::new();
    }
    let need = (width as usize).saturating_mul(height as usize).saturating_mul(4);
    if rgba.len() < need {
        return Vec::new();
    }
    let blocks_x = ((width + 3) / 4) as usize;
    let blocks_y = ((height + 3) / 4) as usize;
    let mut out = vec![0u8; blocks_x * blocks_y * 16];
    let mut at = 0usize;
    let mut y = 0u32;
    while y < height {
        let mut x = 0u32;
        while x < width {
            mode6(rgba, width, height, x, y, &mut out[at..at + 16]);
            at += 16;
            x += 4;
        }
        y += 4;
    }
    out
}

pub fn bc7_mip_chain(width: u32, height: u32, rgba: &[u8]) -> Vec<Vec<u8>> {
    if width == 0 || height == 0 || width % 4 != 0 || height % 4 != 0 {
        return Vec::new();
    }
    let need = (width as usize).saturating_mul(height as usize).saturating_mul(4);
    if rgba.len() < need {
        return Vec::new();
    }
    let mut levels = Vec::new();
    let mut w = width;
    let mut h = height;
    let mut mip = rgba[..need].to_vec();
    loop {
        let block = encode_bc7(w, h, &mip);
        if block.is_empty() {
            return Vec::new();
        }
        levels.push(block);
        if w == 1 && h == 1 {
            break;
        }
        let (nw, nh, next) = downsample(w, h, &mip);
        w = nw;
        h = nh;
        mip = next;
    }
    levels
}

fn mode6(rgba: &[u8], width: u32, height: u32, x: u32, y: u32, out: &mut [u8]) {
    let mut pixels = [0u8; 64];
    for row in 0..4 {
        let sy = (y + row).min(height - 1);
        for col in 0..4 {
            let sx = (x + col).min(width - 1);
            let src = ((sy * width + sx) * 4) as usize;
            let dest = (row as usize * 4 + col as usize) * 4;
            pixels[dest..dest + 4].copy_from_slice(&rgba[src..src + 4]);
        }
    }
    let mut low = [255u8; 4];
    let mut high = [0u8; 4];
    for pixel in 0..16 {
        let at = pixel * 4;
        for channel in 0..4 {
            let value = pixels[at + channel];
            if value < low[channel] {
                low[channel] = value;
            }
            if value > high[channel] {
                high[channel] = value;
            }
        }
    }
    let (mut low, mut low_p) = snap(low);
    let (mut high, mut high_p) = snap(high);
    let mut indices = [0u8; 16];
    if low != high {
        for pixel in 0..16 {
            indices[pixel] = best_index(&pixels[pixel * 4..pixel * 4 + 4], low, high);
        }
        if indices[0] >= 8 {
            std::mem::swap(&mut low, &mut high);
            std::mem::swap(&mut low_p, &mut high_p);
            for index in &mut indices {
                *index = 15 - *index;
            }
        }
    }
    let mut raw = [0u8; 16];
    let mut cursor = 0usize;
    put(&mut raw, &mut cursor, 0b1000000, 7);
    for channel in 0..4 {
        put(&mut raw, &mut cursor, (low[channel] >> 1) as u32, 7);
        put(&mut raw, &mut cursor, (high[channel] >> 1) as u32, 7);
    }
    put(&mut raw, &mut cursor, low_p as u32, 1);
    put(&mut raw, &mut cursor, high_p as u32, 1);
    put(&mut raw, &mut cursor, indices[0] as u32, 3);
    for index in indices.iter().skip(1) {
        put(&mut raw, &mut cursor, *index as u32, 4);
    }
    debug_assert_eq!(cursor, 128);
    out.copy_from_slice(&raw);
}

fn snap(color: [u8; 4]) -> ([u8; 4], u8) {
    let bit = color[0] & 1;
    let mut snapped = [0u8; 4];
    for (index, channel) in color.iter().copied().enumerate() {
        let value = if (channel & 1) == bit {
            channel as i32
        } else if channel != 0 && bit == 0 {
            channel as i32 - 1
        } else if channel < 255 {
            channel as i32 + 1
        } else {
            channel as i32 - 1
        };
        snapped[index] = value.clamp(0, 255) as u8;
    }
    (snapped, bit)
}

fn best_index(pixel: &[u8], low: [u8; 4], high: [u8; 4]) -> u8 {
    let mut best = 0u8;
    let mut best_error = 1i32 << 30;
    for (index, weight) in WEIGHTS.iter().copied().enumerate() {
        let mut error = 0i32;
        for channel in 0..4 {
            let mixed = ((64 - weight) * low[channel] as i32 + weight * high[channel] as i32 + 32) >> 6;
            let delta = pixel[channel] as i32 - mixed;
            error += delta * delta;
        }
        if error < best_error {
            best_error = error;
            best = index as u8;
        }
    }
    best
}

fn put(raw: &mut [u8; 16], cursor: &mut usize, value: u32, count: usize) {
    for shift in 0..count {
        if ((value >> shift) & 1) != 0 {
            let bit = *cursor;
            raw[bit >> 3] |= 1 << (bit & 7);
        }
        *cursor += 1;
    }
}

fn downsample(width: u32, height: u32, rgba: &[u8]) -> (u32, u32, Vec<u8>) {
    let nw = (width / 2).max(1);
    let nh = (height / 2).max(1);
    let mut out = vec![0u8; (nw * nh * 4) as usize];
    for y in 0..nh {
        for x in 0..nw {
            let mut acc = [0u32; 4];
            let mut count = 0u32;
            for oy in 0..2u32 {
                let sy = (height - 1).min(y * 2 + oy);
                for ox in 0..2u32 {
                    let sx = (width - 1).min(x * 2 + ox);
                    let pixel = ((sy * width + sx) * 4) as usize;
                    for channel in 0..4 {
                        acc[channel] += rgba[pixel + channel] as u32;
                    }
                    count += 1;
                }
            }
            let dest = ((y * nw + x) * 4) as usize;
            let denom = count.max(1);
            for channel in 0..4 {
                out[dest + channel] = (acc[channel] / denom) as u8;
            }
        }
    }
    (nw, nh, out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hex(text: &str) -> Vec<u8> {
        (0..text.len())
            .step_by(2)
            .map(|at| u8::from_str_radix(&text[at..at + 2], 16).unwrap())
            .collect()
    }

    #[test]
    fn flat_colour_matches_the_python_block() {
        let rgba = [180u8, 40, 30, 255].repeat(16);
        assert_eq!(encode_bc7(4, 4, &rgba), hex("40ad9642793cfe7f0000000000000000"));
    }

    #[test]
    fn endpoint_and_index_swap_match_python() {
        assert_eq!(
            encode_bc7(4, 4, &[0, 1, 254, 255].repeat(16)),
            hex("40000000f8ffff7f0000000000000000")
        );
        let mut mix = vec![0u8, 0, 0, 255];
        mix.extend(std::iter::repeat(255u8).take(60));
        assert_eq!(encode_bc7(4, 4, &mix), hex("40c01ff007fcff7ff1ffffffffffffff"));
        assert_eq!(
            encode_bc7(2, 2, &[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]),
            hex("40802170081c04884144e9eee9eee9ee")
        );
    }

    #[test]
    fn gradient_chain_matches_python_levels() {
        let mut pixels = Vec::new();
        for y in 0..8u8 {
            for x in 0..8u8 {
                pixels.extend_from_slice(&[x * 30, y * 30, 40, 255]);
            }
        }
        let levels = bc7_mip_chain(8, 8, &pixels);
        assert_eq!(levels.len(), 4);
        assert_eq!(
            levels[0],
            hex("40400bd0a250fe7f207552a775daa7fd405e1ad0a250fe7f207552a775daa7fd40408b97a650fe7f207552a775daa7fd405e9a97a650fe7f207552a775daa7fd")
        );
        assert_eq!(levels[1], hex("c043f810a650feff217552a775daa7fd"));
        assert_eq!(levels[3].len(), 16);
    }
}
