# Gracy 2.0 — Feature Parity Checklist (release gate)

Rule: **2.0.0 does not ship until every row links to a passing test** (or is
explicitly marked `DROPPED` with rationale). Ported v1 tests are the parity
oracle; rows fixed-by-design still need a test asserting the new behavior.

Legend: `rust` = crates/gracy-core, `py` = python/gracy, `split` = both.

| # | v1 feature | v2 home | Status | Test |
|---|---|---|---|---|
| 1 | `Gracy` base class + nested `Config` | py `client.py` (class attrs `base_url`/`timeout`/`config`) | ☐ | — |
| 2 | Typed HTTP methods `self.get[T]` … | py `endpoints.py` (`@get` decorators + `api.request(decode_as=)`) | ☐ | — |
| 3 | `GracyConfig` + `Unset` + `merge_config` | py `config.py` + `plan.py` (one merge, one precedence chain) | ☐ | — |
| 4 | `@graceful` / `@graceful_generator` | py (endpoint kwargs + `api.options()` + URL-glob overrides) | ☐ | — |
| 5 | Strict / allowed status codes | py `validators.py` (single `status_policy=`; conflict = build error) | ☐ | — |
| 6 | Custom validators (`GracefulValidator`) | py `validators.py` (same sync `check()` protocol) | ☐ | — |
| 7 | Per-status parser map (callable\|Exception\|literal) | py `parsing.py` (`on={...}`, semantics preserved) | ☐ | — |
| 8 | `GracefulRetry` (all fields, overrides, pass/break) | split: policy py, timing rust `retry_timing.rs` | ☐ | — |
| 9 | `GracefulRetryState` in after-hooks/logs | py (`RetryState`, same fields) | ☐ | — |
| 10 | `GracefulThrottle` + `ThrottleRule` (regex, rolling window) | rust `queue/throttle.rs` (exact window default, GCRA opt-in) | ☐ | — |
| 11 | `ConcurrentRequestLimit` (global/per-uurl, blocking_args) | rust `queue/concurrency.rs` (+ idle TTL eviction) | ☐ | — |
| 12 | `LogEvent`/`LogLevel` + full placeholder matrix | py `logging_events.py` (typed placeholders, NullHandler) | ☐ | — |
| 13 | `GracyUserDefinedException` + picklable hierarchy | py `exceptions.py` (subclass-preserving `__reduce__`) | ☐ | — |
| 14 | Reports + 4 printers (logger/list/rich/plotly) | split: collection rust `metrics.rs`, rendering py | ☐ | — |
| 15 | `GracyReplay` record/replay/smart-replay + flags | split: policy py, SQLite engine rust | ☐ | — |
| 16 | `SQLiteReplayStorage` | rust `replay/sqlite.rs` (schema v2, no pickle) | ☐ | — |
| 17 | `MongoReplayStorage` | py `replay/mongo.py` (real extra, flushed by `aclose()`) | ☐ | — |
| 18 | Custom storage ABC | py protocol (async `prepare/record/find/load/flush`) | ☐ | — |
| 19 | `GracyNamespace` auto-instantiation | py (explicit descriptor `berry = BerryNamespace()`) | ☐ | — |
| 20 | `GracyPaginator` / `GracyOffsetPaginator` | py `paginator.py` (`page_size` honored, typed) | ☐ | — |
| 21 | before/after hooks + recursion guard | py `hooks.py`/`pipeline.py` (+ `from_hook` bypass) | ☐ | — |
| 22 | Common backoff hooks + `HookResult` | py `hooks.py` → queue pause gates (actually pause now) | ☐ | — |
| 23 | Customizing the httpx client | py `transports.py` (`TransportConfig` + `HttpxTransport` hatch) | ☐ | — |
| 24 | Request kwargs + `BaseEndpoint` + `GracyRequestContext` | py `endpoints.py` (`api.request()` compat; one whitelist) | ☐ | — |
| 25 | `parsed_response` / `generated_parsed_response` | **DROPPED** — deprecated no-op shims; return annotations replace them | ✔ | n/a |
| 26 | `DEBUG_ENABLED` + introspection | py (`debug=True`, `api.plan.explain()`, `api.queue_stats()`) | ☐ | — |
| 27 | `ThrottleController` rate metrics feeding reports | rust `metrics.rs` (monotonic, `total_seconds()` math) | ☐ | — |

## New in v2 (need tests too)

| Feature | Status | Test |
|---|---|---|
| Internal priority queue (priorities, seq fairness, retry re-admission) | ☐ | — |
| Priority-aware backpressure (`max_pending`, `on_full=wait/raise`) | ☐ | — |
| Pause gates (`pause_on_status`, covers in-flight retries) | ☐ | — |
| Cancellation state machine (queued/Reserved/Sending rollback) | ☐ | — |
| Fork safety (unstarted clients fork-safe; started clients poisoned) | ☐ | — |
| Loop pinning (`GracyWrongLoopError`) + loop-death guard | ☐ | — |
| Sync facade (`Client.sync()`, async hooks work in sync mode) | ☐ | — |
| Scrub-on-record defaults | ☐ | — |
| Replay migration CLI (v1 pickle → schema v2) | ☐ | — |
| `gracy.testing` (retries_off / throttle_off / MockTransport) | ☐ | — |
| Property test: never > N requests in any trailing window W | ☐ | — |
| Wheel matrix install+import (8 targets + cp314t) | ☐ | — |

## v1 bugs — each needs a regression test asserting the v2 behavior

| # | Bug | Fixed by | Status |
|---|---|---|---|
| 1 | Throttle negative-wait when bursting over limit | exact sliding-window `next_allowed` | ☐ |
| 2 | Retry validates stale response after transport error | per-attempt response reset | ☐ |
| 3 | Namespace config double-merge / BASE_URL clobber / class mutation | single merge + descriptor | ☐ |
| 4 | Paginator hardcodes `page_size=20` | honored param | ☐ |
| 5 | SQLite `discard_replays_older_than` TypeError (str vs datetime) | unix-ms integers | ☐ |
| 6 | `plotly` extra not installable | declared extras + CI install test | ☐ |
| 7 | `logging.basicConfig` at import hijacks root logger | NullHandler | ☐ |
| 8 | Pickle replay storage (RCE + httpx version trap) | schema v2, zero pickle at runtime | ☐ |
| 9 | `readable_time_range` iterates a set (nondeterministic) | ordered rendering | ☐ |
| 10 | Hook requests still throttled/limited despite docs | `from_hook` bypass + explicit knob | ☐ |
| 11 | Backoff hooks claim to pause but don't | dispatcher pause gates | ☐ |
| 12 | Mongo batched writes silently lost (flush never called) | `aclose()` flushes | ☐ |
| 13 | `after` hook: raw exc on retries, wrapped on first attempt | always `GracyRequestFailed`-wrapped | ☐ |
| 14 | Printers mutate the report (double TOTAL row) | frozen report, pure printers | ☐ |
| 15 | `timedelta.seconds` truncation in req/s math | `total_seconds()` + monotonic | ☐ |
| 16 | Pickled custom exceptions lose subclass identity | fixed `__reduce__` | ☐ |
| 17 | Namespace string annotations fail cross-module | explicit descriptors | ☐ |
| 18 | README documents removed 2-method storage ABC | docs generated from protocol | ☐ |
| 19 | Report req/s regex from unescaped URL mis-matches | plan-id matching, no regex-over-URL | ☐ |
