//! gracy-py: PyO3 bindings exposing gracy-core as the `gracy._core` extension module.

mod runtime;
mod scheduler;
mod transport;

use pyo3::prelude::*;

use crate::scheduler::{CorePermit, CoreScheduler};
use crate::transport::{CoreResponse, CoreTransport};

#[pyfunction]
fn engine_version() -> &'static str {
    gracy_core::engine_version()
}

#[pymodule(gil_used = false)]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_class::<CoreScheduler>()?;
    m.add_class::<CorePermit>()?;
    m.add_class::<CoreTransport>()?;
    m.add_class::<CoreResponse>()?;
    Ok(())
}
