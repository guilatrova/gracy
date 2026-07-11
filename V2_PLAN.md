# Gracy 2.0 — Rust Core Rewrite Plan

> **Status:** approved design, pre-implementation.
> **Target release:** `gracy 2.0.0` (current: 1.34.0).
> **Branch:** `v2-rust-rewrite`.

Gracy 2.0 rebuilds the request machinery as a **layered stack**: a small set of
orthogonal Rust primitives (a priority request queue that owns all throttling, a
reqwest-based transport, an hdrhistogram metrics sink, and a pickle-free SQLite
replay store) exposed via PyO3 as `gracy._core`, composed by a fully-typed
pure-Python facade where all user-facing policy lives.

Three principles drive everything below:

1. **The queue IS the throttle.** No request reaches the transport without a
   permit from the scheduler. Throttling, concurrency limits, priorities,
   pausing, and backpressure are all admission control on one component —
   not "sleep before send".
2. **Python callbacks never cross the FFI boundary in 2.0.** The per-request
   orchestration loop stays in Python; Rust is entered exactly twice per
   attempt (permit, send). Hooks/validators/parsers are ordinary awaited
   Python callables — zero GIL choreography, zero reentrancy hazards.
3. **Every stage is a protocol.** v2.0 ships orchestration in Python and hot
   primitives in Rust; 2.x can swap more stages into Rust (up to a full-Rust
   fast path when no Python callbacks are registered) without changing a
   single line of user code.

This design won a 3-proposal / 9-judge review (angles: max-Rust, layered,
DX-first; lenses: Rust systems engineer, Python maintainer, production user).
The layered proposal scored highest (55.8/70 avg); this plan is that proposal
plus the fixes the judges demanded (§6.4, §8) and the best ideas grafted from
the other two (compile-once plan, `pause_on_status` dispatcher gates,
`PARITY.md` release gate, `gracy.testing` switches, typed endpoint decorators).

---

## 1. Goals / non-goals

**Goals**

- Rust-grade per-request overhead and true parallelism for scheduling,
  throttling, transport, metrics, and replay I/O.
- An internal request **queue** as the single mechanism for throttling,
  concurrency, priorities, and backpressure — with exact sliding-window math
  (fixes v1's negative-wait bug by construction).
- Python-pluggable **hooks, validators, parsers, transports, and replay
  storages** — plain Python callables/protocols, no FFI knowledge required.
- Zero feature loss vs v1 (see `V2_PARITY.md` — release gate: every row needs
  a linked test) except deliberate drops listed in §13.
- All 19 known v1 bugs fixed by construction (§12).

**Non-goals for 2.0** (deferred to 2.x issues)

- HTTP/3, response caching, browser fingerprinting/impersonation, Prometheus
  export, trio support, the all-Rust fast path (Phase 6), typed lifecycle
  event stream.

---

## 2. Stack (verified July 2026)

| Piece | Choice | Notes |
|---|---|---|
| Bindings | **PyO3 0.29** (`abi3-py310`) | `gil_used = false`; do NOT build on `experimental-async` (feature-gated, asyncio-poll-only) |
| Async bridge | **pyo3-async-runtimes 0.29** (tokio) | `future_into_py` for every awaitable `_core` method |
| Runtime | **tokio 1.52** (rt-multi-thread, sync, time) | one process-global lazy runtime; threads = `min(4, cpus)`, `GRACY_CORE_THREADS` override |
| HTTP | **reqwest 0.13** | rustls with **ring** provider (aws-lc-rs breaks musllinux-aarch64/windows-arm64 builds); http2, gzip/brotli/zstd, cookies, socks, stream |
| Rate limiting | hand-rolled `VecDeque<Instant>` sliding window (default) + **governor 0.10** GCRA (`mode="smooth"` opt-in) | v1's contract is "max N in any trailing window W" — GCRA alone can't express it exactly |
| Queue | `BinaryHeap` + `tokio_util::time::DelayQueue` + keyed `tokio::sync::Semaphore` (dashmap) | see §6 |
| Metrics | **hdrhistogram 7.5** + atomics | per-worker, merged on snapshot |
| Replay | **rusqlite 0.40 `bundled`** | WAL, single-writer task, versioned schema, no pickle |
| Packaging | **maturin 1.14** mixed layout + maturin-action | abi3-py310 wheels (~8 targets) + cp314t builds; migrate to abi3t at Python 3.15 |
| Python floor | **3.10** | 3.9 is EOL |

License hygiene: rnet/wreq are GPLv3 — study architecture only, never vendor
code. primp (MIT-family) and ry are the reference implementations to read.

---

## 3. Repository layout

```
Cargo.toml                     # workspace
crates/
  gracy-core/                  # pure Rust, NO PyO3 — `cargo test`-able, policy-free
    src/
      plan.rs                  # CompiledPlan: serde structs received once from Python
      queue/{mod,throttle,concurrency,delay}.rs
      transport.rs             # reqwest wrapper: send(RequestSpec) -> ResponseData
      retry_timing.rs          # pure fn: (attempt, policy, status, retry_after) -> Duration
      replay/{mod,sqlite}.rs   # versioned cassette codec + rusqlite store
      metrics.rs
  gracy-py/                    # PyO3 bindings -> extension module `gracy._core`
    src/{lib,runtime,scheduler,transport,replay,metrics,convert}.rs
python/gracy/
  __init__.py                  # exports; NullHandler on "gracy" logger (no basicConfig!)
  client.py                    # Gracy, GracyNamespace, build/aclose lifecycle, sync facade
  endpoints.py                 # @get/@post/... decorators, Path/Query/Header/Body markers,
                               #   BaseEndpoint kept for api.request() compat
  config.py                    # GracyConfig, Retry, Backoff, Throttle, Rate, Concurrency,
                               #   Queue, Unset sentinel, ONE documented deep-merge
  plan.py                      # compile(): validate config, resolve precedence once,
                               #   emit JSON plan for _core; api.plan.explain()
  pipeline.py                  # per-request Python orchestrator (the executable spec)
  hooks.py                     # Hook protocol, HookResult, recursion guard,
                               #   RetryAfterBackoff / RateLimitBackoff built-ins
  validators.py                # Validator protocol, status policies (strict/allow)
  parsing.py                   # on={status: action}, raises()/returns(), Decoder protocol
                               #   + PydanticDecoder/MsgspecDecoder (extras)
  exceptions.py                # picklable hierarchy (subclass-preserving __reduce__)
  logging_events.py            # LogEvent/LogLevel, SafeDict, typed placeholder matrix
  paginator.py                 # GracyPaginator/GracyOffsetPaginator (page_size honored)
  replay/{__init__,storages,mongo,migrate}.py
  reports/{__init__,printers}.py
  transports.py                # Transport protocol; RustTransport, HttpxTransport,
                               #   MockTransport
  testing.py                   # retries_off(), throttle_off(), MockTransport helpers
  _core.pyi, py.typed
```

**Load-bearing decision:** `pipeline.py` (Python) orchestrates every attempt:
`permit = await scheduler.submit(spec)` (Rust) → hooks/validators/parsers
(Python) → `await permit.send(spec)` (Rust). The plan compiler records
`has_before_hooks / has_after_hooks / has_validators / has_custom_parser`
flags and specializes the pipeline into a list of present-only stage callables
— absent stages cost zero branches per request, and the flags double as the
Phase-6 fast-path eligibility signal.

---

## 4. The v2 interface

### 4.1 Declaring a client

```python
from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

import gracy
from gracy import (
    Gracy, GracyConfig, GracyNamespace,
    get, post, Path, Query, Body,
    Retry, Backoff, Throttle, Rate, Concurrency, Queue,
    LogEvent, LogLevel, raises,
)
from gracy.parsing import PydanticDecoder
from pydantic import BaseModel


class Pokemon(BaseModel):
    name: str
    order: int

class PokemonNotFound(gracy.GracyUserDefinedException):
    BASE_MESSAGE = "Unable to find [{NAME}] at {URL} due to {STATUS}"


class BerryNamespace(GracyNamespace):
    @get("/berry/{name}")
    async def get_one(self, name: Annotated[str, Path]) -> dict: ...


class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"
    timeout = 10.2                       # default 30s; timeout=None is an explicit opt-out

    config = GracyConfig(
        decoder=PydanticDecoder(),       # decodes bytes into return annotations
        log_errors=LogEvent(LogLevel.ERROR),   # user config now merges UNDER defaults
        retry=Retry(
            on=(gracy.status(429, 502, 503), TimeoutError),
            attempts=3,
            wait=Backoff(initial=1.0, multiplier=1.5, max=10.0, jitter=True),
            respect_retry_after=True,
            on_exhausted="raise",        # or "return"; v1 behavior="pass" split, see §13
        ),
        throttle=Throttle(rules=[
            Rate(10, per="1s", match=r".*/pokemon/.*"),
            Rate(600, per="1m"),         # burst + sustained; default match=".*"
        ]),
        queue=Queue(
            max_at_once=10, max_pending=5_000, on_full="wait",
            pause_on_status={429: "endpoint"},   # dispatcher-level lane pause (§6.3)
        ),
    )

    berry = BerryNamespace()             # explicit descriptor — no annotation scanning

    @get("/pokemon/{name}", on={HTTPStatus.NOT_FOUND: None}, retry=None)
    async def get_pokemon(self, name: Annotated[str, Path]) -> Pokemon | None: ...

    @get("/pokemon/{name}", on={HTTPStatus.NOT_FOUND: raises(PokemonNotFound)})
    async def get_pokemon_strict(self, name: Annotated[str, Path]) -> Pokemon: ...

    @get("/pokemon", concurrency=Concurrency(limit=2))
    async def list_pokemon(
        self, offset: Annotated[int, Query] = 0, limit: Annotated[int, Query] = 20,
    ) -> ResourceList: ...

    @post("/battle", status_policy=gracy.strict(HTTPStatus.CREATED))
    async def battle(self, req: Annotated[dict, Body]) -> dict: ...
```

The return annotation drives decoding; pyright sees real types with zero
casts. Endpoint stubs use `...` bodies — mypy users need
`disable_error_code = ["empty-body"]` (documented; a mypy plugin is a 2.x
candidate). Endpoint validation (unknown path params, `on=` statuses vs the
return union) runs at `build()` time, not class-definition time, so
`from __future__ import annotations` and forward refs work.

### 4.2 Using it — explicit lifecycle

```python
async def main() -> None:
    async with PokeAPI() as api:          # build -> compile plan -> start scheduler
        mew = await api.get_pokemon("mew")            # -> Pokemon | None
        berry = await api.berry.get_one("cheri")

        # ad-hoc escape hatch (v1 BaseEndpoint enums + endpoint_args still work)
        page = await api.request(
            "GET", "/pokemon/{NAME}", path={"NAME": "pikachu"},
            decode_as=Pokemon, priority=10,
        )

        api.report().print("rich")        # or "list" / "logger"
        fig = api.report().to_plotly()    # report is frozen; printers never mutate

# Sync facade: dedicated background thread runs a private loop hosting the
# real async client — sync and async share 100% of the pipeline, hooks included.
with PokeAPI.sync() as api:
    mew = api.get_pokemon("mew")
```

Per-call knobs (`priority=`, overrides) live on `api.request()` and
`api.options()` — NOT as magic kwargs on typed endpoint methods, which would
break their declared signatures under pyright.

### 4.3 Hooks, validators, scoped overrides

```python
class PokeAPI(Gracy):
    hooks = [RetryAfterBackoff(lock_per_endpoint=True)]   # ordered, first-class

    async def before(self, ctx: gracy.RequestContext) -> None:
        ctx.state["t0"] = time.monotonic()

    async def after(self, ctx, result: gracy.Response | Exception,
                    retry: gracy.RetryState | None) -> None:
        ...   # `result` exception is ALWAYS GracyRequestFailed-wrapped (consistent now)

class NoErrorField(gracy.Validator):
    def check(self, response: gracy.Response) -> None:
        if response.json().get("error"):
            raise MyDomainError(response)

# scoped override — replaces @graceful; contextvar semantics kept but explicit
async with api.options(retry=None, on={404: None}):
    await api.get_pokemon("x")            # applies to nested calls too

# URL-shaped overrides — replaces scattering @graceful across methods
config = GracyConfig(overrides={
    "*/pokemon/*": gracy.Override(throttle=Throttle(rules=[Rate(5, per="1s")])),
})
```

Requests issued inside hooks: skip hooks (recursion guard via contextvar),
bypass concurrency semaphores by default (kills v1's documented hook
deadlock; `from_hook=True` on the ticket), throttle-in-hooks is an explicit
`Queue(throttle_in_hooks=False)` knob — v1's README claim becomes true.

### 4.4 Replay

```python
from gracy.replay import Replay, SqliteStorage, Scrub

replay = Replay(
    mode="smart-replay",                  # "record" | "replay" | "smart-replay" | "off"
    storage=SqliteStorage("tests/pokeapi.db"),
    match_on=("method", "url", "body"),   # v1-compatible default; +query/headers opt-in
    scrub=Scrub(headers=["authorization"]),   # ON by default for common secrets
    discard_older_than=datetime(2026, 1, 1),  # works on SQLite now (unix-ms ints)
    disable_throttling=True,
)
async with PokeAPI(replay=replay) as api:   # aclose() flushes storage (Mongo batches survive)
    ...
```

### 4.5 Testing

```python
with gracy.testing.retries_off(), gracy.testing.throttle_off():
    async with PokeAPI(transport=gracy.testing.MockTransport(
        {"*/pokemon/*": {"name": "mew", "order": 1}},
    )) as api:
        assert (await api.get_pokemon("mew")).name == "mew"
```

`HttpxTransport` keeps respx/`httpx.MockTransport`/ASGI-transport workflows
alive (§7) — the testing ecosystem does not die with the default transport.

---

## 5. Config model

- Frozen dataclasses; `Unset` vs `None` distinction kept (`Unset` = inherit,
  `None` = explicitly disable).
- **One documented deep-merge and one precedence chain, resolved once at
  `build()`:** call `options()` > endpoint decorator > URL-glob override >
  namespace > client > library defaults. No per-request `merge_config`, no
  shared mutable config objects, no contextvar mutation bugs.
- User `config` merges UNDER library defaults, so partial configs keep
  default error logging (fixes v1's silent `DEFAULT_CONFIG` replacement).
- `plan.compile()` validates everything early (conflicting `status_policy`,
  unknown placeholders, bad regexes) and emits one JSON plan handed to
  `_core` at build — polars-style compile-once; `api.plan.explain()` prints
  the merged per-route plan.

---

## 6. The queue (Rust: `crates/gracy-core/src/queue/`)

### 6.1 Data structures

- **Priority queue:** `BinaryHeap<QueuedItem>` under `parking_lot::Mutex` +
  `tokio::sync::Notify`. `QueuedItem { priority: i32, seq: u64, spec,
  reply: oneshot::Sender<PermitGrant>, from_hook: bool, is_retry: bool }`.
  FIFO tie-break via monotonic `seq`; **retries keep their original `seq`**,
  so they outrank newer work of equal priority without a starvation knob.
- **Priority-aware backpressure (judge fix):** `max_pending` capacity is
  accounted on the heap mutex itself — no FIFO semaphore in front of the
  heap (that would queue priority-10 submits behind priority-0 under
  saturation). `on_full="wait"` waiters park in a priority-ordered wait list
  and are woken in priority order; `on_full="raise"` → `GracyQueueFull`.
- **Throttle rules (compiled once):** `Vec<CompiledRule { regex,
  windows: Vec<SlidingWindow> }>`. Default `SlidingWindow` =
  `VecDeque<Instant>` ring buffer: `next_allowed(now) = if len < max { now }
  else { front + window }` — the exact instant the oldest in-window request
  expires. Fixes v1's negative-wait (burst over limit) AND full-window
  over-wait (exactly at limit) bugs. Opt-in `mode="smooth"` uses governor
  GCRA (`Quota::with_period(W/N).allow_burst(N)`).
- **Delay wheel:** `tokio_util::time::DelayQueue` parks not-yet-allowed items;
  the dispatcher never busy-waits and wakes at exact instants. Retry backoff
  is a re-enqueue through the same wheel — no `sleep()` anywhere.
- **Concurrency limits:** `dashmap<ConcKey, Arc<Semaphore>>`, key =
  `(rule_id, uurl_or_global, blocking_arg_values…)` — v1
  `ConcurrentRequestLimit` semantics exactly, but scoped to the scheduler
  instance. **Idle-TTL eviction** on keyed entries (judge fix: high-cardinality
  `blocking_args` must not leak semaphores for the client's lifetime).

### 6.2 Admission ordering — no check-then-reserve race (judge fix)

The winning proposal's original sketch reserved window slots in a spawned
task *after* awaiting the concurrency semaphore — two items could both pass
a window check that includes neither. v2 admission is strictly ordered:

```
submit() ──► heap (priority + seq, capacity-checked)
dispatcher loop:
  1. pop max-priority ready item
  2. concurrency permit: try_acquire; if unavailable, park item on that key's
     waiter list (dispatcher moves on — no head-of-line blocking); the key's
     release wakes the item back into the ready set
  3. WITH permit held: throttle check + window reservation, done serially
     and atomically inside the dispatcher (single thread — no TOCTOU)
     - not allowed yet -> release nothing, park in DelayQueue until
       next_allowed (permit retained; see note), re-run step 3 on wake
  4. grant: reply.send(PermitGrant { permit, grant_instant })
```

Because reservation happens serially in the single dispatcher task *after*
the permit is held, admission can never over-commit a window, and a granted
permit is immediately usable — the grant→send gap is one FFI hop (§6.4).

### 6.3 Pause gates

`Queue(pause_on_status={429: "endpoint" | "client"})` and
`engine.pause(scope, until)` are **dispatcher gates**, not locks held by
requests (grafted from the max-Rust/DX proposals — structurally cannot
deadlock). A paused lane parks its ready items in the DelayQueue; **in-flight
retries also re-enter admission**, so they respect the pause (fixes the
"pause defeated by already-dispatched retries" judge finding). The built-in
`RetryAfterBackoff`/`RateLimitBackoff` hooks drive these gates — making v1's
"pauses ALL client requests" docstring true for the first time.

### 6.4 Hooks run BEFORE admission (judge fix)

v1 ordering was throttle → hooks → send, which in a queue world means window
slots get consumed at grant time while Python `before` hooks (and pause
gates) run between grant and wire — under a 429 pause, granted-but-paused
requests would fire as a burst that violates the rate limit exactly when the
server asked for mercy. v2 pipeline order per attempt:

```
before hooks (Python) ─► replay check ─► submit/admission (Rust: queue +
throttle reserve + concurrency) ─► permit.send (Rust) ─► after hooks ─►
validators ─► retry decision ─► [re-enter at before hooks] ─► decode/parse
```

Consequences, all deliberate:
- Token spend happens at the last possible instant before the wire — no
  grant-vs-wire divergence, no burst-after-pause.
- Replay hits **never spend throttle tokens** (checked before admission;
  also kills v1's double storage lookup for `disable_throttling`).
- A retry is a full re-admission: every attempt is throttled (v1 parity)
  and respects pauses.
- Slow hooks hold neither throttle tokens nor concurrency permits.

### 6.5 Two-phase permit & cancellation state machine (judge fix)

`submit()` resolves to a `Permit`; `permit.send(spec)` executes on
`CoreTransport`, stamps metrics, tees into replay storage when recording.
The grant carries an atomic state machine `Reserved → Sending → Done`;
`permit.send`'s first poll CASes `Reserved → Sending`. asyncio cancellation
aborts the tokio side (`JoinHandle::abort` wired through `future_into_py`):

- cancel while queued → item removed, nothing to roll back
- cancel in `Reserved` → CAS `Reserved → Cancelled` wins or loses against
  `Sending` atomically; on win: window reservation rolled back, permit and
  capacity released
- cancel in `Sending` → request aborted at the transport; tokens stay spent
  (the wire was touched)

When the plan has zero Python callbacks, 2.x collapses both hops into
`scheduler.execute(spec)` — the all-Rust fast path — with identical
observable behavior (Phase 6, gated on the differential suite).

### 6.6 Observability

`api.queue_stats()`: pending per priority band, in-flight count (v1
`ongoing_requests_count` parity), per-rule throttle-wait histograms and hit
counts, grant latency p50/p95, active pauses. Feeds the report's Throttles
column and the 3am-debugging surface the judges asked for.

---

## 7. Transports & the testing ecosystem (judge fix)

`Transport` is a Python protocol: `async def send(spec) -> Response`.

- **`RustTransport`** (default): `CoreTransport` / reqwest. Configured via
  `TransportConfig` (headers, proxy, TLS, timeouts, http2, redirects).
- **`HttpxTransport`** (escape hatch, ships in 2.0): full httpx client under
  the hood — custom SSLContext/mTLS, UDS, `httpx.Auth`, ASGI/WSGI transports,
  respx/pytest-httpx mocking all keep working. Swapping transport does NOT
  bypass the queue: the Rust permit is granted first, then the Python
  transport sends — full queue/retry/replay treatment either way.
- **`MockTransport`** (in `gracy.testing`): pattern → canned response.

This is the answer to "dropping httpx severs the testing ecosystem": the
default is Rust, the protocol keeps every httpx-based workflow one line away.

---

## 8. Async model & lifecycle

- **Runtime:** process-global lazy tokio runtime, initialized on first
  `build()` — **never at import**. Module-level client *instances* are
  fork-safe because unstarted clients own no runtime state.
- **Fork safety (judge fix):** `os.register_at_fork` handler clears the
  runtime handle in the child and poisons *started* clients with a clear
  `GracyForkedClientError`; new/unstarted clients lazily create a fresh
  runtime in the child. gunicorn/celery prefork guidance in docs: build
  clients per-worker (post-fork), which the explicit lifecycle makes natural.
- **Loop pinning:** `build()` captures the running loop; cross-loop use
  raises `GracyWrongLoopError` instead of silently hanging.
- **Loop-death guard (judge fix):** all completions route through one guarded
  waker that catches `RuntimeError` from `call_soon_threadsafe` on a closed
  loop and resolves/abandons cleanly — no tokio task ever hangs on a dead
  loop (pytest teardown, `asyncio.run()` return, Ctrl-C).
- **Shutdown:** `aclose()` cancels the dispatcher, drains in-flight permits,
  flushes replay storage, closes the reqwest client. Finalizer warns
  (aiohttp-style) on unclosed clients. CI runs create/destroy-loop and
  cancel-storm stress tests.
- **Logging is lossless:** log emission happens synchronously in the Python
  pipeline (no lossy broadcast channel).
- **Sync facade:** background thread + private loop hosting the real async
  client; `asyncio.run_coroutine_threadsafe(...).result()`. Sync and async
  share the pipeline — async hooks work in sync mode (the private loop runs
  them). trio is out of scope for 2.0.
- **Free-threading:** `gil_used = false`; abi3-py310 wheels + explicit cp314t
  builds; abi3t when Python 3.15 lands.

---

## 9. Replay v2

- **Schema v2, no pickle:** SQLite `gracy_recordings_v2(key_url, method,
  match_hash, request_headers_json, request_body BLOB, status,
  response_headers_json, response_body BLOB, http_version, elapsed_ms,
  recorded_at INT unix-ms, schema_version)`. Diffable, inspectable, readable
  from Rust and Python, nothing executes on load. Kills the pickle RCE
  surface and the httpx-version lock-in.
- Modes kept: `record` / `replay` / `smart-replay` / `off`. Default
  `match_on=("method","url","body")` reproduces v1's key; the SAME request
  spec drives live requests and match-hash computation (closes v1's
  whitelist divergence).
- `Scrub` on by default: `authorization`, `cookie`, `x-api-key`.
- `SqliteStorage` wraps the Rust store (WAL, single-writer). Mongo stays pure
  Python behind the async `ReplayStorage` protocol
  (`prepare/record/find/load/flush`); pymongo becomes a real extra with an
  eager, instructive `ImportError`. `aclose()` always flushes.
- **Migration:** `python -m gracy.replay.migrate old.sqlite3 new.db` —
  separate opt-in CLI, restricted unpickler, loud "only run on DBs you trust"
  warning. v2 runtime imports zero pickle.
- Replayed responses carry `is_replay=True`, feed the `{REPLAY}` placeholders
  and Replays report column, and no-op the backoff hooks (v1 parity).

---

## 10. Reports v2

- `CoreMetrics` (Rust): per-uurl counters (total, 2xx/3xx/4xx/5xx, aborts,
  retries, throttles, replays) + hdrhistogram latencies; monotonic +
  `total_seconds()` math.
- `api.report()` returns a **frozen** `GracyReport` from `snapshot()` —
  printers are pure functions (double-print bug dies), TOTAL row computed in
  the renderer. Columns match v1 + free p95/p99.
- **Per-instance scope** (class-level global state removed);
  `Gracy.shared_metrics(group=...)` opts into cross-client aggregation;
  `api.reset_metrics()` replaces `dangerously_reset_report()`.
- `success_when=` configurable (default counts 2xx + allowed/parsed statuses;
  v1 counted only 2xx — dashboards will shift, documented).
- `rich` and `plotly` extras actually declared and install-tested in CI.

---

## 11. Everything that stays Python (policy layer)

Validators (same sync `check()` protocol), per-status `on=` actions
(callable | `raises(Exc)` | literal — v1 semantics preserved, including
"parse runs last, even on failed responses when suppressed"), decoders
(pydantic/msgspec extras; core stays dependency-free), LogEvent templating
with the full typed placeholder matrix, the exception hierarchy (picklable,
subclass-preserving), paginators (`page_size` honored, typed generics),
namespaces (explicit descriptor with per-instance `__get__` binding — no
class-attribute state bleed, nesting and cross-module declarations work),
`api.options()` scoped overrides, and the whole `pipeline.py` orchestrator.

---

## 12. v1 bugs fixed by construction (parity gate: each gets a test)

1. Throttle negative-wait when over limit → exact sliding-window `next_allowed`.
2. Retry validates stale response after transport error → response reset per attempt.
3. Namespace config double-merge / BASE_URL clobber / class mutation → one merge, descriptor binding.
4. Paginator hardcoded `page_size=20` → honored.
5. SQLite `discard_replays_older_than` TypeError → unix-ms integers.
6. `plotly` extra not installable → declared extras, CI install test.
7. `logging.basicConfig` at import → NullHandler.
8. Pickle replay storage (RCE + version trap) → schema v2, no pickle at runtime.
9. `readable_time_range` iterates a set → ordered rendering.
10. Hook requests still throttled/limited (doc mismatch) → `from_hook` bypass + explicit knob.
11. Backoff hooks don't actually pause → dispatcher pause gates.
12. Mongo batched writes lost (no close) → `aclose()` flushes, protocol has `flush`.
13. `after` hook sees raw exc on retries, wrapped on first attempt → always wrapped.
14. Printers mutate the report → frozen report, pure printers.
15. `timedelta.seconds` truncation in req/s → `total_seconds()` + monotonic.
16. Pickled custom exceptions lose subclass → fixed `__reduce__`.
17. Namespace string-annotation resolution fails cross-module → explicit descriptors.
18. README documents removed 2-method storage ABC → docs generated from the protocol.
19. Report req/s regex built from unescaped URL → match on compiled plan ids, not regex-over-URL.

---

## 13. Breaking changes (each with a migration path in the cookbook)

- Python floor **3.10**; compiled wheels only (sdist needs a Rust toolchain).
- **Explicit lifecycle:** `async with Client()` / `await client.build()` +
  `aclose()`; unclosed clients warn. Sync facade added in exchange.
- **Class-level global state removed:** metrics/throttle per instance;
  sharing is opt-in.
- `self.get[T](endpoint, args)` → `@get(...)` decorated endpoints;
  `api.request(..., decode_as=T)` for ad-hoc calls;
  `parsed_response`/`generated_parsed_response` deleted (were no-op shims).
- `parser={...}` → `on={...}` + return annotations; `raises(Exc)` replaces
  bare exception classes.
- `@graceful` / `@graceful_generator` → endpoint-decorator kwargs +
  `api.options()` + URL-glob `overrides=`.
- `strict_status_code`/`allowed_status_code` → single `status_policy=`;
  combining both is a build-time error (was silent precedence).
- `GracefulRetry.behavior="pass"` split into `on_exhausted="return"` vs
  `suppress=True` — pick which you meant.
- Replay format v2 (no pickle) + async 5-method storage protocol; one-shot
  migration CLI.
- Namespaces declared explicitly (`berry = BerryNamespace()`); bare
  annotations raise a helpful build-time error.
- Unset `REQUEST_TIMEOUT` no longer disables timeouts — default 30s;
  `timeout=None` is explicit.
- `_create_client()` override → `TransportConfig` / `Transport` protocol
  (`HttpxTransport` for full httpx control); unknown request kwargs fail
  eagerly.
- `after` hook contract normalized; hook requests bypass concurrency by default.
- Import-time `logging.basicConfig` removed.
- Success-rate definition in reports changed (configurable).
- **Hook ordering: `before` hooks now run BEFORE throttling** (v1: after).
  Deliberate — see §6.4. Hooks that relied on running post-throttle must move
  to a queue gate or the `after` side.

A `gracy.v1compat` shim maps `GracyConfig(parser=...)`/`@graceful` onto the
new plan with deprecation warnings for the alpha/beta cycle only.

---

## 14. Implementation phases (each independently shippable)

Estimates are optimistic single-focus weeks; judges flagged scope realism as
the #1 project risk — the phase gates are the mitigation, and every phase
ships value even if later phases slip.

**Phase 0 — Skeleton & CI.** maturin mixed layout, workspace, hello-world
`_core`, abi3-py310 + cp314t wheel matrix on maturin-action, drop py3.8/3.9.
Release tooling: replace python-semantic-release v7 (incompatible with
compiled wheels) with git-cliff changelog + tag-triggered maturin-action
publish. *Exit:* wheels install and import on all 8 targets.

**Phase 1 — Python facade, pure-Python engine (the executable spec).**
`endpoints/config/plan/pipeline/hooks/validators/parsing/exceptions/logging/paginator`
on top of `HttpxTransport` and a Python `Scheduler` implementing the same
protocol the Rust one will. Port the v1 test suite as the parity oracle
(fixed-bug tests inverted and annotated); property tests (hypothesis) on
merge/precedence. *Exit:* behavioral parity suite green; `V2_PARITY.md` rows
all linked; pyright strict passes; docs example runs.

**Phase 2 — Rust queue (`CoreScheduler`).** Sliding window, GCRA mode,
concurrency semaphores + TTL eviction, priorities, backpressure, DelayQueue,
pause gates, stats. Swap behind the protocol. *Exit:* parity suite green with
the flag flipped; `cargo test` property tests ("never > N in any trailing W
under random schedules", loom on the window math); timing tests
(burst-over-limit, exact wake-up, per-attempt throttling, hook bypass,
pause-covers-retries); differential runs vs the Python reference scheduler.

**Phase 3 — Rust transport (reqwest).** `RustTransport` default,
`HttpxTransport` kept. *Exit:* parity suite green on BOTH transports (CI
matrix); differential tests against a local httpbin-style server (redirects,
proxies, compression, streaming, timeouts, TLS errors → exception taxonomy).
**→ ship `2.0.0a1`** (call for testers).

**Phase 4 — Rust replay + migration tool.** Schema v2, scrubbing, Mongo
protocol port, `gracy.replay.migrate`. *Exit:* record→replay round-trips on
both storages; the repo's own v1 fixture DB migrates and replays; cargo-fuzz
on the cassette codec.

**Phase 5 — Rust metrics + reports.** *Exit:* report matches hand-computed
fixtures; double-print identical; extras install-tested.
**→ ship `2.0.0b1`**, then **`2.0.0`** when the migration guide + cookbook
(before/after for every inventory feature) are done. v1 gets security-only
maintenance for 12 months.

**Phase 6 (2.x) — Rust fast path.** `scheduler.execute()` for callback-free
plans; `Py<PyAny>` hook slots + `into_future` bridging (per-submit
TaskLocals, `spawn_blocking` for sync callables, never blocking a worker
while attached) for the rest. *Exit:* parity suite green on both pipelines;
benchmark gate: no-callback path within 1.5x of raw reqwest-in-Rust; "<2%
regression vs feature-absent" CI gate for each hook slot.

**Testing strategy overall:** the Phase-1 Python engine is the reference
implementation, retained for differential testing (scheduler + pipeline
semantics). CI runs the full suite × {python-scheduler, rust-scheduler} ×
{httpx, reqwest} until 2.1, then rust-only default with a weekly full-matrix
job. Rust unit tests own timing math; the Python suite owns semantics.

---

## 15. Risk register

| Risk | Mitigation |
|---|---|
| tokio↔asyncio bridge edges (loop shutdown, cancellation, fork) | Loop pinning + `GracyWrongLoopError`; guarded waker; `register_at_fork` poisoning + lazy-per-process runtime; cancel-storm and loop-churn stress tests in CI |
| reqwest ≠ httpx behavior drift | Phase-3 differential matrix vs local server; `HttpxTransport` one-line fallback keeps queue/retry/replay |
| Queue math bugs worse than v1's | Pure-Rust window math with property tests + loom; Python reference scheduler differential runs; governor GCRA as battle-tested alternate mode |
| Solo-maintainer scope (judges' #1) | Strict phase gates, each phase shippable; Rust crates small (~3–4 kLOC), policy-free, API-frozen; all policy/extension surface in Python; `gracy-core` has no PyO3 (plain `cargo test`) |
| Wheel/ABI matrix pain | abi3-py310 + cp314t now, abi3t at 3.15; rustls-ring avoids aws-lc-rs build failures on musllinux-aarch64/windows-arm64; sdist verified in a clean container |
| Replay migration security | Migration is opt-in CLI with restricted unpickler + trust warning; runtime has zero pickle |
| API redesign alienates v1 users | `api.request()` keeps BaseEndpoint enums; `gracy.v1compat` shim through beta; mechanical cookbook for every feature |
| mypy `empty-body` on endpoint stubs | Documented config (`disable_error_code = ["empty-body"]`); mypy plugin as 2.x candidate |
| Dual-pipeline maintenance forever | Differential matrix demoted to weekly job at 2.1; Phase 6 gated on it staying green |

---

## 16. Drop-in compat adapters + documentation (added scope)

### 16.1 `gracy.compat` — one-line swap from requests/httpx

Goal: a user replaces `import requests` with `from gracy.compat import requests`
(or `requests = gracy.compat.requests`) and their code keeps working, duck-typed,
now running through the full gracy pipeline (retry/throttle/queue/reports).

- **`gracy.compat.requests`** (sync duck-type of the `requests` top-level API):
  `get/post/put/patch/delete/head/options(url, params=, headers=, json=, data=,
  timeout=, **kw)` returning a requests-shaped `CompatResponse`
  (`.status_code`, `.ok`, `.text`, `.content`, `.json()`, `.headers`
  case-insensitive dict, `.url`, `.elapsed`, `.raise_for_status()` raising an
  `HTTPError`-shaped exception). A `Session()` class with persistent headers
  and the same verbs. Backed by a lazily-started module-level Gracy sync
  facade; `gracy.compat.requests.configure(GracyConfig(...))` upgrades the
  drop-in with retry/throttle policies without touching call sites.
- **`gracy.compat.httpx`** (async duck-type): `AsyncClient(base_url=,
  headers=, timeout=)` with `get/post/...` coroutines and httpx-shaped
  responses; `Client` sync twin. Same `configure()` hook.
- Both adapters are pure sugar over the public Gracy API — no pipeline forks.
- Tests: mirror idiomatic requests/httpx snippets (params/json/data/headers/
  timeout/raise_for_status/session reuse) against the local test server, plus
  "the swap line" itself (`requests = gracy.compat.requests`).

### 16.2 Documentation deliverables (release gate additions)

- **README.md rewritten for v2**: Rust-core pitch, quickstart (async + sync),
  feature tour matching the real v2 API, compat one-liner section, dev setup
  (maturin/uv), badges updated.
- **MIGRATING.md**: (a) v1 → v2 cookbook — one before/after block for every
  breaking change in §13; (b) "coming from requests" and "coming from httpx"
  — the one-line swap, then gradual adoption (declared endpoints, configs);
  (c) replay DB migration walkthrough (`python -m gracy.replay.migrate`).

## 17. Immediate next steps

1. Phase 0 scaffold: `Cargo.toml` workspace, `crates/gracy-core`,
   `crates/gracy-py`, `python/gracy/`, maturin config in `pyproject.toml`.
2. CI: maturin-action wheel matrix + `cargo test` + pytest jobs; kill
   python-semantic-release v7.
3. Port `V2_PARITY.md` into tracking issues (one per feature row).
4. Phase 1: build the executable spec.
