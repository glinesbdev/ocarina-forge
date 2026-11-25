//! Packed walkable surface for the viewer: Land_* meshes plus heightmap tiles.
//!
//! The binary layout is documented in `ocarina_forge/surface_pack.py`. This
//! module reads that buffer, builds a grid index, and samples the composed
//! height (finest finite height tile, then the highest Land_* mesh, not
//! sentinel 0). A 64-bit actor hash can be excluded so a dragged Land_
//! actor is not used as its own floor.

#![allow(dead_code)]

use crate::surface::{compose_height, MeshIndex};

pub const MAGIC: &[u8; 4] = b"OFGS";
pub const VERSION: u16 = 1;
pub const HEIGHT_SIZE: usize = 257;
pub const HEIGHT_COUNT: usize = HEIGHT_SIZE * HEIGHT_SIZE;

const HEADER: usize = 56;

#[derive(Debug)]
pub struct PackError(pub String);

impl std::fmt::Display for PackError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

pub struct HeightTile {
    pub layer: u8,
    pub name: String,
    pub center_x: f32,
    pub center_z: f32,
    pub edge: f32,
    pub heights: Vec<f32>,
}

pub struct GroundSurface {
    pub step: f32,
    pub origin: [f32; 3],
    pub scale: f32,
    pub height_scale: f32,
    pub vertex_count: u32,
    pub triangle_count: u32,
    pub actor_hashes: Vec<u64>,
    pub tiles: Vec<HeightTile>,
    mesh: MeshIndex,
    /// Actor slot (1-based into `actor_hashes`) per triangle; 0 means none.
    slots: Vec<u32>,
}

impl GroundSurface {
    pub fn from_bytes(data: &[u8]) -> Result<Self, PackError> {
        if data.len() < HEADER {
            return Err(PackError("Surface pack is truncated.".into()));
        }
        if &data[0..4] != MAGIC {
            return Err(PackError("Not an Ocarina Forge ground surface pack.".into()));
        }
        let version = u16::from_le_bytes(data[4..6].try_into().unwrap());
        if version != VERSION {
            return Err(PackError(format!("Unsupported surface pack version {version}.")));
        }
        let step = f32_at(data, 8)?;
        let origin = [f32_at(data, 12)?, f32_at(data, 16)?, f32_at(data, 20)?];
        let scale = f32_at(data, 24)?;
        let height_scale = f32_at(data, 28)?;
        let vertex_count = u32_at(data, 32)?;
        let index_count = u32_at(data, 36)?;
        let actor_count = u32_at(data, 40)?;
        let tile_count = u32_at(data, 44)?;
        let names_size = u32_at(data, 48)? as usize;
        if index_count % 3 != 0 {
            return Err(PackError("Surface pack index count is not a multiple of 3.".into()));
        }

        let mut offset = HEADER;
        let verts_bytes = vertex_count as usize * 12;
        let positions = f32_slice(data, offset, vertex_count as usize * 3)?;
        offset += verts_bytes;
        let indices = u32_slice(data, offset, index_count as usize)?;
        offset += index_count as usize * 4;

        let mut actor_hashes = Vec::with_capacity(actor_count as usize);
        let mut ranges = Vec::with_capacity(actor_count as usize);
        for _ in 0..actor_count {
            let hash = u64_at(data, offset)?;
            let first = u32_at(data, offset + 8)?;
            let count = u32_at(data, offset + 12)?;
            actor_hashes.push(hash);
            ranges.push((first, count));
            offset += 16;
        }

        let triangle_count = (index_count / 3) as usize;
        let mut slots = vec![0u32; triangle_count];
        for (slot, (first, count)) in ranges.iter().enumerate() {
            let start = *first as usize;
            let end = start + *count as usize;
            if end > triangle_count {
                return Err(PackError("Surface pack actor range is out of bounds.".into()));
            }
            for item in slots.iter_mut().take(end).skip(start) {
                *item = (slot as u32) + 1;
            }
        }

        let mut tile_headers = Vec::with_capacity(tile_count as usize);
        for _ in 0..tile_count {
            if offset + 24 > data.len() {
                return Err(PackError("Surface pack tile header is truncated.".into()));
            }
            let layer = data[offset];
            let name_offset = u32_at(data, offset + 4)?;
            let name_len = u32_at(data, offset + 8)?;
            let center_x = f32_at(data, offset + 12)?;
            let center_z = f32_at(data, offset + 16)?;
            let edge = f32_at(data, offset + 20)?;
            tile_headers.push((layer, name_offset, name_len, center_x, center_z, edge));
            offset += 24;
        }

        if offset + names_size > data.len() {
            return Err(PackError("Surface pack name table is truncated.".into()));
        }
        let names = &data[offset..offset + names_size];
        offset += names_size;

        let mut tiles = Vec::with_capacity(tile_count as usize);
        for (layer, name_offset, name_len, center_x, center_z, edge) in tile_headers {
            let start = name_offset as usize;
            let end = start + name_len as usize;
            if end > names.len() {
                return Err(PackError("Surface pack tile name is out of bounds.".into()));
            }
            let name = String::from_utf8_lossy(&names[start..end]).into_owned();
            let heights = f32_slice(data, offset, HEIGHT_COUNT)?;
            offset += HEIGHT_COUNT * 4;
            tiles.push(HeightTile {
                layer,
                name,
                center_x,
                center_z,
                edge,
                heights,
            });
        }

        let mut mesh = MeshIndex::new(step as f64);
        let mut actor_slots = Vec::with_capacity(triangle_count);
        let mut i = 0;
        while i + 2 < indices.len() {
            let mut tri = [0.0f64; 9];
            for corner in 0..3 {
                let vertex = indices[i + corner] as usize;
                let base = vertex * 3;
                if base + 2 >= positions.len() {
                    return Err(PackError("Surface pack index is out of bounds.".into()));
                }
                tri[corner * 3] = positions[base] as f64;
                tri[corner * 3 + 1] = positions[base + 1] as f64;
                tri[corner * 3 + 2] = positions[base + 2] as f64;
            }
            mesh.add(&tri);
            actor_slots.push(slots[i / 3]);
            i += 3;
        }

        let _ = offset;
        Ok(Self {
            step,
            origin,
            scale,
            height_scale,
            vertex_count,
            triangle_count: triangle_count as u32,
            actor_hashes,
            tiles,
            mesh,
            slots: actor_slots,
        })
    }

    pub fn sample_mesh(&self, x: f64, z: f64, exclude_hash: u64) -> Option<f64> {
        if exclude_hash == 0 {
            return self.mesh.sample(x, z);
        }
        let skip = self
            .actor_hashes
            .iter()
            .position(|item| *item == exclude_hash)
            .map(|index| (index as u32) + 1);
        let Some(skip) = skip else {
            return self.mesh.sample(x, z);
        };
        self.sample_mesh_skipping(x, z, skip)
    }

    fn sample_mesh_skipping(&self, x: f64, z: f64, skip: u32) -> Option<f64> {
        self.mesh.sample_filtered(x, z, |triangle_id| self.slots.get(triangle_id as usize).copied() != Some(skip))
    }

    pub fn sample_heightmap(&self, x: f64, z: f64) -> Option<f64> {
        sample_heightmap(&self.tiles, self.origin, self.scale, self.height_scale, x, z)
    }

    pub fn sample(&self, x: f64, z: f64, exclude_hash: u64) -> Option<f64> {
        compose_height(self.sample_heightmap(x, z), self.sample_mesh(x, z, exclude_hash)).0
    }

    pub fn memory_bytes(&self) -> usize {
        self.mesh.memory_bytes()
            + self.slots.len() * 4
            + self.actor_hashes.len() * 8
            + self.tiles.iter().map(|tile| tile.heights.len() * 4 + tile.name.len()).sum::<usize>()
    }
}

pub fn sample_heightmap(
    tiles: &[HeightTile],
    origin: [f32; 3],
    scale: f32,
    height_scale: f32,
    x: f64,
    z: f64,
) -> Option<f64> {
    let scale = if scale == 0.0 { 1.0 } else { scale as f64 };
    let unit_x = (x - origin[0] as f64) / scale;
    let unit_z = (z - origin[2] as f64) / scale;
    let mut hits: Vec<(f32, u8, &str, f64)> = Vec::new();
    for tile in tiles {
        if tile.heights.len() < HEIGHT_COUNT || tile.edge == 0.0 {
            continue;
        }
        let half = tile.edge as f64 / 2.0;
        let x0 = tile.center_x as f64 - half;
        let z0 = tile.center_z as f64 - half;
        if !(x0 <= unit_x && unit_x <= x0 + tile.edge as f64 && z0 <= unit_z && unit_z <= z0 + tile.edge as f64) {
            continue;
        }
        let span = (HEIGHT_SIZE - 1) as f64;
        let fx = (unit_x - x0) / tile.edge as f64 * span;
        let fz = (unit_z - z0) / tile.edge as f64 * span;
        if let Some(raw) = bilinear(&tile.heights, fx, fz) {
            let world = raw * height_scale as f64 + origin[1] as f64;
            hits.push((tile.edge, tile.layer, tile.name.as_str(), world));
        }
    }
    hits.sort_by(|a, b| {
        a.0.total_cmp(&b.0)
            .then(a.1.cmp(&b.1))
            .then(a.2.cmp(b.2))
    });
    hits.into_iter().map(|item| item.3).next()
}

fn bilinear(heights: &[f32], fx: f64, fz: f64) -> Option<f64> {
    let max = (HEIGHT_SIZE - 1) as f64;
    if fx < 0.0 || fz < 0.0 || fx > max || fz > max {
        return None;
    }
    let x0 = fx.floor() as usize;
    let z0 = fz.floor() as usize;
    let x1 = (x0 + 1).min(HEIGHT_SIZE - 1);
    let z1 = (z0 + 1).min(HEIGHT_SIZE - 1);
    let tx = fx - x0 as f64;
    let tz = fz - z0 as f64;
    let h00 = finite_nonzero(heights[z0 * HEIGHT_SIZE + x0])?;
    let h10 = finite_nonzero(heights[z0 * HEIGHT_SIZE + x1])?;
    let h01 = finite_nonzero(heights[z1 * HEIGHT_SIZE + x0])?;
    let h11 = finite_nonzero(heights[z1 * HEIGHT_SIZE + x1])?;
    Some(h00 * (1.0 - tx) * (1.0 - tz) + h10 * tx * (1.0 - tz) + h01 * (1.0 - tx) * tz + h11 * tx * tz)
}

fn finite_nonzero(value: f32) -> Option<f64> {
    let value = value as f64;
    if value.is_finite() && value != 0.0 {
        Some(value)
    } else {
        None
    }
}

fn f32_at(data: &[u8], offset: usize) -> Result<f32, PackError> {
    let bytes: [u8; 4] = data
        .get(offset..offset + 4)
        .ok_or_else(|| PackError("Surface pack is truncated.".into()))?
        .try_into()
        .unwrap();
    Ok(f32::from_le_bytes(bytes))
}

fn u32_at(data: &[u8], offset: usize) -> Result<u32, PackError> {
    let bytes: [u8; 4] = data
        .get(offset..offset + 4)
        .ok_or_else(|| PackError("Surface pack is truncated.".into()))?
        .try_into()
        .unwrap();
    Ok(u32::from_le_bytes(bytes))
}

fn u64_at(data: &[u8], offset: usize) -> Result<u64, PackError> {
    let bytes: [u8; 8] = data
        .get(offset..offset + 8)
        .ok_or_else(|| PackError("Surface pack is truncated.".into()))?
        .try_into()
        .unwrap();
    Ok(u64::from_le_bytes(bytes))
}

fn f32_slice(data: &[u8], offset: usize, count: usize) -> Result<Vec<f32>, PackError> {
    let bytes = count * 4;
    if offset + bytes > data.len() {
        return Err(PackError("Surface pack is truncated.".into()));
    }
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        out.push(f32_at(data, offset + i * 4)?);
    }
    Ok(out)
}

fn u32_slice(data: &[u8], offset: usize, count: usize) -> Result<Vec<u32>, PackError> {
    let bytes = count * 4;
    if offset + bytes > data.len() {
        return Err(PackError("Surface pack is truncated.".into()));
    }
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        out.push(u32_at(data, offset + i * 4)?);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::surface::compose_height;

    #[test]
    fn bilinear_matches_the_python_corners() {
        let mut heights = vec![0.0f32; HEIGHT_COUNT];
        heights[0] = 10.0;
        heights[1] = 20.0;
        heights[HEIGHT_SIZE] = 30.0;
        heights[HEIGHT_SIZE + 1] = 40.0;
        let mid = bilinear(&heights, 0.5, 0.5).unwrap();
        assert!((mid - 25.0).abs() < 1e-9);
        assert!(bilinear(&heights, -0.1, 0.0).is_none());
        heights[0] = 0.0;
        assert!(bilinear(&heights, 0.0, 0.0).is_none());
    }

    #[test]
    fn compose_still_prefers_the_island_over_the_lakebed() {
        let (y, source) = compose_height(Some(102.6), Some(131.353));
        assert!((y.unwrap() - 131.353).abs() < 1e-9);
        assert_eq!(source, Some("mesh"));
    }
}
