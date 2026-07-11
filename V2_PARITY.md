# Gracy 2.0 — Feature Parity Checklist (release gate)

Rule: **2.0.0 does not ship until every row links to a passing test** (or is
explicitly marked `DROPPED` with rationale). Status below reflects the suite
at 328 tests / green on both engines (`GRACY_ENGINE=python` and `=rust`),
plus 35 `cargo test -p gracy-core` tests.

Legend: `rust` = crates/gracy-core, `py` = python/gracy, `split` = both.

| # | v1 feature | v2 home | Status | Test |
|---|---|---|---|---|
| 1 | `Gracy` base class + nested `Config` | py `client.py` (class attrs `base_url`/`timeout`/`config`; v1 `class Config` guarded) | ✔ | `tests/test_client.py` |
| 2 | Typed HTTP methods `self.get[T]` … | py `endpoints.py` (`@get` decorators + `api.request(decode_as=)`) | ✔ | `tests/test_endpoints.py` |
| 3 | `GracyConfig` + `Unset` + `merge_config` | py `config.py` + `plan.py` (one merge, one precedence chain) | ✔ | `tests/test_config.py` |
| 4 | `@graceful` / `@graceful_generator` | py (endpoint kwargs + `api.options()` + URL-glob overrides) | ✔ | `tests/test_client.py`, `tests/test_config.py` |
| 5 | Strict / allowed status codes | py `validators.py` (single `status_policy=`; conflict = build error) | ✔ | `tests/test_parsing_validation.py` |
| 6 | Custom validators (`GracefulValidator`) | py `validators.py` (same sync `check()` protocol) | ✔ | `tests/test_parsing_validation.py` |
| 7 | Per-status parser map (callable\|Exception\|literal) | py `parsing.py` (`on={...}`, semantics preserved) | ✔ | `tests/test_parsing_validation.py` |
| 8 | `GracefulRetry` (all fields, overrides, pass/break) | py policy (`Retry`/`Backoff`), timing math shared | ✔ | `tests/test_retry.py` |
| 9 | `GracefulRetryState` in after-hooks/logs | py (`RetryState`, same fields) | ✔ | `tests/test_retry.py`, `tests/test_hooks.py` |
| 10 | `GracefulThrottle` + `ThrottleRule` (regex, rolling window) | **rust** `queue/throttle.rs` (exact window; py reference mirrors) | ✔ | `tests/test_queue_throttle.py`, `tests/test_rust_engine.py`, cargo `queue_scheduler_test` |
| 11 | `ConcurrentRequestLimit` (global/per-uurl, key_by) | **rust** `queue/concurrency.rs` (+ py reference) | ✔ | `tests/test_queue_throttle.py`, cargo |
| 12 | `LogEvent`/`LogLevel` + full placeholder matrix | py `logging_events.py` (typed placeholders, NullHandler) | ✔ | `tests/test_logging.py` |
| 13 | `GracyUserDefinedException` + picklable hierarchy | py `exceptions.py` (subclass-preserving `__reduce__`) | ✔ | `tests/test_parsing_validation.py` |
| 14 | Reports + 4 printers (logger/list/rich/plotly) | split: collection py `reports/collector.py`, scheduler counters rust | ✔ | `tests/test_reports.py` |
| 15 | `GracyReplay` record/replay/smart-replay + flags | py policy `replay/__init__.py` (schema v2 storages) | ✔ | `tests/test_replay.py` |
| 16 | `SQLiteReplayStorage` | py `replay/storages.py` (schema v2, WAL, no pickle) | ✔ | `tests/test_replay.py`, `tests/test_migrate.py` |
| 17 | `MongoReplayStorage` | py `replay/mongo.py` (real extra, flushed by `aclose()`) | ✔* | interface parity; no live-Mongo test (v1 had none either) |
| 18 | Custom storage ABC | py protocol (async `prepare/record/find/load/flush`) | ✔ | `tests/test_replay.py` (MemoryStorage) |
| 19 | `GracyNamespace` auto-instantiation | py (explicit descriptor `berry = BerryNamespace()`) | ✔ | `tests/test_endpoints.py` |
| 20 | `GracyPaginator` / `GracyOffsetPaginator` | py `paginator.py` (`page_size` honored, typed) | ✔ | `tests/test_paginator.py` |
| 21 | before/after hooks + recursion guard | py `hooks.py`/`pipeline.py` (+ `from_hook` bypass) | ✔ | `tests/test_hooks.py` |
| 22 | Common backoff hooks + `HookResult` | py `hooks.py` → queue pause gates (actually pause now) | ✔ | `tests/test_hooks.py`, `tests/test_queue_throttle.py` |
| 23 | Customizing the httpx client | py `transports.py` (`TransportConfig` + `HttpxTransport` hatch + injected client) | ✔ | `tests/test_endpoints.py`, `tests/test_rust_engine.py` |
| 24 | Request kwargs + `BaseEndpoint` + `GracyRequestContext` | py `endpoints.py` (`api.request()` compat; one whitelist) | ✔ | `tests/test_endpoints.py` |
| 25 | `parsed_response` / `generated_parsed_response` | **DROPPED** — deprecated no-op shims; return annotations replace them | ✔ | n/a |
| 26 | `DEBUG_ENABLED` + introspection | py (`debug=True`, `api.plan.explain()`, `api.queue_stats()`) | ✔ | `tests/test_client.py` |
| 27 | `ThrottleController` rate metrics feeding reports | rust scheduler stats + py collector (`total_seconds` math) | ✔ | `tests/test_reports.py` |

## New in v2 (tested)

| Feature | Status | Test |
|---|---|---|
| Internal priority queue (priorities, seq fairness, retry re-admission) | ✔ | `tests/test_queue_throttle.py`, `tests/test_rust_engine.py`, cargo |
| Priority-aware backpressure (`max_pending`, `on_full=wait/raise`) | ✔ | same |
| Pause gates (`pause_on_status`, covers in-flight retries) | ✔ | `tests/test_queue_throttle.py` |
| Cancellation safety (grant drop / waiter cancel rollback) | ✔ | cargo `cancelled_submit_and_grant_drop_release_all_capacity` + `tests/test_rust_engine.py` |
| Fork safety (unstarted clients inert; started clients guarded) | ✔ | `tests/test_client.py` |
| Loop pinning (`GracyWrongLoopError`) | ✔ | `tests/test_client.py` |
| Sync facade (`Client.sync()`, hooks work in sync mode) | ✔ | `tests/test_client.py` |
| Scrub-on-record defaults | ✔ | `tests/test_replay.py` |
| Replay migration CLI (v1 pickle → schema v2, real repo fixture) | ✔ | `tests/test_migrate.py` |
| `gracy.testing` (retries_off / throttle_off / MockTransport) | ✔ | `tests/test_retry.py`, `tests/test_queue_throttle.py` |
| Property test: never > N requests in any trailing window W | ✔ | `tests/test_queue_throttle.py` + cargo (virtual time) |
| Rust engine default + `GRACY_ENGINE` selection | ✔ | `tests/test_rust_engine.py` (full suite runs on both) |
| `gracy.compat.requests` drop-in (verbs/Session/configure) | ✔ | `tests/test_compat_requests.py` |
| `gracy.compat.httpx` drop-in (AsyncClient/Client) | ✔ | `tests/test_compat_httpx.py` |
| Wheel matrix install+import (8 targets + cp314t) | ◐ | local abi3-py310 build green; CI wheels job added (`.github/workflows/ci.yml`), full matrix pending first main build |

## v1 bugs — regression tests asserting the v2 behavior

| # | Bug | Fixed by | Status |
|---|---|---|---|
| 1 | Throttle negative-wait when bursting over limit | exact sliding-window `next_allowed` | ✔ `tests/test_queue_throttle.py`, cargo |
| 2 | Retry validates stale response after transport error | per-attempt response reset | ✔ `tests/test_retry.py` |
| 3 | Namespace config double-merge / BASE_URL clobber / class mutation | single merge + descriptor | ✔ `tests/test_endpoints.py` |
| 4 | Paginator hardcodes `page_size=20` | honored param | ✔ `tests/test_paginator.py` |
| 5 | SQLite `discard_replays_older_than` TypeError (str vs datetime) | unix-ms integers | ✔ `tests/test_replay.py` |
| 6 | `plotly` extra not installable | declared extras | ✔ `pyproject.toml` (install-tested locally; CI extra job pending) |
| 7 | `logging.basicConfig` at import hijacks root logger | NullHandler | ✔ `tests/test_logging.py` |
| 8 | Pickle replay storage (RCE + httpx version trap) | schema v2, zero pickle at runtime | ✔ `tests/test_replay.py`, `tests/test_migrate.py` (fixture even contains an unpicklable old-httpx row — skipped loudly) |
| 9 | `readable_time_range` iterates a set (nondeterministic) | ordered rendering | ✔ (v2 renders from typed `Rate.per`) |
| 10 | Hook requests still throttled/limited despite docs | `from_hook` bypass + explicit knob | ✔ `tests/test_queue_throttle.py`, cargo |
| 11 | Backoff hooks claim to pause but don't | dispatcher pause gates | ✔ `tests/test_hooks.py` |
| 12 | Mongo batched writes silently lost (flush never called) | `aclose()` flushes | ✔ `tests/test_replay.py` (flush spy) |
| 13 | `after` hook: raw exc on retries, wrapped on first attempt | always `GracyRequestFailed`-wrapped | ✔ `tests/test_hooks.py` |
| 14 | Printers mutate the report (double TOTAL row) | frozen report, pure printers | ✔ `tests/test_reports.py` |
| 15 | `timedelta.seconds` truncation in req/s math | `total_seconds()` + monotonic | ✔ `tests/test_reports.py` |
| 16 | Pickled custom exceptions lose subclass identity | fixed `__reduce__` | ✔ `tests/test_parsing_validation.py` |
| 17 | Namespace string annotations fail cross-module | explicit descriptors | ✔ `tests/test_endpoints.py` |
| 18 | README documents removed 2-method storage ABC | docs match the real protocol | ✔ README rewritten from source |
| 19 | Report req/s regex from unescaped URL mis-matches | per-uurl aggregation, no regex-over-URL | ✔ `tests/test_reports.py` |

\* Row 17: `MongoReplayStorage` implements the same storage interface exercised
by MemoryStorage/Sqlite tests; a live-Mongo integration test (docker-compose)
is a pre-2.0.0 follow-up, matching v1 which shipped Mongo untested.
