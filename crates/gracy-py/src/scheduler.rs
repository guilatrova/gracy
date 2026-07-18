//! CoreScheduler / CorePermit: PyO3 surface over `gracy_core::queue::Scheduler`.
//!
//! Submit errors cross the FFI as `RuntimeError` with marker messages
//! ("GRACY_QUEUE_FULL" / "GRACY_CLOSED"); the Python wrapper translates them
//! into gracy exception types.

use std::sync::{Arc, Mutex};

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

use gracy_core::queue::{Grant, Scheduler, SubmitError};

use crate::runtime::ensure_runtime;

fn submit_err_to_py(err: SubmitError) -> PyErr {
    match err {
        SubmitError::QueueFull => PyRuntimeError::new_err("GRACY_QUEUE_FULL"),
        SubmitError::Closed => PyRuntimeError::new_err("GRACY_CLOSED"),
    }
}

/// A granted admission (throttle tokens spent, concurrency slots held).
///
/// `release()` frees the slots and decrements in_flight; it is idempotent,
/// and garbage-collecting the permit releases it too (via `Grant`'s Drop).
#[pyclass(module = "gracy._core", frozen)]
pub struct CorePermit {
    grant: Mutex<Option<Grant>>,
}

impl CorePermit {
    pub(crate) fn new(grant: Grant) -> Self {
        Self {
            grant: Mutex::new(Some(grant)),
        }
    }
}

#[pymethods]
impl CorePermit {
    /// Release concurrency slots + in_flight. Safe to call more than once.
    fn release(&self) {
        // A poisoned lock only means a previous release panicked mid-drop;
        // taking the value out is still the right (idempotent) behavior.
        let mut slot = match self.grant.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        slot.take();
    }
}

/// The queue/throttle engine. Constructed from the `scheduler_plan` JSON
/// emitted by `gracy.plan.compile_plan`.
#[pyclass(module = "gracy._core", frozen)]
pub struct CoreScheduler {
    inner: Arc<Scheduler>,
}

#[pymethods]
impl CoreScheduler {
    #[new]
    fn new(plan_json: &str) -> PyResult<Self> {
        ensure_runtime();
        let scheduler = Scheduler::from_plan_json(plan_json).map_err(PyValueError::new_err)?;
        Ok(Self {
            inner: Arc::new(scheduler),
        })
    }

    /// Awaitable admission: resolves to a `CorePermit` when the request may
    /// go on the wire NOW.
    #[pyo3(signature = (uurl, url, priority=0, from_hook=false, no_throttle=false, conc_extra=String::new()))]
    #[allow(clippy::too_many_arguments)]
    fn submit<'py>(
        &self,
        py: Python<'py>,
        uurl: String,
        url: String,
        priority: i32,
        from_hook: bool,
        no_throttle: bool,
        conc_extra: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let grant = inner
                .submit(&uurl, &url, priority, from_hook, no_throttle, &conc_extra)
                .await
                .map_err(submit_err_to_py)?;
            Ok(CorePermit::new(grant))
        })
    }

    /// Pause a scope ("client" or a uurl) for `seconds`. Extends, never shortens.
    fn pause(&self, scope: &str, seconds: f64) {
        self.inner.pause(scope, seconds);
    }

    /// Point-in-time stats snapshot as JSON (same shape as `PyScheduler.stats()`).
    fn stats_json(&self) -> PyResult<String> {
        serde_json::to_string(&self.inner.stats())
            .map_err(|e| PyRuntimeError::new_err(format!("failed to serialize stats: {e}")))
    }

    /// Close the scheduler: pending and future submits fail with GRACY_CLOSED.
    fn close(&self) {
        self.inner.close();
    }
}
