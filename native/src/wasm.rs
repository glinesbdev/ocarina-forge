//! Browser bindings for the walkable-surface sampler and texture decode.
//!
//! Built with `--no-default-features --features wasm`. The python feature is
//! off, so PyO3 is not linked. SharedArrayBuffer threading lives in the
//! JavaScript worker pool: each worker instantiates this module and writes
//! a slice of results into a shared buffer.

use js_sys::Float64Array;
use wasm_bindgen::prelude::*;

use crate::decode::{decode_linear, decode_swizzle};
use crate::surface::MeshIndex;
use crate::swizzle;

#[wasm_bindgen]
pub struct WasmMeshIndex {
    inner: MeshIndex,
}

#[wasm_bindgen]
impl WasmMeshIndex {
    #[wasm_bindgen(constructor)]
    pub fn new(step: f64, triangles: &Float64Array) -> WasmMeshIndex {
        let data = triangles.to_vec();
        WasmMeshIndex {
            inner: MeshIndex::from_triangles(step, &data),
        }
    }

    #[wasm_bindgen(getter, js_name = triangleCount)]
    pub fn triangle_count(&self) -> u32 {
        self.inner.len() as u32
    }

    /// Sample every XZ pair in `points` (length `2 * N`). Missing hits are NaN.
    #[wasm_bindgen(js_name = sampleMany)]
    pub fn sample_many(&self, points: &Float64Array) -> Float64Array {
        let pts = points.to_vec();
        let n = pts.len() / 2;
        let mut out = vec![f64::NAN; n];
        self.inner.sample_many(&pts, &mut out);
        vec_to_f64(&out)
    }

    /// Sample `points[2*start .. 2*end]` and return `end - start` heights.
    /// The worker pool uses this so each worker only copies its own slice.
    #[wasm_bindgen(js_name = sampleRange)]
    pub fn sample_range(&self, points: &Float64Array, start: u32, end: u32) -> Float64Array {
        let pts = points.to_vec();
        let n = pts.len() / 2;
        let start = (start as usize).min(n);
        let end = (end as usize).min(n).max(start);
        let mut out = vec![f64::NAN; end - start];
        self.inner.sample_many(&pts[start * 2..end * 2], &mut out);
        vec_to_f64(&out)
    }

    #[wasm_bindgen(js_name = sampleOne)]
    pub fn sample_one(&self, x: f64, z: f64) -> f64 {
        self.inner.sample(x, z).unwrap_or(f64::NAN)
    }
}

fn vec_to_f64(values: &[f64]) -> Float64Array {
    let out = Float64Array::new_with_length(values.len() as u32);
    out.copy_from(values);
    out
}

/// Deswizzle Tegra/Switch block-linear bytes into a linear surface.
#[wasm_bindgen(js_name = deswizzle)]
pub fn deswizzle_bytes(
    width: i32,
    height: i32,
    block_w: i32,
    block_h: i32,
    bytes_per: i32,
    tile_mode: i32,
    log2: i32,
    data: &[u8],
) -> Vec<u8> {
    swizzle::deswizzle(width, height, block_w, block_h, bytes_per, tile_mode, log2, data, None)
}

/// Decode a linear (already deswizzled) surface to RGBA.
#[wasm_bindgen(js_name = decodeLinear)]
pub fn decode_linear_bytes(codec: &str, width: i32, height: i32, linear: &[u8], block_w: i32, block_h: i32) -> Option<Vec<u8>> {
    decode_linear(codec, width, height, linear, block_w, block_h).map(|(rgba, _note)| rgba)
}

/// Deswizzle and decode a block-linear surface to RGBA in one pass.
#[wasm_bindgen(js_name = decodeSwizzle)]
pub fn decode_swizzle_bytes(
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
) -> Option<Vec<u8>> {
    decode_swizzle(codec, width, height, block_w, block_h, bytes_per, tile, log2, raw, channels).map(|(rgba, _note, _used)| rgba)
}
