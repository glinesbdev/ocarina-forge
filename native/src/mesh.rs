//! Face normals for a triangle list.
//!
//! The accumulation matches `ocarina_forge/formats/gltf.py`: f64 cross
//! products, summed in triangle order, then a unit vector. A vertex whose
//! length is under 1e-8 becomes (0, 1, 0). An index past the vertex list is
//! skipped. No Python types, so this builds for wasm32.

/// Little-endian f32 normals, one xyz per vertex. Same values as
/// `face_normals`, then the f32 cast `to_glb` uses.
pub fn face_normal_bytes(positions: &[f64], indices: &[u32]) -> Vec<u8> {
    let normals = face_normals(positions, indices);
    let mut out = Vec::with_capacity(normals.len() * 4);
    for value in normals {
        out.extend_from_slice(&(value as f32).to_le_bytes());
    }
    out
}

/// One normal per vertex, xyz f64, in the same order as `positions`.
pub fn face_normals(positions: &[f64], indices: &[u32]) -> Vec<f64> {
    let count = positions.len() / 3;
    let mut acc = vec![0.0f64; count * 3];
    let mut index = 0usize;
    while index + 2 < indices.len() {
        let a = indices[index] as usize;
        let b = indices[index + 1] as usize;
        let c = indices[index + 2] as usize;
        index += 3;
        if a >= count || b >= count || c >= count {
            continue;
        }
        let (ax, ay, az) = at(positions, a);
        let (bx, by, bz) = at(positions, b);
        let (cx, cy, cz) = at(positions, c);
        let ux = bx - ax;
        let uy = by - ay;
        let uz = bz - az;
        let vx = cx - ax;
        let vy = cy - ay;
        let vz = cz - az;
        let nx = uy * vz - uz * vy;
        let ny = uz * vx - ux * vz;
        let nz = ux * vy - uy * vx;
        for vertex in [a, b, c] {
            let at = vertex * 3;
            acc[at] += nx;
            acc[at + 1] += ny;
            acc[at + 2] += nz;
        }
    }
    let mut out = Vec::with_capacity(acc.len());
    for vertex in acc.chunks(3) {
        let length = (vertex[0] * vertex[0] + vertex[1] * vertex[1] + vertex[2] * vertex[2]).sqrt();
        if length < 1e-8 {
            out.extend([0.0, 1.0, 0.0]);
        } else {
            out.extend([vertex[0] / length, vertex[1] / length, vertex[2] / length]);
        }
    }
    out
}

fn at(positions: &[f64], index: usize) -> (f64, f64, f64) {
    let base = index * 3;
    (positions[base], positions[base + 1], positions[base + 2])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_flat_triangle_points_up() {
        let positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0];
        let normals = face_normals(&positions, &[0, 1, 2]);
        assert_eq!(normals.len(), 9);
        for chunk in normals.chunks(3) {
            assert!((chunk[1] + 1.0).abs() < 1e-9);
            assert!(chunk[0].abs() < 1e-9 && chunk[2].abs() < 1e-9);
        }
    }
}
