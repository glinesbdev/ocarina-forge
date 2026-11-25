//! Walkable-surface sampling, matching `ocarina_forge.world_surface`.
//!
//! Height tiles miss the Land_* island, cliff, soil, and hill meshes. This
//! index holds those world-space triangles on a uniform XZ grid and returns
//! the highest surface under a point. Snap, height-diff, and the wasm bench
//! all use this path. Bytes in, floats out, no Python types, so it builds
//! for wasm32.

use std::collections::HashMap;

const EPS: f64 = 1e-12;
const BARY: f64 = 1e-7;

/// World Y of a triangle if `(x, z)` is inside its XZ projection, else `None`.
pub fn triangle_height(x: f64, z: f64, triangle: &[f64]) -> Option<f64> {
    if triangle.len() < 9 {
        return None;
    }
    let ax = triangle[0];
    let ay = triangle[1];
    let az = triangle[2];
    let bx = triangle[3];
    let by = triangle[4];
    let bz = triangle[5];
    let cx = triangle[6];
    let cy = triangle[7];
    let cz = triangle[8];
    let det = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz);
    if det.abs() < EPS {
        return None;
    }
    let u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / det;
    let v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / det;
    let w = 1.0 - u - v;
    if u < -BARY || v < -BARY || w < -BARY {
        return None;
    }
    Some(u * ay + v * by + w * cy)
}

/// Highest real surface: a finite heightmap sample or a mesh hit, not sentinel 0.
pub fn compose_height(heightmap_y: Option<f64>, mesh_y: Option<f64>) -> (Option<f64>, Option<&'static str>) {
    let tile_y = heightmap_y.filter(|y| y.is_finite() && *y != 0.0);
    let mesh_y = mesh_y.filter(|y| y.is_finite());
    match (tile_y, mesh_y) {
        (None, None) => (None, None),
        (None, Some(y)) => (Some(y), Some("mesh")),
        (Some(y), None) => (Some(y), Some("heightmap")),
        (Some(tile), Some(mesh)) if tile > mesh => (Some(tile), Some("heightmap")),
        (Some(_), Some(mesh)) => (Some(mesh), Some("mesh")),
    }
}

/// Uniform XZ grid of world-space triangles.
pub struct MeshIndex {
    step: f64,
    triangles: Vec<f64>,
    cells: HashMap<(i32, i32), Vec<u32>>,
}

impl MeshIndex {
    pub fn new(step: f64) -> Self {
        Self {
            step: if step == 0.0 { 32.0 } else { step },
            triangles: Vec::new(),
            cells: HashMap::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.triangles.len() / 9
    }

    pub fn is_empty(&self) -> bool {
        self.triangles.is_empty()
    }

    pub fn from_triangles(step: f64, data: &[f64]) -> Self {
        let mut index = Self::new(step);
        let mut i = 0;
        while i + 8 < data.len() {
            index.add(&data[i..i + 9]);
            i += 9;
        }
        index
    }

    pub fn add(&mut self, triangle: &[f64]) {
        if triangle.len() < 9 {
            return;
        }
        let id = (self.triangles.len() / 9) as u32;
        self.triangles.extend_from_slice(&triangle[..9]);
        let xs = [triangle[0], triangle[3], triangle[6]];
        let zs = [triangle[2], triangle[5], triangle[8]];
        let x0 = min3(xs[0], xs[1], xs[2]);
        let x1 = max3(xs[0], xs[1], xs[2]);
        let z0 = min3(zs[0], zs[1], zs[2]);
        let z1 = max3(zs[0], zs[1], zs[2]);
        let step = self.step;
        let ix0 = (x0 / step).floor() as i32;
        let ix1 = (x1 / step).floor() as i32;
        let iz0 = (z0 / step).floor() as i32;
        let iz1 = (z1 / step).floor() as i32;
        for ix in ix0..=ix1 {
            for iz in iz0..=iz1 {
                self.cells.entry((ix, iz)).or_default().push(id);
            }
        }
    }

    pub fn sample(&self, x: f64, z: f64) -> Option<f64> {
        let step = self.step;
        let key = ((x / step).floor() as i32, (z / step).floor() as i32);
        let mut best = None;
        let Some(ids) = self.cells.get(&key) else {
            return None;
        };
        for &id in ids {
            let start = id as usize * 9;
            if let Some(y) = triangle_height(x, z, &self.triangles[start..start + 9]) {
                if best.map(|prev| y > prev).unwrap_or(true) {
                    best = Some(y);
                }
            }
        }
        best
    }

    /// Sample many XZ pairs (`points` length is `2 * N`). Writes NaN where there is no hit.
    pub fn sample_many(&self, points: &[f64], out: &mut [f64]) {
        let n = (points.len() / 2).min(out.len());
        for i in 0..n {
            out[i] = self.sample(points[i * 2], points[i * 2 + 1]).unwrap_or(f64::NAN);
        }
    }
}

fn min3(a: f64, b: f64, c: f64) -> f64 {
    a.min(b).min(c)
}

fn max3(a: f64, b: f64, c: f64) -> f64 {
    a.max(b).max(c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn triangle_height_is_barycentric_in_xz() {
        let flat = [0.0, 10.0, 0.0, 4.0, 10.0, 0.0, 0.0, 14.0, 4.0];
        assert!((triangle_height(1.0, 1.0, &flat).unwrap() - 11.0).abs() < 1e-9);
        assert!(triangle_height(-1.0, 0.0, &flat).is_none());
        let sloped = [0.0, 0.0, 0.0, 2.0, 4.0, 0.0, 0.0, 0.0, 2.0];
        assert!((triangle_height(1.0, 0.0, &sloped).unwrap() - 2.0).abs() < 1e-9);
    }

    #[test]
    fn compose_height_takes_the_highest_real_surface() {
        let (y, source) = compose_height(Some(102.6), Some(131.35));
        assert!((y.unwrap() - 131.35).abs() < 1e-9 && source == Some("mesh"));
        let (y, source) = compose_height(Some(140.0), Some(131.35));
        assert!((y.unwrap() - 140.0).abs() < 1e-9 && source == Some("heightmap"));
        let (y, source) = compose_height(Some(0.0), Some(131.35));
        assert!((y.unwrap() - 131.35).abs() < 1e-9 && source == Some("mesh"));
        let (y, source) = compose_height(Some(0.0), None);
        assert!(y.is_none() && source.is_none());
        let (y, _) = compose_height(Some(f64::NAN), None);
        assert!(y.is_none());
    }

    #[test]
    fn mesh_index_returns_the_highest_triangle() {
        let mut index = MeshIndex::new(8.0);
        index.add(&[0.0, 10.0, 0.0, 4.0, 10.0, 0.0, 0.0, 10.0, 4.0]);
        index.add(&[0.0, 13.0, 0.0, 2.0, 13.0, 0.0, 0.0, 13.0, 2.0]);
        assert!((index.sample(0.5, 0.5).unwrap() - 13.0).abs() < 1e-9);
        assert!(index.sample(3.5, 3.5).is_none());
        let mut out = [0.0; 2];
        index.sample_many(&[0.5, 0.5, 3.5, 3.5], &mut out);
        assert!((out[0] - 13.0).abs() < 1e-9);
        assert!(out[1].is_nan());
    }
}
