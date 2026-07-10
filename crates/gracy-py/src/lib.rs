//! gracy-py: PyO3 bindings exposing gracy-core as the `gracy._core` extension module.

use pyo3::prelude::*;

#[pyfunction]
fn engine_version() -> &'static str {
    gracy_core::engine_version()
}

#[pymodule(gil_used = false)]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    Ok(())
}
