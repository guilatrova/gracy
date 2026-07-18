//! CoreTransport / CoreResponse: PyO3 surface over `gracy_core::transport`.
//!
//! Error mapping: Timeout -> TimeoutError, Connect -> ConnectionError,
//! InvalidUrl -> ValueError, Other -> RuntimeError.

use std::sync::Arc;

use pyo3::exceptions::{PyConnectionError, PyRuntimeError, PyTimeoutError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use gracy_core::transport::{RequestData, ResponseData, Transport, TransportConfig, TransportError};

use crate::runtime::ensure_runtime;

fn transport_err_to_py(err: TransportError) -> PyErr {
    match err {
        TransportError::Timeout(msg) => {
            PyTimeoutError::new_err(format!("transport timeout: {msg}"))
        }
        TransportError::Connect(msg) => {
            PyConnectionError::new_err(format!("connection failed: {msg}"))
        }
        TransportError::InvalidUrl(msg) => PyValueError::new_err(format!("invalid URL: {msg}")),
        TransportError::Other(msg) => PyRuntimeError::new_err(format!("transport error: {msg}")),
    }
}

/// Fully buffered HTTP response (mirrors `gracy._types.Response`).
#[pyclass(module = "gracy._core", frozen)]
pub struct CoreResponse {
    #[pyo3(get)]
    status: u16,
    #[pyo3(get)]
    url: String,
    #[pyo3(get)]
    elapsed: f64,
    #[pyo3(get)]
    http_version: String,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl CoreResponse {
    fn from_data(data: ResponseData) -> Self {
        Self {
            status: data.status,
            url: data.url,
            elapsed: data.elapsed,
            http_version: data.http_version,
            headers: data.headers,
            body: data.body,
        }
    }
}

#[pymethods]
impl CoreResponse {
    /// Response headers: lowercase names, response order preserved.
    fn headers(&self) -> Vec<(String, String)> {
        self.headers.clone()
    }

    /// Raw response body bytes.
    fn body<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.body)
    }
}

/// reqwest-backed transport; one instance wraps one connection pool.
#[pyclass(module = "gracy._core", frozen)]
pub struct CoreTransport {
    inner: Arc<Transport>,
}

#[pymethods]
impl CoreTransport {
    #[new]
    fn new(config_json: &str) -> PyResult<Self> {
        let runtime = ensure_runtime();
        let config = TransportConfig::from_json(config_json).map_err(PyValueError::new_err)?;
        // Build inside the runtime context: reqwest may need a reactor handle.
        let _guard = runtime.enter();
        let transport = Transport::new(config).map_err(PyValueError::new_err)?;
        Ok(Self {
            inner: Arc::new(transport),
        })
    }

    /// Awaitable send: resolves to a `CoreResponse` with the full body buffered.
    #[pyo3(signature = (method, url, headers=Vec::new(), body=None, timeout=None))]
    fn send<'py>(
        &self,
        py: Python<'py>,
        method: String,
        url: String,
        headers: Vec<(String, String)>,
        body: Option<Vec<u8>>,
        timeout: Option<f64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        let req = RequestData {
            method,
            url,
            headers,
            body,
            timeout,
        };
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let response = inner.send(req).await.map_err(transport_err_to_py)?;
            Ok(CoreResponse::from_data(response))
        })
    }
}
