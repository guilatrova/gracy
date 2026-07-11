//! Lazy global tokio runtime shared by every CoreScheduler / CoreTransport.
//!
//! NEVER started at import — built on the first constructor call. Also points
//! pyo3-async-runtimes at this runtime so `future_into_py` spawns onto it.

use std::sync::OnceLock;

use tokio::runtime::Runtime;

static RUNTIME: OnceLock<Runtime> = OnceLock::new();

/// Build (once) and return the shared runtime: multi-thread,
/// `worker_threads = min(4, cpus)`, threads named "gracy-core".
pub fn ensure_runtime() -> &'static Runtime {
    let rt = RUNTIME.get_or_init(|| {
        let cpus = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(1);
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(cpus.min(4))
            .thread_name("gracy-core")
            .enable_all()
            .build()
            .expect("failed to build gracy tokio runtime")
    });
    // Err(()) just means it was already registered — same runtime either way.
    let _ = pyo3_async_runtimes::tokio::init_with_runtime(rt);
    rt
}
