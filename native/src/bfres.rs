//! BFRES vertex attributes and triangle indices.
//!
//! The float values match `ocarina_forge/formats/bfres.py`: IEEE half to f64,
//! unorm and snorm divisors, and the 10-bit packed normals. A short buffer
//! or an unknown format returns nothing so Python keeps its own loop.
//! No Python types, so this builds for wasm32.

const F32: u32 = 0x0516;
const F32_2: u32 = 0x0517;
const F32_3: u32 = 0x0518;
const F32_4: u32 = 0x0519;
const F16: u32 = 0x050A;
const F16_2: u32 = 0x0512;
const F16_4: u32 = 0x0515;
const U8: u32 = 0x0102;
const U8_2: u32 = 0x0109;
const U8_4: u32 = 0x010B;
const S8_4: u32 = 0x020B;
const U16_2: u32 = 0x0112;
const S16_2: u32 = 0x0212;
const SN10: u32 = 0x020E;
const UN10: u32 = 0x000B;

fn components(format: u32) -> Option<usize> {
    Some(match format {
        F32 | F16 | U8 => 1,
        F32_2 | F16_2 | U8_2 | U16_2 | S16_2 => 2,
        F32_3 | SN10 | UN10 => 3,
        F32_4 | F16_4 | U8_4 | S8_4 => 4,
        _ => return None,
    })
}

fn format_size(format: u32) -> Option<usize> {
    Some(match format {
        U8 => 1,
        F16 | U8_2 => 2,
        F32 | U8_4 | S8_4 | U16_2 | S16_2 | F16_2 | SN10 | UN10 => 4,
        F16_4 | F32_2 => 8,
        F32_3 => 12,
        F32_4 => 16,
        _ => return None,
    })
}

/// Little-endian f64, `count * components` values. Empty when the whole
/// attribute does not fit.
pub fn decode_attribute(buffer: &[u8], count: u32, stride: u32, offset: u32, format: u32) -> Vec<u8> {
    let Some(values) = attribute_values(buffer, count, stride, offset, format) else {
        return Vec::new();
    };
    let mut out = Vec::with_capacity(values.len() * 8);
    for value in values {
        out.extend_from_slice(&value.to_le_bytes());
    }
    out
}

/// Finite vertices only. A non-finite component drops that whole vertex, the
/// same as the Python min/max scan. `(count, min bytes, max bytes)`.
pub fn attribute_bounds(buffer: &[u8], count: u32, stride: u32, offset: u32, format: u32) -> Option<(u32, Vec<u8>, Vec<u8>)> {
    let values = attribute_values(buffer, count, stride, offset, format)?;
    let width = components(format)?;
    if values.len() != count as usize * width {
        return None;
    }
    let mut low = vec![f64::INFINITY; width];
    let mut high = vec![f64::NEG_INFINITY; width];
    let mut finite = 0u32;
    for vertex in values.chunks(width) {
        if vertex.iter().any(|value| !value.is_finite()) {
            continue;
        }
        for (slot, value) in vertex.iter().enumerate() {
            low[slot] = low[slot].min(*value);
            high[slot] = high[slot].max(*value);
        }
        finite += 1;
    }
    if finite == 0 {
        return Some((0, Vec::new(), Vec::new()));
    }
    Some((finite, f64s_to_bytes(&low), f64s_to_bytes(&high)))
}

/// Triangle list as little-endian u32. `pairs` is `(byte offset, index count)`
/// repeated. Primitive 3 is a list, 4 is a strip with the same winding flip
/// as Python. Empty when the primitive is unknown or an index does not fit.
pub fn decode_triangles(blob: &[u8], width: u32, pairs: &[u32], first: u32, primitive: u32) -> Vec<u8> {
    if width != 1 && width != 2 && width != 4 {
        return Vec::new();
    }
    if primitive != 3 && primitive != 4 {
        return Vec::new();
    }
    let mut indices = Vec::new();
    let mut pair = 0usize;
    while pair + 1 < pairs.len() {
        let offset = pairs[pair] as usize;
        let count = pairs[pair + 1] as usize;
        pair += 2;
        for item in 0..count {
            let pos = offset + item * width as usize;
            if pos + width as usize > blob.len() {
                break;
            }
            let value = match width {
                1 => blob[pos] as u64,
                2 => u16::from_le_bytes([blob[pos], blob[pos + 1]]) as u64,
                _ => u32::from_le_bytes([blob[pos], blob[pos + 1], blob[pos + 2], blob[pos + 3]]) as u64,
            };
            let sum = value + first as u64;
            if sum > u32::MAX as u64 {
                return Vec::new();
            }
            indices.push(sum as u32);
        }
    }
    let triangles = if primitive == 4 {
        strip(indices)
    } else {
        let end = indices.len() - (indices.len() % 3);
        indices[..end].to_vec()
    };
    let mut out = Vec::with_capacity(triangles.len() * 4);
    for index in triangles {
        out.extend_from_slice(&index.to_le_bytes());
    }
    out
}

fn strip(indices: Vec<u32>) -> Vec<u32> {
    let mut out = Vec::new();
    if indices.len() < 3 {
        return out;
    }
    for index in 0..indices.len() - 2 {
        let a = indices[index];
        let b = indices[index + 1];
        let c = indices[index + 2];
        if a == b || b == c || a == c {
            continue;
        }
        if index % 2 == 0 {
            out.extend([a, b, c]);
        } else {
            out.extend([a, c, b]);
        }
    }
    out
}

pub fn attribute_values(buffer: &[u8], count: u32, stride: u32, offset: u32, format: u32) -> Option<Vec<f64>> {
    let width = components(format)?;
    let size = format_size(format)?;
    let step = if stride == 0 { size as u32 } else { stride };
    if count == 0 {
        return Some(Vec::new());
    }
    let mut out = Vec::with_capacity(count as usize * width);
    for index in 0..count {
        let at = (index as u64) * (step as u64) + (offset as u64);
        if at > usize::MAX as u64 {
            return None;
        }
        let at = at as usize;
        if at + size > buffer.len() {
            return None;
        }
        push_vertex(buffer, at, format, &mut out)?;
    }
    Some(out)
}

fn push_vertex(buffer: &[u8], at: usize, format: u32, out: &mut Vec<f64>) -> Option<()> {
    match format {
        F32 => out.push(f32_at(buffer, at) as f64),
        F32_2 => {
            out.push(f32_at(buffer, at) as f64);
            out.push(f32_at(buffer, at + 4) as f64);
        }
        F32_3 => {
            out.push(f32_at(buffer, at) as f64);
            out.push(f32_at(buffer, at + 4) as f64);
            out.push(f32_at(buffer, at + 8) as f64);
        }
        F32_4 => {
            for slot in 0..4 {
                out.push(f32_at(buffer, at + slot * 4) as f64);
            }
        }
        F16 => out.push(f16_to_f64(u16::from_le_bytes([buffer[at], buffer[at + 1]]))),
        F16_2 => {
            for slot in 0..2 {
                let pos = at + slot * 2;
                out.push(f16_to_f64(u16::from_le_bytes([buffer[pos], buffer[pos + 1]])));
            }
        }
        F16_4 => {
            for slot in 0..4 {
                let pos = at + slot * 2;
                out.push(f16_to_f64(u16::from_le_bytes([buffer[pos], buffer[pos + 1]])));
            }
        }
        U8 => out.push(buffer[at] as f64 / 255.0),
        U8_2 => {
            out.push(buffer[at] as f64 / 255.0);
            out.push(buffer[at + 1] as f64 / 255.0);
        }
        U8_4 => {
            for byte in &buffer[at..at + 4] {
                out.push(*byte as f64 / 255.0);
            }
        }
        S8_4 => {
            for byte in &buffer[at..at + 4] {
                out.push(*byte as i8 as f64 / 127.0);
            }
        }
        U16_2 => {
            let a = u16::from_le_bytes([buffer[at], buffer[at + 1]]);
            let b = u16::from_le_bytes([buffer[at + 2], buffer[at + 3]]);
            out.push(a as f64 / 65535.0);
            out.push(b as f64 / 65535.0);
        }
        S16_2 => {
            let a = i16::from_le_bytes([buffer[at], buffer[at + 1]]);
            let b = i16::from_le_bytes([buffer[at + 2], buffer[at + 3]]);
            out.push(a as f64 / 32767.0);
            out.push(b as f64 / 32767.0);
        }
        SN10 | UN10 => {
            let packed = u32::from_le_bytes([buffer[at], buffer[at + 1], buffer[at + 2], buffer[at + 3]]);
            for shift in [0, 10, 20] {
                let mut raw = ((packed >> shift) & 0x3FF) as i32;
                if format == SN10 && raw & 0x200 != 0 {
                    raw -= 0x400;
                }
                let scale = if format == SN10 { 511.0 } else { 1023.0 };
                out.push(raw as f64 / scale);
            }
        }
        _ => return None,
    }
    Some(())
}

fn f32_at(buffer: &[u8], at: usize) -> f32 {
    f32::from_le_bytes([buffer[at], buffer[at + 1], buffer[at + 2], buffer[at + 3]])
}

fn f16_to_f64(bits: u16) -> f64 {
    let sign = ((bits as u64) >> 15) & 1;
    let exp = ((bits >> 10) & 0x1F) as i32;
    let frac = (bits & 0x3FF) as u64;
    let decoded = if exp == 0 {
        if frac == 0 {
            sign << 63
        } else {
            let mut magnitude = frac;
            let mut exponent = -14i32;
            while magnitude & 0x400 == 0 {
                magnitude <<= 1;
                exponent -= 1;
            }
            magnitude &= 0x3FF;
            let exp64 = (exponent + 1023) as u64;
            (sign << 63) | (exp64 << 52) | (magnitude << 42)
        }
    } else if exp == 31 {
        (sign << 63) | (0x7FFu64 << 52) | (frac << 42)
    } else {
        let exp64 = (exp - 15 + 1023) as u64;
        (sign << 63) | (exp64 << 52) | (frac << 42)
    };
    f64::from_bits(decoded)
}

/// Little-endian f32. `rows` is already f64 components, `width` of them per vertex.
pub fn pack_f32(values: &[f64], width: usize) -> Vec<u8> {
    if width == 0 || values.len() % width != 0 {
        return Vec::new();
    }
    let mut out = Vec::with_capacity(values.len() * 4);
    for value in values {
        out.extend_from_slice(&(*value as f32).to_le_bytes());
    }
    out
}

fn f64s_to_bytes(values: &[f64]) -> Vec<u8> {
    let mut out = Vec::with_capacity(values.len() * 8);
    for value in values {
        out.extend_from_slice(&value.to_le_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn half_one_and_a_subnormal_match_ieee() {
        assert_eq!(f16_to_f64(0x3C00), 1.0);
        assert_eq!(f16_to_f64(0xC000), -2.0);
        assert!(f16_to_f64(0x0001) > 0.0);
        assert!(f16_to_f64(0x0001) < 1e-6);
        assert!(f16_to_f64(0x7C00).is_infinite());
    }

    #[test]
    fn a_strip_flips_every_other_triangle() {
        let blob = [0u8, 1, 2, 3, 3];
        let out = decode_triangles(&blob, 1, &[0, 5], 0, 4);
        let indices: Vec<u32> = out.chunks(4).map(|item| u32::from_le_bytes(item.try_into().unwrap())).collect();
        assert_eq!(indices, vec![0, 1, 2, 1, 3, 2]);
    }
}
