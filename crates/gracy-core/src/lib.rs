//! gracy-core: policy-free engine primitives (queue, throttle, concurrency,
//! transport, retry timing, metrics). No PyO3 in this crate — `cargo test`-able.

pub fn engine_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
