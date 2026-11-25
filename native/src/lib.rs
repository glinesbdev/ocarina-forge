mod bc7;
mod bfres;
mod decode;
mod image;
mod mesh;
mod surface;
mod swizzle;
mod verts;

pub use bc7::{bc7_mip_chain, encode_bc7};
pub use bfres::{attribute_bounds, attribute_values, decode_attribute, decode_triangles, pack_f32};
pub use mesh::{face_normal_bytes, face_normals};
pub use decode::{apply_channels, decode_linear, decode_swizzle, noise};
pub use image::{bc1_mip_chain, blit_channel, downsample, encode_bc1, has_near_magenta, limit_rgba, rgba_opaque};
pub use swizzle::{deswizzle, fill_partial_gob, swizzle};
pub use verts::{close_uv_seams, pack_f32_from_f64, shape_key, transform_positions, vec_bounds};
pub use surface::{compose_height, triangle_height, MeshIndex};

#[cfg(feature = "wasm")]
mod wasm;

#[cfg(feature = "python")]
mod py {
    use super::{
        apply_channels, attribute_bounds, attribute_values, bc1_mip_chain, bc7_mip_chain, blit_channel, decode_attribute,
        decode_linear,
        decode_swizzle, decode_triangles, deswizzle, downsample, encode_bc1, encode_bc7, fill_partial_gob, has_near_magenta,
        face_normal_bytes, face_normals, limit_rgba, noise, pack_f32_from_f64, rgba_opaque, shape_key, swizzle,
        transform_positions, vec_bounds,
    };
    use super::close_uv_seams;
    use pyo3::prelude::*;
    use pyo3::types::{PyByteArray, PyByteArrayMethods, PyBytes, PyList, PyTuple};

    fn order_of(order: Option<&str>) -> Option<&str> {
        match order {
            Some(text) if !text.is_empty() => Some(text),
            _ => None,
        }
    }

    #[pyfunction]
    #[pyo3(name = "encode_bc7")]
    fn encode_bc7_py(py: Python<'_>, width: u32, height: u32, rgba: &[u8]) -> PyResult<Py<PyBytes>> {
        let owned = rgba.to_vec();
        let out = py.allow_threads(move || encode_bc7(width, height, &owned));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "bc7_mip_chain")]
    fn bc7_mip_chain_py(py: Python<'_>, width: u32, height: u32, rgba: &[u8]) -> PyResult<Vec<Py<PyBytes>>> {
        let owned = rgba.to_vec();
        let levels = py.allow_threads(move || bc7_mip_chain(width, height, &owned));
        Ok(levels
            .into_iter()
            .map(|level| PyBytes::new(py, &level).unbind())
            .collect())
    }

    #[pyfunction]
    #[pyo3(name = "deswizzle", signature = (width, height, block_w, block_h, bytes_per, tile_mode, log2, data, order=None))]
    fn deswizzle_py(
        py: Python<'_>,
        width: i32,
        height: i32,
        block_w: i32,
        block_h: i32,
        bytes_per: i32,
        tile_mode: i32,
        log2: i32,
        data: &[u8],
        order: Option<&str>,
    ) -> PyResult<Py<PyBytes>> {
        let owned = data.to_vec();
        let chosen = order_of(order).map(str::to_string);
        let out = py.allow_threads(move || {
            deswizzle(
                width,
                height,
                block_w,
                block_h,
                bytes_per,
                tile_mode,
                log2,
                &owned,
                chosen.as_deref(),
            )
        });
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "swizzle", signature = (width, height, block_w, block_h, bytes_per, tile_mode, log2, data, order=None))]
    fn swizzle_py(
        py: Python<'_>,
        width: i32,
        height: i32,
        block_w: i32,
        block_h: i32,
        bytes_per: i32,
        tile_mode: i32,
        log2: i32,
        data: &[u8],
        order: Option<&str>,
    ) -> PyResult<Py<PyBytes>> {
        let owned = data.to_vec();
        let chosen = order_of(order).map(str::to_string);
        let out = py.allow_threads(move || {
            swizzle(
                width,
                height,
                block_w,
                block_h,
                bytes_per,
                tile_mode,
                log2,
                &owned,
                chosen.as_deref(),
            )
        });
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "fill_partial_gob")]
    fn fill_partial_gob_py(
        py: Python<'_>,
        linear: &[u8],
        width: i32,
        height: i32,
        block_w: i32,
        block_h: i32,
        bytes_per: i32,
    ) -> PyResult<Py<PyBytes>> {
        let owned = linear.to_vec();
        let out = py.allow_threads(move || fill_partial_gob(&owned, width, height, block_w, block_h, bytes_per));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "decode_linear")]
    fn decode_linear_py(
        py: Python<'_>,
        codec: &str,
        width: i32,
        height: i32,
        linear: &[u8],
        block_w: i32,
        block_h: i32,
    ) -> PyResult<Option<(Py<PyBytes>, String)>> {
        let owned = linear.to_vec();
        let codec = codec.to_string();
        let decoded = py.allow_threads(move || decode_linear(&codec, width, height, &owned, block_w, block_h));
        Ok(decoded.map(|(rgba, note)| (PyBytes::new(py, &rgba).unbind(), note)))
    }

    #[pyfunction]
    #[pyo3(name = "decode_swizzle")]
    fn decode_swizzle_py(
        py: Python<'_>,
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
    ) -> PyResult<Option<(Py<PyBytes>, String, i32)>> {
        let owned = raw.to_vec();
        let ch = channels.to_vec();
        let codec = codec.to_string();
        let decoded = py.allow_threads(move || {
            decode_swizzle(&codec, width, height, block_w, block_h, bytes_per, tile, log2, &owned, &ch)
        });
        Ok(decoded.map(|(rgba, note, used)| (PyBytes::new(py, &rgba).unbind(), note, used)))
    }

    #[pyfunction]
    #[pyo3(name = "limit_rgba")]
    fn limit_rgba_py(py: Python<'_>, width: i32, height: i32, rgba: &[u8], limit: i32) -> PyResult<(i32, i32, Py<PyBytes>)> {
        let owned = rgba.to_vec();
        let (nw, nh, out) = py.allow_threads(move || limit_rgba(width, height, &owned, limit));
        Ok((nw, nh, PyBytes::new(py, &out).unbind()))
    }

    #[pyfunction]
    #[pyo3(name = "downsample")]
    fn downsample_py(py: Python<'_>, width: i32, height: i32, rgba: &[u8]) -> PyResult<(i32, i32, Py<PyBytes>)> {
        let owned = rgba.to_vec();
        let (nw, nh, out) = py.allow_threads(move || downsample(width, height, &owned));
        Ok((nw, nh, PyBytes::new(py, &out).unbind()))
    }

    #[pyfunction]
    #[pyo3(name = "has_near_magenta")]
    fn has_near_magenta_py(rgba: &[u8]) -> bool {
        has_near_magenta(rgba)
    }

    #[pyfunction]
    #[pyo3(name = "noise")]
    fn noise_py(rgba: &[u8], width: i32, height: i32) -> f64 {
        noise(rgba, width, height)
    }

    #[pyfunction]
    #[pyo3(name = "encode_bc1")]
    fn encode_bc1_py(py: Python<'_>, width: u32, height: u32, rgba: &[u8]) -> PyResult<Py<PyBytes>> {
        let owned = rgba.to_vec();
        let out = py.allow_threads(move || encode_bc1(width, height, &owned));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "bc1_mip_chain")]
    fn bc1_mip_chain_py(py: Python<'_>, width: u32, height: u32, rgba: &[u8]) -> PyResult<Vec<Py<PyBytes>>> {
        let owned = rgba.to_vec();
        let levels = py.allow_threads(move || bc1_mip_chain(width, height, &owned));
        Ok(levels
            .into_iter()
            .map(|level| PyBytes::new(py, &level).unbind())
            .collect())
    }

    #[pyfunction]
    #[pyo3(name = "blit_channel")]
    fn blit_channel_py(
        out: &Bound<'_, PyByteArray>,
        width: i32,
        height: i32,
        src: &[u8],
        src_w: i32,
        src_h: i32,
        channel: i32,
        dest: i32,
    ) -> PyResult<()> {
        // The bytearray stays the Python object pack_layer already built.
        // Nothing in this call resizes it, and the GIL is held.
        let buffer = unsafe { out.as_bytes_mut() };
        blit_channel(buffer, width, height, src, src_w, src_h, channel, dest);
        Ok(())
    }

    #[pyfunction]
    #[pyo3(name = "decode_rows")]
    fn decode_rows_py(
        py: Python<'_>,
        buffer: &[u8],
        count: u32,
        stride: u32,
        offset: u32,
        format: u32,
    ) -> PyResult<Py<PyAny>> {
        let owned = buffer.to_vec();
        let values = py.allow_threads(move || attribute_values(&owned, count, stride, offset, format));
        let Some(values) = values else {
            return Ok(py.None());
        };
        if count == 0 {
            return Ok(PyList::empty(py).unbind().into_any());
        }
        let width = values.len() / count as usize;
        if width == 0 || values.len() != count as usize * width {
            return Ok(py.None());
        }
        let list = PyList::empty(py);
        for chunk in values.chunks(width) {
            list.append(PyTuple::new(py, chunk.iter().copied())?)?;
        }
        Ok(list.unbind().into_any())
    }

    #[pyfunction]
    #[pyo3(name = "decode_attribute")]
    fn decode_attribute_py(
        py: Python<'_>,
        buffer: &[u8],
        count: u32,
        stride: u32,
        offset: u32,
        format: u32,
    ) -> PyResult<Py<PyBytes>> {
        let owned = buffer.to_vec();
        let out = py.allow_threads(move || decode_attribute(&owned, count, stride, offset, format));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "attribute_bounds")]
    fn attribute_bounds_py(
        py: Python<'_>,
        buffer: &[u8],
        count: u32,
        stride: u32,
        offset: u32,
        format: u32,
    ) -> PyResult<Option<(u32, Py<PyBytes>, Py<PyBytes>)>> {
        let owned = buffer.to_vec();
        let found = py.allow_threads(move || attribute_bounds(&owned, count, stride, offset, format));
        Ok(found.map(|(finite, low, high)| (finite, PyBytes::new(py, &low).unbind(), PyBytes::new(py, &high).unbind())))
    }

    #[pyfunction]
    #[pyo3(name = "decode_triangles")]
    fn decode_triangles_py(
        py: Python<'_>,
        blob: &[u8],
        width: u32,
        pairs: Vec<u32>,
        first: u32,
        primitive: u32,
    ) -> PyResult<Py<PyBytes>> {
        let owned = blob.to_vec();
        let out = py.allow_threads(move || decode_triangles(&owned, width, &pairs, first, primitive));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "pack_f32")]
    fn pack_f32_py(py: Python<'_>, rows: &Bound<'_, PyAny>, width: usize) -> PyResult<Py<PyBytes>> {
        if width == 0 || width > 4 {
            return Ok(PyBytes::new(py, b"").unbind());
        }
        let mut out = Vec::new();
        for item in rows.try_iter()? {
            let mut got = 0usize;
            for component in item?.try_iter()? {
                if got == width {
                    break;
                }
                let value = component?.extract::<f64>()? as f32;
                out.extend_from_slice(&value.to_le_bytes());
                got += 1;
            }
            if got != width {
                return Ok(PyBytes::new(py, b"").unbind());
            }
        }
        Ok(PyBytes::new(py, &out).unbind())
    }

    fn read_mesh(positions: &Bound<'_, PyAny>, indices: &Bound<'_, PyAny>) -> PyResult<Option<(Vec<f64>, Vec<u32>)>> {
        let mut xyz = Vec::new();
        for item in positions.try_iter()? {
            let mut got = 0usize;
            for component in item?.try_iter()? {
                if got == 3 {
                    break;
                }
                xyz.push(component?.extract::<f64>()?);
                got += 1;
            }
            if got != 3 {
                return Ok(None);
            }
        }
        let mut index = Vec::new();
        for item in indices.try_iter()? {
            let value = item?.extract::<u64>()?;
            if value > u32::MAX as u64 {
                return Ok(None);
            }
            index.push(value as u32);
        }
        Ok(Some((xyz, index)))
    }

    #[pyfunction]
    #[pyo3(name = "face_normal_bytes")]
    fn face_normal_bytes_py(py: Python<'_>, positions: &Bound<'_, PyAny>, indices: &Bound<'_, PyAny>) -> PyResult<Py<PyBytes>> {
        let Some((xyz, index)) = read_mesh(positions, indices)? else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let out = py.allow_threads(move || face_normal_bytes(&xyz, &index));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "face_normals")]
    fn face_normals_py(py: Python<'_>, positions: &Bound<'_, PyAny>, indices: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let Some((xyz, index)) = read_mesh(positions, indices)? else {
            return Ok(py.None());
        };
        let count = xyz.len() / 3;
        let out = py.allow_threads(move || face_normals(&xyz, &index));
        if out.len() != count * 3 {
            return Ok(py.None());
        }
        let list = PyList::empty(py);
        for chunk in out.chunks(3) {
            list.append(PyTuple::new(py, chunk.iter().copied())?)?;
        }
        Ok(list.unbind().into_any())
    }

    fn f64_buf(bytes: &[u8]) -> Option<Vec<f64>> {
        if bytes.len() % 8 != 0 {
            return None;
        }
        Some(bytes.chunks_exact(8).map(|chunk| f64::from_le_bytes(chunk.try_into().unwrap())).collect())
    }

    fn i32_buf(bytes: &[u8]) -> Option<Vec<i32>> {
        if bytes.len() % 4 != 0 {
            return None;
        }
        Some(bytes.chunks_exact(4).map(|chunk| i32::from_le_bytes(chunk.try_into().unwrap())).collect())
    }

    fn u32_buf(bytes: &[u8]) -> Option<Vec<u32>> {
        if bytes.len() % 4 != 0 {
            return None;
        }
        Some(bytes.chunks_exact(4).map(|chunk| u32::from_le_bytes(chunk.try_into().unwrap())).collect())
    }

    fn f64_bytes(values: &[f64]) -> Vec<u8> {
        let mut out = Vec::with_capacity(values.len() * 8);
        for value in values {
            out.extend_from_slice(&value.to_le_bytes());
        }
        out
    }

    #[pyfunction]
    #[pyo3(name = "transform_positions")]
    fn transform_positions_py(
        py: Python<'_>,
        positions: &[u8],
        worlds: &[u8],
        bone_index: i32,
        slots: &[u8],
        palette: &[u8],
        rigid: &[u8],
    ) -> PyResult<Py<PyBytes>> {
        let Some(xyz) = f64_buf(positions) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let Some(matrices) = f64_buf(worlds) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let Some(slot_idx) = i32_buf(slots) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let Some(palette_idx) = i32_buf(palette) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let Some(rigid_idx) = i32_buf(rigid) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let out = py.allow_threads(move || transform_positions(&xyz, &matrices, bone_index, &slot_idx, &palette_idx, &rigid_idx));
        Ok(PyBytes::new(py, &f64_bytes(&out)).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "vec_bounds")]
    fn vec_bounds_py(py: Python<'_>, values: &[u8], width: usize) -> PyResult<Py<PyAny>> {
        let Some(data) = f64_buf(values) else {
            return Ok(py.None());
        };
        let found = py.allow_threads(move || vec_bounds(&data, width));
        let Some((low, high)) = found else {
            return Ok(py.None());
        };
        Ok(PyTuple::new(py, [PyBytes::new(py, &f64_bytes(&low)), PyBytes::new(py, &f64_bytes(&high))])?.into_any().unbind())
    }

    #[pyfunction]
    #[pyo3(name = "pack_f64")]
    fn pack_f64_py(py: Python<'_>, values: &[u8]) -> PyResult<Py<PyBytes>> {
        let Some(data) = f64_buf(values) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let out = py.allow_threads(move || pack_f32_from_f64(&data));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "shape_key")]
    fn shape_key_py(py: Python<'_>, positions: &[u8], indices: &[u8]) -> PyResult<Py<PyBytes>> {
        let Some(xyz) = f64_buf(positions) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let Some(index) = u32_buf(indices) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let out = py.allow_threads(move || shape_key(&xyz, &index));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "face_normal_packed")]
    fn face_normal_packed_py(py: Python<'_>, positions: &[u8], indices: &[u8]) -> PyResult<Py<PyBytes>> {
        let Some(xyz) = f64_buf(positions) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let Some(index) = u32_buf(indices) else {
            return Ok(PyBytes::new(py, b"").unbind());
        };
        let out = py.allow_threads(move || face_normal_bytes(&xyz, &index));
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pyfunction]
    #[pyo3(name = "close_uv_seams")]
    fn close_uv_seams_py(
        py: Python<'_>,
        positions: &[u8],
        uvs: &[u8],
        indices: &[u8],
        extras: Vec<Vec<u8>>,
        widths: Vec<usize>,
    ) -> PyResult<Py<PyAny>> {
        let Some(xyz) = f64_buf(positions) else {
            return Ok(py.None());
        };
        let Some(uv) = f64_buf(uvs) else {
            return Ok(py.None());
        };
        let Some(index) = u32_buf(indices) else {
            return Ok(py.None());
        };
        let mut extra_f64 = Vec::new();
        for blob in &extras {
            let Some(data) = f64_buf(blob) else {
                return Ok(py.None());
            };
            extra_f64.push(data);
        }
        let found = py.allow_threads(move || close_uv_seams(&xyz, &uv, &index, &extra_f64, &widths));
        let Some((pos, uv_out, idx, carried)) = found else {
            return Ok(py.None());
        };
        let extra_list = PyList::empty(py);
        for item in carried {
            extra_list.append(PyBytes::new(py, &f64_bytes(&item)))?;
        }
        let mut raw_idx = Vec::with_capacity(idx.len() * 4);
        for value in idx {
            raw_idx.extend_from_slice(&value.to_le_bytes());
        }
        Ok(PyTuple::new(
            py,
            [
                PyBytes::new(py, &f64_bytes(&pos)).into_any(),
                PyBytes::new(py, &f64_bytes(&uv_out)).into_any(),
                PyBytes::new(py, &raw_idx).into_any(),
                extra_list.into_any(),
            ],
        )?
        .into_any()
        .unbind())
    }

    #[pyfunction]
    #[pyo3(name = "rgba_opaque")]
    fn rgba_opaque_py(py: Python<'_>, rgba: &[u8]) -> PyResult<bool> {
        let owned = rgba.to_vec();
        Ok(py.allow_threads(move || rgba_opaque(&owned)))
    }

    #[pyfunction]
    #[pyo3(name = "apply_channels")]
    fn apply_channels_py(
        py: Python<'_>,
        rgba: &[u8],
        width: i32,
        height: i32,
        channels: &[u8],
    ) -> PyResult<Py<PyBytes>> {
        let out = apply_channels(rgba, width, height, channels);
        Ok(PyBytes::new(py, &out).unbind())
    }

    #[pymodule]
    fn ocarina_forge_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add_function(wrap_pyfunction!(encode_bc7_py, module)?)?;
        module.add_function(wrap_pyfunction!(bc7_mip_chain_py, module)?)?;
        module.add_function(wrap_pyfunction!(deswizzle_py, module)?)?;
        module.add_function(wrap_pyfunction!(swizzle_py, module)?)?;
        module.add_function(wrap_pyfunction!(fill_partial_gob_py, module)?)?;
        module.add_function(wrap_pyfunction!(decode_linear_py, module)?)?;
        module.add_function(wrap_pyfunction!(decode_swizzle_py, module)?)?;
        module.add_function(wrap_pyfunction!(apply_channels_py, module)?)?;
        module.add_function(wrap_pyfunction!(limit_rgba_py, module)?)?;
        module.add_function(wrap_pyfunction!(downsample_py, module)?)?;
        module.add_function(wrap_pyfunction!(has_near_magenta_py, module)?)?;
        module.add_function(wrap_pyfunction!(noise_py, module)?)?;
        module.add_function(wrap_pyfunction!(encode_bc1_py, module)?)?;
        module.add_function(wrap_pyfunction!(bc1_mip_chain_py, module)?)?;
        module.add_function(wrap_pyfunction!(blit_channel_py, module)?)?;
        module.add_function(wrap_pyfunction!(decode_attribute_py, module)?)?;
        module.add_function(wrap_pyfunction!(decode_rows_py, module)?)?;
        module.add_function(wrap_pyfunction!(attribute_bounds_py, module)?)?;
        module.add_function(wrap_pyfunction!(decode_triangles_py, module)?)?;
        module.add_function(wrap_pyfunction!(pack_f32_py, module)?)?;
        module.add_function(wrap_pyfunction!(face_normals_py, module)?)?;
        module.add_function(wrap_pyfunction!(face_normal_bytes_py, module)?)?;
        module.add_function(wrap_pyfunction!(transform_positions_py, module)?)?;
        module.add_function(wrap_pyfunction!(vec_bounds_py, module)?)?;
        module.add_function(wrap_pyfunction!(pack_f64_py, module)?)?;
        module.add_function(wrap_pyfunction!(shape_key_py, module)?)?;
        module.add_function(wrap_pyfunction!(face_normal_packed_py, module)?)?;
        module.add_function(wrap_pyfunction!(close_uv_seams_py, module)?)?;
        module.add_function(wrap_pyfunction!(rgba_opaque_py, module)?)?;
        Ok(())
    }
}
