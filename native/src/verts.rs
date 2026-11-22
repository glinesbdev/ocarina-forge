//! Bind-pose transforms, UV seam splits, and bounds on packed f64 buffers.
//!
//! Vertex data stays little-endian f64 bytes. Callers do not build a Python
//! tuple per vertex. No Python types, so the same code builds for wasm32.

/// Row-major 4×4 worlds, one matrix per bone. An empty `slots` buffer means
/// every vertex uses `bone_index` and the rigid flag is ignored. Otherwise
/// `slots` is one palette index per vertex.
pub fn transform_positions(
    positions: &[f64],
    worlds: &[f64],
    bone_index: i32,
    slots: &[i32],
    palette: &[i32],
    rigid: &[i32],
) -> Vec<f64> {
    let count = positions.len() / 3;
    let bones = worlds.len() / 16;
    let mut out = Vec::with_capacity(count * 3);
    for vertex in 0..count {
        let slot = if slots.is_empty() {
            None
        } else {
            Some(*slots.get(vertex).unwrap_or(&0))
        };
        let x = positions[vertex * 3];
        let y = positions[vertex * 3 + 1];
        let z = positions[vertex * 3 + 2];
        match matrix_for(slot, bone_index, bones, palette, rigid, worlds) {
            Some(m) => {
                out.push(m[0] * x + m[1] * y + m[2] * z + m[3]);
                out.push(m[4] * x + m[5] * y + m[6] * z + m[7]);
                out.push(m[8] * x + m[9] * y + m[10] * z + m[11]);
            }
            None => {
                out.push(x);
                out.push(y);
                out.push(z);
            }
        }
    }
    out
}

fn matrix_for<'a>(
    slot: Option<i32>,
    bone_index: i32,
    bones: usize,
    palette: &[i32],
    rigid: &[i32],
    worlds: &'a [f64],
) -> Option<&'a [f64]> {
    let mut index = bone_index;
    if let Some(slot) = slot {
        if !palette.is_empty() && slot >= 0 && (slot as usize) < palette.len() {
            index = palette[slot as usize];
        } else if slot >= 0 && (slot as usize) < bones {
            index = slot;
        }
    }
    if index < 0 || index as usize >= bones {
        return None;
    }
    if slot.is_some() {
        let flag = rigid.get(index as usize).copied().unwrap_or(0);
        if flag < 0 {
            return None;
        }
    }
    let start = index as usize * 16;
    Some(&worlds[start..start + 16])
}

/// Min and max of each component. Non-finite values are skipped, matching
/// the attribute bounds. An empty buffer returns None.
pub fn vec_bounds(values: &[f64], width: usize) -> Option<(Vec<f64>, Vec<f64>)> {
    if width == 0 || values.len() < width || values.len() % width != 0 {
        return None;
    }
    let mut low = vec![f64::INFINITY; width];
    let mut high = vec![f64::NEG_INFINITY; width];
    let mut any = false;
    for chunk in values.chunks(width) {
        if chunk.iter().any(|value| !value.is_finite()) {
            continue;
        }
        any = true;
        for (index, value) in chunk.iter().enumerate() {
            low[index] = low[index].min(*value);
            high[index] = high[index].max(*value);
        }
    }
    if !any {
        return None;
    }
    Some((low, high))
}

pub fn pack_f32_from_f64(values: &[f64]) -> Vec<u8> {
    let mut out = Vec::with_capacity(values.len() * 4);
    for value in values {
        out.extend_from_slice(&(*value as f32).to_le_bytes());
    }
    out
}

/// A stable key for “is this the same mesh”. Positions are rounded to 4
/// decimals and each triangle is sorted, then the triangles are sorted.
pub fn shape_key(positions: &[f64], indices: &[u32]) -> Vec<u8> {
    let mut tris = Vec::new();
    let mut cursor = 0usize;
    while cursor + 2 < indices.len() {
        let mut tri = [indices[cursor], indices[cursor + 1], indices[cursor + 2]];
        tri.sort_unstable();
        tris.push(tri);
        cursor += 3;
    }
    tris.sort_unstable();
    let mut out = Vec::with_capacity(positions.len() * 4 + tris.len() * 12);
    for value in positions {
        let quantized = (value * 10000.0).round() as i32;
        out.extend_from_slice(&quantized.to_le_bytes());
    }
    out.extend_from_slice(&(tris.len() as u32).to_le_bytes());
    for tri in tris {
        for index in tri {
            out.extend_from_slice(&index.to_le_bytes());
        }
    }
    out
}

/// Split a triangle whose UVs jump across a unit-chart cut.
///
/// Same rules as `_close_uv_seams`: a tiled axis (outside -0.25..1.25) is
/// left alone, a wrap has to land within 0.15 of an integer, and the split
/// copies the vertex so the other triangles keep the original UV.
pub fn close_uv_seams(
    positions: &[f64],
    uvs: &[f64],
    indices: &[u32],
    extras: &[Vec<f64>],
    widths: &[usize],
) -> Option<(Vec<f64>, Vec<f64>, Vec<u32>, Vec<Vec<f64>>)> {
    let verts = positions.len() / 3;
    if verts == 0 || uvs.len() / 2 != verts || indices.len() < 3 || positions.len() % 3 != 0 || uvs.len() % 2 != 0 {
        return None;
    }
    let mut pos = positions.to_vec();
    let mut uv = uvs.to_vec();
    let mut idx = indices.to_vec();
    let mut carried = extras.to_vec();
    let mut tiled = [false, false];
    for axis in 0..2 {
        let mut low = f64::INFINITY;
        let mut high = f64::NEG_INFINITY;
        for vertex in 0..verts {
            let value = uv[vertex * 2 + axis];
            low = low.min(value);
            high = high.max(value);
        }
        tiled[axis] = low < -0.25 || high > 1.25;
    }
    if tiled[0] && tiled[1] {
        return Some((pos, uv, idx, carried));
    }
    let mut tri = 0usize;
    while tri + 2 < idx.len() {
        for axis in 0..2 {
            if tiled[axis] {
                continue;
            }
            let mut ids = [idx[tri], idx[tri + 1], idx[tri + 2]];
            if ids.iter().any(|id| *id as usize >= uv.len() / 2) {
                return None;
            }
            let mut vals = [0.0f64; 3];
            for slot in 0..3 {
                vals[slot] = uv[ids[slot] as usize * 2 + axis];
            }
            let span = vals.iter().copied().fold(f64::NEG_INFINITY, f64::max)
                - vals.iter().copied().fold(f64::INFINITY, f64::min);
            if span <= 0.5 {
                continue;
            }
            for slot in 0..3 {
                let mut others = [0.0f64; 2];
                let mut n = 0usize;
                for (index, value) in vals.iter().copied().enumerate() {
                    if index != slot {
                        others[n] = value;
                        n += 1;
                    }
                }
                if (others[0] - others[1]).abs() > 0.25 {
                    continue;
                }
                let body = (others[0] + others[1]) * 0.5;
                let value = vals[slot];
                let delta = value - body;
                let steps = delta.round();
                if steps == 0.0 || (delta - steps).abs() > 0.15 {
                    continue;
                }
                let shifted = value - steps;
                if (shifted - value).abs() <= 1e-6 {
                    continue;
                }
                let mut moved = vals;
                moved[slot] = shifted;
                let moved_span = moved.iter().copied().fold(f64::NEG_INFINITY, f64::max)
                    - moved.iter().copied().fold(f64::INFINITY, f64::min);
                if moved_span < 0.02 {
                    continue;
                }
                let vertex = ids[slot] as usize;
                if vertex >= pos.len() / 3 {
                    return None;
                }
                let copy = [pos[vertex * 3], pos[vertex * 3 + 1], pos[vertex * 3 + 2]];
                pos.extend_from_slice(&copy);
                for (extra, width) in carried.iter_mut().zip(widths.iter().copied()) {
                    if width == 0 || extra.len() % width != 0 {
                        continue;
                    }
                    if vertex < extra.len() / width {
                        let start = vertex * width;
                        let owned = extra[start..start + width].to_vec();
                        extra.extend_from_slice(&owned);
                    }
                }
                let mut pair = [uv[vertex * 2], uv[vertex * 2 + 1]];
                pair[axis] = shifted;
                uv.extend_from_slice(&pair);
                let new_index = (pos.len() / 3 - 1) as u32;
                idx[tri + slot] = new_index;
                ids[slot] = new_index;
                vals[slot] = shifted;
            }
        }
        tri += 3;
    }
    Some((pos, uv, idx, carried))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_translation_moves_every_vertex() {
        let positions = [1.0, 2.0, 3.0, 0.0, 0.0, 0.0];
        let mut worlds = [0.0; 16];
        worlds[0] = 1.0;
        worlds[5] = 1.0;
        worlds[10] = 1.0;
        worlds[15] = 1.0;
        worlds[3] = 4.0;
        worlds[7] = -1.0;
        let out = transform_positions(&positions, &worlds, 0, &[], &[], &[0]);
        assert_eq!(out, vec![5.0, 1.0, 3.0, 4.0, -1.0, 0.0]);
    }

    #[test]
    fn a_non_rigid_slot_stays_put() {
        let positions = [1.0, 0.0, 0.0];
        let mut worlds = [0.0; 32];
        for matrix in 0..2 {
            worlds[matrix * 16] = 1.0;
            worlds[matrix * 16 + 5] = 1.0;
            worlds[matrix * 16 + 10] = 1.0;
            worlds[matrix * 16 + 15] = 1.0;
        }
        worlds[3] = 9.0;
        let out = transform_positions(&positions, &worlds, 0, &[0], &[1], &[0, -1]);
        assert_eq!(out, vec![1.0, 0.0, 0.0]);
    }

    #[test]
    fn a_trunk_seam_moves_the_wrapped_corner() {
        let positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
        let uvs = [0.02, 0.2, 0.98, 0.2, 0.04, 0.8];
        let (pos, uv, idx, _) = close_uv_seams(&positions, &uvs, &[0, 1, 2], &[], &[]).unwrap();
        assert_ne!(idx[1], 1);
        let corner = idx[1] as usize;
        assert!(uv[corner * 2] < 0.0);
        assert!((uv[1 * 2] - 0.98).abs() < 1e-9);
        assert_eq!(pos.len() / 3, 4);
    }

    #[test]
    fn a_tiled_chart_is_not_folded() {
        let positions = [0.0, 0.0, 0.0, 4.83, 0.0, 0.0, 0.0, 0.0, 1.2];
        let uvs = [1.354, -1.45, 2.570, -1.40, 1.400, -1.20];
        let (_pos, uv, idx, _) = close_uv_seams(&positions, &uvs, &[0, 1, 2], &[], &[]).unwrap();
        assert_eq!(idx, vec![0, 1, 2]);
        assert_eq!(uv[2], 2.570);
    }
}
