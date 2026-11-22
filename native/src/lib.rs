mod bc7;
mod decode;
mod swizzle;

pub use bc7::{bc7_mip_chain, encode_bc7};
pub use decode::{apply_channels, decode_linear, decode_swizzle};
pub use swizzle::{deswizzle, fill_partial_gob, swizzle};

#[cfg(feature = "python")]
mod py {
    use super::{
        apply_channels, bc7_mip_chain, decode_linear, decode_swizzle, deswizzle, encode_bc7, fill_partial_gob, swizzle,
    };
    use pyo3::prelude::*;
    use pyo3::types::PyBytes;

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
        Ok(())
    }
}
