//! Per-pixel loops from the house profile: box limit, one-step downsample,
//! the near-magenta scan, BC1 blocks, and ORM channel blits.
//!
//! Integer arithmetic matches the Python functions. No Python types, so the
//! same code builds for wasm32. PNG stays in Python: a fast deflate that
//! only run-length-encodes zeros made tiled maps several megabytes.

pub fn limit_rgba(width: i32, height: i32, rgba: &[u8], limit: i32) -> (i32, i32, Vec<u8>) {
    if width <= 0 || height <= 0 || limit <= 0 {
        return (width, height, rgba.to_vec());
    }
    let need = (width as usize).saturating_mul(height as usize).saturating_mul(4);
    if rgba.len() < need || (width <= limit && height <= limit) {
        return (width, height, rgba.to_vec());
    }
    let scale = (limit as f64) / (width.max(height) as f64);
    let nw = (((width as f64) * scale) as i32).max(1);
    let nh = (((height as f64) * scale) as i32).max(1);
    let mut out = vec![0u8; (nw as usize) * (nh as usize) * 4];
    for y in 0..nh {
        let y0 = ((y as f64) / scale) as i32;
        let y1 = height.min((y0 + 1).max((((y + 1) as f64) / scale) as i32));
        for x in 0..nw {
            let x0 = ((x as f64) / scale) as i32;
            let x1 = width.min((x0 + 1).max((((x + 1) as f64) / scale) as i32));
            let mut acc = [0u64; 4];
            let mut count = 0u64;
            let mut yy = y0;
            while yy < y1 {
                let row = (yy as usize) * (width as usize);
                let mut xx = x0;
                while xx < x1 {
                    let pixel = (row + xx as usize) * 4;
                    acc[0] += rgba[pixel] as u64;
                    acc[1] += rgba[pixel + 1] as u64;
                    acc[2] += rgba[pixel + 2] as u64;
                    acc[3] += rgba[pixel + 3] as u64;
                    count += 1;
                    xx += 1;
                }
                yy += 1;
            }
            if count == 0 {
                continue;
            }
            let dest = ((y as usize) * (nw as usize) + x as usize) * 4;
            for channel in 0..4 {
                out[dest + channel] = (acc[channel] / count) as u8;
            }
        }
    }
    (nw, nh, out)
}

pub fn downsample(width: i32, height: i32, rgba: &[u8]) -> (i32, i32, Vec<u8>) {
    if width <= 0 || height <= 0 {
        return (0, 0, Vec::new());
    }
    let need = (width as usize).saturating_mul(height as usize).saturating_mul(4);
    if rgba.len() < need {
        return (0, 0, Vec::new());
    }
    let nw = (width / 2).max(1);
    let nh = (height / 2).max(1);
    let mut out = vec![0u8; (nw as usize) * (nh as usize) * 4];
    for y in 0..nh {
        for x in 0..nw {
            let mut acc = [0u32; 4];
            let mut count = 0u32;
            for oy in 0..2i32 {
                let sy = (height - 1).min(y * 2 + oy);
                for ox in 0..2i32 {
                    let sx = (width - 1).min(x * 2 + ox);
                    let pixel = ((sy as usize) * (width as usize) + sx as usize) * 4;
                    for channel in 0..4 {
                        acc[channel] += rgba[pixel + channel] as u32;
                    }
                    count += 1;
                }
            }
            let dest = ((y as usize) * (nw as usize) + x as usize) * 4;
            let denom = count.max(1);
            for channel in 0..4 {
                out[dest + channel] = (acc[channel] / denom) as u8;
            }
        }
    }
    (nw, nh, out)
}

fn near_magenta(red: u8, green: u8, blue: u8, alpha: u8) -> bool {
    alpha >= 200 && red >= 190 && blue >= 190 && green <= 80 && (red as i32 - blue as i32).abs() <= 48
}

pub fn has_near_magenta(rgba: &[u8]) -> bool {
    let mut at = 0usize;
    while at + 3 < rgba.len() {
        if near_magenta(rgba[at], rgba[at + 1], rgba[at + 2], rgba[at + 3]) {
            return true;
        }
        at += 4;
    }
    false
}

pub fn encode_bc1(width: u32, height: u32, rgba: &[u8]) -> Vec<u8> {
    if width == 0 || height == 0 {
        return Vec::new();
    }
    let need = (width as usize).saturating_mul(height as usize).saturating_mul(4);
    if rgba.len() < need {
        return Vec::new();
    }
    let blocks_x = ((width + 3) / 4) as usize;
    let blocks_y = ((height + 3) / 4) as usize;
    let mut out = vec![0u8; blocks_x * blocks_y * 8];
    let mut at = 0usize;
    let mut y = 0u32;
    while y < height {
        let mut x = 0u32;
        while x < width {
            bc1_block(rgba, width, height, x, y, &mut out[at..at + 8]);
            at += 8;
            x += 4;
        }
        y += 4;
    }
    out
}

pub fn bc1_mip_chain(width: u32, height: u32, rgba: &[u8]) -> Vec<Vec<u8>> {
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
        let block = encode_bc1(w, h, &mip);
        if block.is_empty() {
            return Vec::new();
        }
        levels.push(block);
        if w == 1 && h == 1 {
            break;
        }
        let (nw, nh, next) = downsample(w as i32, h as i32, &mip);
        if nw <= 0 || nh <= 0 || next.is_empty() {
            return Vec::new();
        }
        w = nw as u32;
        h = nh as u32;
        mip = next;
    }
    levels
}

fn bc1_block(rgba: &[u8], width: u32, height: u32, x: u32, y: u32, out: &mut [u8]) {
    let mut pixels = [[0u8; 4]; 16];
    for row in 0..4u32 {
        let sy = (y + row).min(height - 1);
        for col in 0..4u32 {
            let sx = (x + col).min(width - 1);
            let src = ((sy * width + sx) * 4) as usize;
            pixels[(row * 4 + col) as usize].copy_from_slice(&rgba[src..src + 4]);
        }
    }
    let luma = |pixel: &[u8; 4]| pixel[0] as i32 * 2 + pixel[1] as i32 * 4 + pixel[2] as i32;
    let mut dark = 0usize;
    let mut bright = 0usize;
    let mut dark_l = luma(&pixels[0]);
    let mut bright_l = dark_l;
    for (index, pixel) in pixels.iter().enumerate().skip(1) {
        let value = luma(pixel);
        if value < dark_l {
            dark_l = value;
            dark = index;
        }
        if value > bright_l {
            bright_l = value;
            bright = index;
        }
    }
    let mut color0 = to565(&pixels[bright]);
    let mut color1 = to565(&pixels[dark]);
    if color0 <= color1 {
        if color0 < 0xFFFF {
            color0 += 1;
        } else {
            color1 -= 1;
        }
    }
    let ends = [from565(color0), from565(color1)];
    let palette = [
        ends[0],
        ends[1],
        [
            (2 * ends[0][0] + ends[1][0]) / 3,
            (2 * ends[0][1] + ends[1][1]) / 3,
            (2 * ends[0][2] + ends[1][2]) / 3,
        ],
        [
            (ends[0][0] + 2 * ends[1][0]) / 3,
            (ends[0][1] + 2 * ends[1][1]) / 3,
            (ends[0][2] + 2 * ends[1][2]) / 3,
        ],
    ];
    let mut indices = 0u32;
    for (pixel_index, pixel) in pixels.iter().enumerate() {
        let mut best = 0u32;
        let mut best_error = 1i32 << 30;
        for (choice, colour) in palette.iter().enumerate() {
            let mut error = 0i32;
            for channel in 0..3 {
                let delta = pixel[channel] as i32 - colour[channel];
                error += delta * delta;
            }
            if error < best_error {
                best_error = error;
                best = choice as u32;
            }
        }
        indices |= best << (pixel_index * 2);
    }
    out[0..2].copy_from_slice(&color0.to_le_bytes());
    out[2..4].copy_from_slice(&color1.to_le_bytes());
    out[4..8].copy_from_slice(&indices.to_le_bytes());
}

fn to565(pixel: &[u8; 4]) -> u16 {
    (((pixel[0] as u16) >> 3) << 11) | (((pixel[1] as u16) >> 2) << 5) | ((pixel[2] as u16) >> 3)
}

fn from565(value: u16) -> [i32; 3] {
    let red = ((value >> 11) & 31) as i32;
    let green = ((value >> 5) & 63) as i32;
    let blue = (value & 31) as i32;
    [(red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2)]
}

pub fn blit_channel(
    out: &mut [u8],
    width: i32,
    height: i32,
    src: &[u8],
    src_w: i32,
    src_h: i32,
    channel: i32,
    dest: i32,
) {
    if width <= 0 || height <= 0 || src_w <= 0 || src_h <= 0 || !(0..4).contains(&channel) || !(0..4).contains(&dest)
    {
        return;
    }
    let need = (width as usize).saturating_mul(height as usize).saturating_mul(4);
    if out.len() < need {
        return;
    }
    for y in 0..height {
        let sy = (((y as i64) * (src_h as i64)) / (height as i64)).min(src_h as i64 - 1) as i32;
        for x in 0..width {
            let sx = (((x as i64) * (src_w as i64)) / (width as i64)).min(src_w as i64 - 1) as i32;
            let at = ((sy as usize) * (src_w as usize) + sx as usize) * 4 + channel as usize;
            if at < src.len() {
                out[((y as usize) * (width as usize) + x as usize) * 4 + dest as usize] = src[at];
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limit_of_a_flat_image_keeps_the_colour_and_the_long_edge() {
        let rgba = [10u8, 140, 40, 255].repeat(8 * 4);
        let (width, height, out) = limit_rgba(8, 4, &rgba, 2);
        assert_eq!((width, height), (2, 1));
        assert_eq!(out, [10, 140, 40, 255, 10, 140, 40, 255]);
    }

    #[test]
    fn magenta_scan_stops_on_the_first_dash_and_rejects_a_clean_flat() {
        assert!(!has_near_magenta(&[20, 30, 40, 255, 1, 2, 3, 255]));
        let mut rgba = vec![20u8, 30, 40, 255];
        rgba.extend_from_slice(&[200, 10, 210, 255]);
        assert!(has_near_magenta(&rgba));
        assert!(has_near_magenta(&[255, 0, 255, 255]));
        assert!(!has_near_magenta(&[255, 200, 255, 255]));
    }

    #[test]
    fn bc1_flat_block_forces_opaque_endpoints() {
        let rgba = [40u8, 90, 20, 255].repeat(16);
        let block = encode_bc1(4, 4, &rgba);
        assert_eq!(block.len(), 8);
        let color0 = u16::from_le_bytes([block[0], block[1]]);
        let color1 = u16::from_le_bytes([block[2], block[3]]);
        assert!(color0 > color1);
    }

}
