# Migrating to Gracy 2.0

## gracy 1.x → 2.0

Gracy 2.0 is a ground-up rewrite of the request machinery:

- **Rust core.** Scheduling, throttling, concurrency limits, the HTTP
  transport (reqwest), metrics aggregation, and the replay store are compiled
  Rust (`gracy._core`), driven by a fully-typed pure-Python facade. Your
  hooks, validators, and parsers stay ordinary Python callables.
- **The queue IS the throttle.** No request reaches the wire without a permit
  from the scheduler. Throttling, concurrency limits, priorities, pauses, and
  backpressure are all admission control on one component - not "sleep before
  send". Backoff hooks that used to *claim* to pause now genuinely pause the
  queue.
- **Per-instance state.** v1 kept metrics and throttle state on **class**
  attributes, so every client of the same class (and every test) shared and
  leaked state. In v2 each built instance owns its own scheduler, metrics,
  and transport. Sharing is opt-in (inject the same `scheduler=`/`transport=`).
- **Explicit lifecycle.** Clients are inert until built: use
  `async with MyApi() as api:` (or `await api.build()` / `await api.aclose()`).
  In exchange there is a first-class **sync facade**: `with MyApi.sync() as api:`.
- **Replay is pickle-free.** v1 cassettes stored pickled `httpx.Response`
  objects (arbitrary-code-execution risk + httpx version trap). v2 uses a
  plain-bytes SQLite schema, with a one-shot migration CLI for old databases.
- **New in 2.0: live monitor.** Pass `Gracy(monitor=True)` (or set
  `GRACY_MONITOR=1`) and every built client streams lightweight snapshots to a
  spool file; `python -m gracy.monitor` (or `gracy-monitor`) renders them as a
  live terminal dashboard - queue depth, in-flight, throttles, pauses, retries
  and per-endpoint stats, across all running processes. Zero overhead when off.

Requirements and install:

- Python **3.10+** (v1 supported 3.8).
- Pre-release install: `pip install --pre gracy` (2.0 ships as `2.0.0a0` first).
- Compiled wheels are published for CPython on the usual platforms; installing
  from the **sdist requires a Rust toolchain** (see FAQ).
- httpx is now an optional extra: `pip install --pre 'gracy[httpx]'` if you
  use `HttpxTransport` or the replay migration CLI.

---

## Quick reference: v1 symbol → v2 symbol

| v1 | v2 |
|---|---|
| `Gracy[Endpoint]` (generic base) | `Gracy` (no generic parameter) |
| nested `class Config` | class attributes on the client class |
| `Config.BASE_URL` | `base_url = "..."` class attr |
| `Config.REQUEST_TIMEOUT` | `timeout = 5.0` class attr (unset now means **30s**, not "no timeout") |
| `Config.SETTINGS = GracyConfig(...)` | `config = GracyConfig(...)` class attr |
| instantiate-and-go (implicit lifecycle) | `async with MyApi() as api:` / `await api.build()` + `await api.aclose()` |
| - (no sync support) | `with MyApi.sync() as api:` blocking facade |
| `self.get[T]("/x/{ID}", {"ID": ...})` (and `post`/`put`/…) | `@get("/x/{id}")`-decorated endpoint stubs |
| ad-hoc `self._request(...)` | `await api.request("GET", endpoint, {...}, decode_as=T)` |
| `BaseEndpoint` (str enum) | `BaseEndpoint` - unchanged, works with `api.request()` |
| `@graceful(...)` / `@graceful_generator(...)` | endpoint-decorator kwargs, `api.options(...)`, URL-glob `overrides=` |
| `GracyConfig(parser={...})` | `GracyConfig(on={...})` (also per-endpoint `@get(..., on={...})`) |
| parser value: bare exception class | `raises(MyError)` (bare classes are a build-time error) |
| parser key: `HTTPStatus.NOT_FOUND` | plain int `404` (or `"default"`) |
| `GracyConfig(strict_status_code=...)` | `GracyConfig(status_policy=strict(...))` |
| `GracyConfig(allowed_status_code=...)` | `GracyConfig(status_policy=allow(...))` |
| both strict + allowed set (silent precedence) | build-time `GracyConfigError` |
| `GracefulRetry` | `Retry` |
| `GracefulRetry.max_attempts` | `Retry.attempts` |
| `GracefulRetry.delay` / `delay_modifier` | `Retry.wait` (float or `Backoff(initial=, multiplier=, max=, jitter=)`) |
| `GracefulRetry.retry_on={429, 503}` | `Retry(on=status(429, 503))`; exceptions: `Retry(on=(status(503), TimeoutError))` |
| `GracefulRetry.retry_on=None` (retry any failure) | `Retry(on=(Exception,))` |
| `GracefulRetry.overrides={404: OverrideRetryOn(delay=10)}` | `Retry(overrides={404: 10.0})` |
| `GracefulRetry.behavior="pass"` | `Retry(on_exhausted="return")` **or** `Retry(suppress=True)` - see cookbook |
| `GracefulRetryState` | `RetryState` (same fields, handed to after-hooks/log placeholders) |
| `GracyConfig(throttling=...)` | `GracyConfig(throttle=...)` |
| `GracefulThrottle` | `Throttle` |
| `ThrottleRule(url_pattern, max_requests, per_time_range)` | `Rate(limit=, per="1s", match=r"...")` |
| `GracyConfig(concurrent_requests=...)` | `GracyConfig(concurrency=...)` (bare int still works) |
| `ConcurrentRequestLimit` | `Concurrency` |
| `ConcurrentRequestLimit.uurl_pattern` | `Concurrency.match` |
| `ConcurrentRequestLimit.blocking_args` | `Concurrency.key_by` |
| `ConcurrentRequestLimit.limit_per_uurl` (default `True`) | `Concurrency.per_uurl` (default `False` - flipped!) |
| `GracefulValidator` (ABC, `check(httpx.Response)`) | `Validator` (same sync `check()`, takes a gracy `Response`) |
| `LogEvent` / `LogLevel` | same names; `custom_message` is str-only (callables dropped) |
| `GracyRequestContext` | `RequestContext` |
| `self.before` / `self.after` overrides | unchanged concept; also `hooks = [...]` class attr; `after` result is always `GracyRequestFailed`-wrapped |
| `common_hooks.HttpHeaderRetryAfterBackOffHook` | `RetryAfterBackoff` (registered via `hooks = [...]`; actually pauses the queue) |
| `common_hooks.RateLimitBackOffHook` | `RateLimitBackoff` |
| `common_hooks.HookResult` | `HookResult` (compat shim; return values are ignored - pauses are scheduler gates) |
| `_create_client()` override | `transport=HttpxTransport(client=...)` / `TransportConfig` / `Transport` protocol |
| unknown request kwargs silently passed to httpx | unknown kwargs fail eagerly |
| `GracyReplay` | `Replay` |
| `GracyReplay.discard_replays_older_than` | `Replay(discard_older_than=...)` |
| `SQLiteReplayStorage` | `SqliteStorage(path)` (schema v2, no pickle) |
| `MongoReplayStorage` | `gracy.replay.mongo.MongoReplayStorage` (needs the `mongo` extra; flushed by `aclose()`) |
| `GracyReplayStorage` (ABC) | `ReplayStorage` async protocol (`prepare/record/find/flush`) |
| v1 pickle replay DB | `python -m gracy.replay.migrate` one-shot CLI |
| `api.get_report()` | `api.report()` (frozen snapshot) |
| `api.report_status(printer)` | `api.report().print("rich" \| "list" \| "logger" \| "plotly")` |
| `Gracy.dangerously_reset_report()` (classmethod, global) | `api.reset_metrics()` (per instance) |
| report success = 2xx only | success = **2xx + 3xx** (documented change; dashboards will shift) |
| `parsed_response` / `generated_parsed_response` | **deleted** (were no-op shims) - use return annotations |
| namespace via bare annotation (`berry: BerryNamespace`) | explicit instance (`berry = BerryNamespace()`) |
| `DEBUG_ENABLED=True` | `Gracy(debug=True)`; also `api.queue_stats()` |
| `ongoing_requests_count` | `api.queue_stats()["in_flight"]` |
| import-time `logging.basicConfig(...)` | removed - gracy installs a `NullHandler`; configure logging yourself |
| `GracyPaginator(gracy_func, has_next, page_size=20)` | same class; `page_size` is now actually honored |

---

## Cookbook: every breaking change, before and after

### 1. Class shape: nested `Config` → class attributes

v2 detects a leftover nested `class Config` and raises `GracyConfigError`
with a pointer here, so you cannot silently run with dead config.

**v1**

```python
from gracy import Gracy, GracyConfig, LogEvent, LogLevel

class PokeAPI(Gracy[str]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2"
        REQUEST_TIMEOUT = 5.0
        SETTINGS = GracyConfig(log_errors=LogEvent(LogLevel.ERROR))
```

**v2**

```python
from gracy import Gracy, GracyConfig, LogEvent, LogLevel

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"
    timeout = 5.0
    config = GracyConfig(log_errors=LogEvent(LogLevel.ERROR))
```

Note the generic parameter is gone: `Gracy[str]` / `Gracy[MyEndpointEnum]` is
now just `Gracy`.

### 2. Lifecycle: explicit `async with` (and the sync facade)

Clients own **no** runtime state until built. An unbuilt or closed client
raises `GracyClientClosedError` on use; unclosed built clients warn.

**v1**

```python
async def main() -> None:
    api = PokeAPI()
    pokemon = await api.get_pokemon("pikachu")
    # nothing to close; state lived on class attributes
```

**v2 (async)**

```python
import asyncio

async def main() -> None:
    async with PokeAPI() as api:
        pokemon = await api.get_pokemon("pikachu")

    # or, without a context manager:
    api = PokeAPI()
    await api.build()
    try:
        pokemon = await api.get_pokemon("pikachu")
    finally:
        await api.aclose()

asyncio.run(main())
```

**v2 (sync - new)**

```python
def main() -> None:
    with PokeAPI.sync() as api:
        pokemon = api.get_pokemon("pikachu")  # no await; same pipeline underneath
```

The sync facade runs the real async client on a private daemon-thread event
loop - hooks, retry, throttle, and replay behave identically.

### 3. Typed methods: `self.get[T](endpoint, args)` → `@get` decorators

`parsed_response` / `generated_parsed_response` are deleted (they were no-op
shims); the endpoint's **return annotation** is the parse instruction now.

**v1**

```python
from gracy import Gracy, BaseEndpoint

class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"

class PokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2"

    async def get_pokemon(self, name: str):
        return await self.get[dict](PokeApiEndpoint.GET_POKEMON, {"NAME": name})
```

**v2**

```python
from gracy import Gracy, get, post

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    @get("/pokemon/{name}")
    async def get_pokemon(self, name: str) -> dict: ...

    @post("/pokemon")
    async def create_pokemon(self, body: dict) -> dict: ...
```

The stub body is literally `...` - gracy compiles the route at `build()`.
Parameters map onto the URL automatically: names matching a `{placeholder}`
become path args, everything else becomes a query param. Use
`Annotated[..., Path/Query/Header/Body]` markers to be explicit:

```python
import typing as t
from gracy import Gracy, get, Query, Header

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    @get("/pokemon")
    async def list_pokemon(
        self,
        limit: t.Annotated[int, Query] = 20,
        trace_id: t.Annotated[str, Header] = "",
    ) -> dict: ...
```

**Escape hatch:** ad-hoc calls (with the full queue/retry/replay treatment)
go through `api.request()`, and your v1 `BaseEndpoint` enums still work there:

```python
from gracy import Gracy, BaseEndpoint

class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

async def fetch(api: PokeAPI) -> dict:
    return await api.request(
        "GET", PokeApiEndpoint.GET_POKEMON, {"NAME": "pikachu"}, decode_as=dict
    )
```

### 4. `parser={...}` → `on={...}` + `raises()`

Keys are plain ints (or `"default"`), and bare exception classes are rejected
at build time - wrap them in `raises()` so intent is explicit.

**v1**

```python
from http import HTTPStatus
from gracy import GracyConfig
from gracy.exceptions import GracyUserDefinedException

class PokemonNotFound(GracyUserDefinedException):
    pass

config = GracyConfig(
    parser={
        "default": lambda r: r.json(),
        HTTPStatus.NOT_FOUND: None,
        HTTPStatus.INTERNAL_SERVER_ERROR: PokemonNotFound,
    }
)
```

**v2**

```python
from gracy import GracyConfig, raises
from gracy.exceptions import GracyUserDefinedException

class PokemonNotFound(GracyUserDefinedException):
    pass

config = GracyConfig(
    on={
        "default": lambda r: r.json(),
        404: None,                       # literal: the call returns None
        500: raises(PokemonNotFound),    # bare PokemonNotFound would be a build error
    }
)
```

And the most common v1 parser - `"default": lambda r: r.json()` - usually
disappears entirely: annotate the endpoint `-> dict` and gracy decodes for
you (see also `PydanticDecoder` / `MsgspecDecoder` via `GracyConfig(decoder=...)`).

### 5. `@graceful` → endpoint kwargs + `api.options()` + URL globs

**v1**

```python
from http import HTTPStatus
from gracy import Gracy, GracefulRetry, graceful

class PokeAPI(Gracy[str]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2"

    @graceful(
        strict_status_code=HTTPStatus.OK,
        retry=GracefulRetry(delay=1, max_attempts=3),
    )
    async def get_pokemon(self, name: str):
        return await self.get("/pokemon/{NAME}", {"NAME": name})
```

**v2 - the config moves onto the endpoint decorator:**

```python
from gracy import Gracy, Retry, get, status, strict

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    @get(
        "/pokemon/{name}",
        status_policy=strict(200),
        retry=Retry(on=status(500, 503), attempts=3, wait=1.0),
    )
    async def get_pokemon(self, name: str) -> dict: ...
```

**v2 - per-call scoped overrides (`api.options()`):** for the v1 pattern of
wrapping arbitrary call sites with `@graceful`, use the context manager -
works as `with` and `async with`, nests, and layers on top of everything else:

```python
async def bulk_import(api: PokeAPI) -> None:
    with api.options(retry=None, priority=10):
        await api.get_pokemon("pikachu")
```

Scheduler-side knobs (`throttle`, `concurrency`, `queue`) are compiled at
`build()` and rejected by `options()` - declare those on the client/endpoint.

**v2 - URL-glob overrides:** config that applies to a URL subset lives in
`overrides=` on any config layer:

```python
from gracy import GracyConfig, Retry, status

config = GracyConfig(
    overrides={
        "*/pokemon/*": GracyConfig(retry=Retry(on=status(429), attempts=5)),
    }
)
```

`@graceful_generator` has no direct successor: declare the endpoint with
`@get(...)` and wrap it in your own async generator, or use `GracyPaginator`.

### 6. `strict_status_code` / `allowed_status_code` → `status_policy`

Setting both in v1 silently let strict win. In v2 there is one field, and a
config carrying both concepts is impossible by construction.

**v1**

```python
from http import HTTPStatus
from gracy import GracyConfig

strict_cfg = GracyConfig(strict_status_code=HTTPStatus.CREATED)
allow_cfg = GracyConfig(allowed_status_code={HTTPStatus.NOT_FOUND})
```

**v2**

```python
from gracy import GracyConfig, allow, strict

strict_cfg = GracyConfig(status_policy=strict(201))   # ONLY 201 passes (even 200 fails)
allow_cfg = GracyConfig(status_policy=allow(404))     # 2xx OR 404 pass
```

### 7. `GracefulRetry` → `Retry`

**v1**

```python
from gracy import GracefulRetry, OverrideRetryOn

retry = GracefulRetry(
    delay=1.0,
    max_attempts=3,
    delay_modifier=2.0,
    retry_on={429, 503},
    overrides={404: OverrideRetryOn(delay=10.0)},
    behavior="break",
)
```

**v2**

```python
from gracy import Backoff, Retry, status

retry = Retry(
    on=status(429, 503),                       # required; also takes exception classes
    attempts=3,
    wait=Backoff(initial=1.0, multiplier=2.0), # plain float works for a fixed delay
    overrides={404: 10.0},                     # per-status delay, plain seconds
)
```

Notes:

- `on=` is **required**. v1's `retry_on=None` ("retry any failure") becomes
  `Retry(on=(Exception,))`. Mixed matching: `Retry(on=(status(503), TimeoutError))`.
- `Backoff` also supports `max=` (delay cap) and `jitter=True`.
- New: `respect_retry_after=True` by default - a parseable `Retry-After`
  header wins over the computed wait.

**`behavior="pass"` split - pick which one you meant:**

- `Retry(on_exhausted="return")` - retries run; if they exhaust, the last
  response is **returned** instead of raising. Transport errors and other
  exceptions still raise. Pick this if you used `behavior="pass"` to inspect
  the final failed response yourself.
- `Retry(suppress=True)` - never raise, even for mid-retry failures and
  transport errors with no response at all (the call returns the response, or
  `None` when there is none). Pick this if your v1 code relied on gracy never
  throwing from a `behavior="pass"` endpoint.

```python
from gracy import Retry, status

inspect_failures = Retry(on=status(500), attempts=3, on_exhausted="return")
never_raise = Retry(on=status(500), attempts=3, suppress=True)
```

### 8. `GracefulThrottle` / `ThrottleRule` → `Throttle` / `Rate`

**v1**

```python
from datetime import timedelta
from gracy import GracyConfig, GracefulThrottle, ThrottleRule

config = GracyConfig(
    throttling=GracefulThrottle(
        rules=ThrottleRule(r"http(s)?://pokeapi\.co/.*", 2, timedelta(seconds=1)),
    )
)
```

**v2**

```python
from gracy import GracyConfig, Rate, Throttle

config = GracyConfig(
    throttle=Throttle(
        rules=[Rate(limit=2, per="1s", match=r"https://pokeapi\.co/.*")],
    )
)
```

- Field renamed: `throttling=` → `throttle=`.
- `per` takes `"500ms"`, `"1s"`, `"2m"`, `"1h"`, or a number of seconds.
- `match` is a regex against the **formatted** URL (v1 semantics preserved);
  it defaults to `.*`, so `Rate(limit=2)` alone throttles everything.
- New: `Throttle(mode="smooth")` opts into GCRA-style smoothing; the default
  `"exact"` is a strict sliding window (and fixes v1's negative-wait burst bug).
- Enforcement moved into the scheduler: a request holds throttle tokens from
  the moment its permit is granted - parallel clients can no longer race past
  the limit between "check" and "send".

### 9. `ConcurrentRequestLimit` → `Concurrency`

**v1**

```python
import re
from gracy import ConcurrentRequestLimit, GracyConfig

config = GracyConfig(
    concurrent_requests=ConcurrentRequestLimit(
        limit=2,
        uurl_pattern=re.compile(r".*pokemon.*"),
        blocking_args=["name"],
        limit_per_uurl=True,
    )
)
```

**v2**

```python
from gracy import Concurrency, GracyConfig

config = GracyConfig(
    concurrency=Concurrency(
        limit=2,
        match=r".*pokemon.*",   # regex string vs the UNFORMATTED url; None = all
        key_by=("name",),       # endpoint args partitioning the semaphore
        per_uurl=True,          # NOTE: v2 default is False; v1 defaulted to True
    )
)

simple = GracyConfig(concurrency=10)  # bare int still means "global limit of 10"
```

Watch the **default flip**: v1 `limit_per_uurl=True` grouped by endpoint by
default; v2 `per_uurl=False` limits across the whole client unless you say
otherwise. Global in-flight caps can also be set once via
`GracyConfig(queue=Queue(max_at_once=...))`.

### 10. Namespaces: annotation scan → explicit instance

v1 instantiated namespaces by scanning class **annotations** (which broke for
cross-module string annotations, and double-merged configs). v2 uses an
explicit descriptor instance; a bare annotation raises a helpful build-time
error.

**v1**

```python
from gracy import Gracy, GracyNamespace

class BerryNamespace(GracyNamespace[str]):
    async def get_one(self, name: str):
        return await self.get("/berry/{NAME}", {"NAME": name})

class PokeAPI(Gracy[str]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2"

    berry: BerryNamespace  # instantiated by annotation magic
```

**v2**

```python
from gracy import Gracy, GracyNamespace, get

class BerryNamespace(GracyNamespace):
    path_prefix = "/berry"

    @get("/{name}")
    async def get_one(self, name: str) -> dict: ...

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"
    berry = BerryNamespace()  # explicit instance

async def use(api: PokeAPI) -> dict:
    return await api.berry.get_one("cheri")
```

Namespaces can carry their own `config = GracyConfig(...)`, which layers
between the client config and endpoint kwargs (one precedence chain, resolved
once at `build()`). Two client instances never share namespace state.

### 11. Replay: pickle DB → schema v2 + migration CLI

**v1**

```python
from gracy import Gracy, GracyReplay
from gracy.replays.storages.sqlite import SQLiteReplayStorage

replay = GracyReplay(
    mode="record",
    storage=SQLiteReplayStorage("pokeapi.sqlite3"),
)
api = PokeAPI(replay)
```

**v2**

```python
from gracy import Replay, SqliteStorage

replay = Replay(mode="record", storage=SqliteStorage("pokeapi-v2.db"))

async def main() -> None:
    async with PokeAPI(replay=replay) as api:
        await api.get_pokemon("pikachu")
```

Renames: `GracyReplay` → `Replay`, `SQLiteReplayStorage` → `SqliteStorage`,
`discard_replays_older_than=` → `discard_older_than=`. `mode` gains `"off"`.
New knobs: `match_on=("method", "url", "body")` picks the request dimensions
that must match, and `scrub=Scrub(...)` **redacts secrets by default**
(`authorization`, `cookie`, `x-api-key` headers become `***` before hashing
and before recording - pass `scrub=None` to opt out).

**Migrating an existing v1 database** (one-shot CLI):

```console
$ python -m gracy.replay.migrate old-recordings.sqlite3 new-recordings.db
!!  REFUSING TO MIGRATE.
!!  Gracy v1 replay databases store PICKLED responses. Unpickling EXECUTES
!!  ARBITRARY CODE embedded in the file ...

$ python -m gracy.replay.migrate old-recordings.sqlite3 new-recordings.db --yes-i-trust-this-file
Migrated 1204 recording(s) (2 skipped) -> new-recordings.db
```

- Usage: `python -m gracy.replay.migrate OLD NEW --yes-i-trust-this-file`.
  `OLD` is the v1 `.sqlite3` file; `NEW` is created if missing.
- **Why the scary flag?** v1 rows contain a pickled `httpx.Response`, and
  unpickling executes any code embedded in the file - a malicious cassette
  can take over the machine that loads it. The tool refuses to run (exit
  code 2) until you assert with `--yes-i-trust-this-file` that *you* recorded
  the database or fully trust its origin. This is the only place in gracy v2
  that touches pickle.
- `httpx` must be importable (the pickled objects are httpx Responses):
  `pip install httpx`, or the tool exits with code 1 and says so.
- Rows that fail to unpickle are reported on stderr (`SKIP GET https://... :
  ...`) and don't abort the run; timestamps are preserved so
  `discard_older_than` keeps working.

### 12. Custom replay storage: ABC → async protocol

**v1** - subclass `GracyReplayStorage`, httpx types, mixed sync/async:

```python
import typing as t
from datetime import datetime

import httpx
from gracy import GracyReplayStorage

class MyStorage(GracyReplayStorage):
    def prepare(self) -> None: ...

    async def record(self, response: httpx.Response) -> None: ...

    async def find_replay(
        self, request: httpx.Request, discard_before: datetime | None
    ) -> t.Any | None: ...

    async def _load(
        self, request: httpx.Request, discard_before: datetime | None
    ) -> httpx.Response: ...

    def flush(self) -> None: ...
```

**v2** - duck-type the `ReplayStorage` protocol: four async methods over
gracy's transport-agnostic `RequestSpec`/`Response` (no base class needed,
no pickle, no httpx):

```python
from gracy import RequestSpec, Response

class MyStorage:
    async def prepare(self) -> None:
        """Called by Replay before the first request."""

    async def record(self, spec: RequestSpec, response: Response) -> None:
        """Persist the (already scrubbed) spec + response."""

    async def find(self, spec: RequestSpec, discard_before: float | None) -> Response | None:
        """Return the recorded response or None. discard_before is unix seconds.
        The returned Response must have is_replay=True."""
        return None

    async def flush(self) -> None:
        """Guaranteed to be awaited by Gracy.aclose() (fixes the v1 lost-writes bug)."""
```

Optionally implement the keyed fast path (`record_key(key, spec, response)` /
`find_by_key(key, discard_before)`) - `Replay` prefers it when present so the
match hash honoring the user's `match_on`/`scrub` is computed exactly once
(see `SqliteStorage` / `MemoryStorage` / `MongoReplayStorage` for reference).

### 13. Reports: `report_status` / `dangerously_reset_report` → `api.report()` / `reset_metrics()`

**v1** - reports were built from **class-level** global state and printing
mutated the report (the double-TOTAL bug):

```python
api = PokeAPI()
api.report_status("rich")
PokeAPI.dangerously_reset_report()  # classmethod: nuked metrics for EVERY instance
```

**v2** - per-instance, frozen snapshots; printing never mutates:

```python
async def main() -> None:
    async with PokeAPI() as api:
        await api.get_pokemon("pikachu")

        report = api.report()          # frozen GracyReport snapshot
        report.print("rich")           # "list" | "logger" | "rich" | "plotly"
        fig = report.to_plotly()       # build without showing

        api.reset_metrics()            # this instance only
        stats = api.queue_stats()      # live: pending, in_flight, throttle hits, pauses
```

**Documented metric change:** success rate now counts **2xx + 3xx** responses
as success (v1 counted only 2xx). If you scrape `success_rate` into
dashboards, expect a shift on upgrade.

### 14. Logging: no more import-time `basicConfig`

v1 called `logging.basicConfig(level=logging.INFO, ...)` at import, hijacking
your root logger. v2 attaches a `NullHandler` to the `"gracy"` logger and
touches nothing else. If you relied on gracy's log output appearing "for
free", configure logging yourself:

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
# or scoped: logging.getLogger("gracy").setLevel(logging.INFO)
```

### 15. Timeout default changed

v1: `REQUEST_TIMEOUT` unset → **no timeout at all**.
v2: `timeout` unset → **30 seconds**. Opting out is explicit:

```python
from gracy import Gracy

class SlowAPI(Gracy):
    base_url = "https://example.com"
    timeout = None  # explicit: no timeout (v1's implicit default)

class FastAPI(Gracy):
    base_url = "https://example.com"
    timeout = 5.0
```

`timeout=` is also a `GracyConfig` field, an endpoint-decorator kwarg, an
`api.options()` kwarg, and an `api.request()` kwarg - same precedence chain
as everything else.

### 16. Custom httpx client: `_create_client()` → transports

The default v2 transport is Rust/reqwest. Connection-level knobs move to
`TransportConfig`; full httpx control (mTLS, custom SSLContext, httpx.Auth,
ASGI apps, respx/pytest-httpx) moves to `HttpxTransport`.

**v1**

```python
import httpx
from gracy import Gracy

class PokeAPI(Gracy[str]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2"

    def _create_client(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.Config.BASE_URL, http2=True, verify=False)
```

**v2**

```python
from gracy import Gracy, HttpxTransport, RustTransport, TransportConfig

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

def build_clients() -> None:
    # connection knobs on the default (Rust) transport:
    api = PokeAPI(transport=RustTransport(TransportConfig(http2=True, verify_tls=False)))

    # or full httpx control - bring your own configured AsyncClient:
    import httpx

    client = httpx.AsyncClient(http2=True, verify=False)
    api2 = PokeAPI(transport=HttpxTransport(client=client))  # gracy will NOT close it
```

Swapping the transport does **not** bypass the queue: the scheduler permit is
granted first, then the transport sends. Also stricter in v2: unknown request
kwargs fail eagerly instead of being forwarded to httpx blindly.

### 17. Hooks: contract normalized + real pauses

`before`/`after` overrides on the client still work. Changes:

- **`before` hooks now run BEFORE throttling** (v1 ran them after). If a hook
  relied on running post-throttle, move the logic to the `after` side or a
  queue pause gate.
- The `after` hook's `result` exception is **always** `GracyRequestFailed`-
  wrapped (v1 passed the raw exception on retries but wrapped it on the first
  attempt).
- Requests issued *inside* a hook bypass concurrency (hook-deadlock fix) and
  throttle by default; opt throttling back in with
  `GracyConfig(queue=Queue(throttle_in_hooks=True))`.
- Backoff hooks actually pause admission now (dispatcher gates), including
  in-flight retries.

**v1**

```python
from gracy import Gracy
from gracy.common_hooks import HttpHeaderRetryAfterBackOffHook

hook = HttpHeaderRetryAfterBackOffHook(lock_per_endpoint=True)

class PokeAPI(Gracy[str]):
    async def before(self, context):
        ...

    async def after(self, context, response_or_exc, retry_state):
        ...
```

**v2**

```python
from gracy import Gracy, RequestContext, Response, RetryAfterBackoff, RetryState

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"
    hooks = [RetryAfterBackoff(lock_per_endpoint=True)]  # classes or instances

    async def before(self, context: RequestContext) -> None:
        ...

    async def after(
        self,
        context: RequestContext,
        result: Response | Exception,
        retry_state: RetryState | None,
    ) -> None:
        ...
```

`HttpHeaderRetryAfterBackOffHook` → `RetryAfterBackoff`,
`RateLimitBackOffHook` → `RateLimitBackoff`. `HookResult` still imports, but
return values are ignored - pausing goes through the scheduler.

---

## Coming from requests

The one-line swap:

```python
from gracy.compat import requests  # instead of: import requests

resp = requests.get("https://pokeapi.co/api/v2/pokemon/mew", params={"limit": 5})
resp.raise_for_status()
data = resp.json()
```

(Existing modules can keep their call sites untouched with
`requests = gracy.compat.requests`.)

**What works as-is:** `get/post/put/patch/delete/head/options/request`;
`params=`, `headers=`, `json=`, `data=` (dict/pairs → form-encoded, str/bytes
→ raw), `timeout=` (float or `(connect, read)` tuple - collapsed to one
budget), basic `auth=(user, password)`; responses with `.status_code`, `.ok`
(< 400, requests semantics), `.text`, `.content`, `.json()`, case-insensitive
`.headers`, `.url`, `.elapsed`, `.reason`, `.iter_content()`,
`.raise_for_status()` raising an `HTTPError` with `.response`; `Session()`
with persistent headers; `except requests.RequestException` for transport
failures (raised as a `ConnectionError` subclass of it).

**Gradual adoption ladder:**

1. **Swap the import.** Every call now runs through the full gracy pipeline
   (queue, metrics, replay-ready) with zero call-site changes.
2. **Configure policies.** Retry/throttle for every compat call, still
   without touching call sites:

   ```python
   from gracy.compat import requests
   from gracy import GracyConfig, Rate, Retry, Throttle, status

   requests.configure(
       GracyConfig(
           retry=Retry(on=status(429, 503), attempts=4, wait=2.0),
           throttle=Throttle(rules=[Rate(limit=5, per="1s")]),
       )
   )
   ```

   `requests.configure()` with no arguments restores defaults;
   `requests.shutdown()` tears down the shared engine (it lazily rebuilds).
3. **Declare your hot endpoints** on a `Gracy` subclass (`@get("/pokemon/{name}")
   ... -> dict`) to gain typed signatures, per-endpoint policies, and
   per-instance reports - while the long tail stays on the compat shim.
4. **Full client.** Move the remaining calls to `api.request(...)` and drop
   the shim.

**Honest limits** - the adapter deliberately does **not** emulate:

- **Streaming**: `stream=True` is ignored; bodies are always fully buffered
  (`iter_content()` just slices the buffered body).
- **Cookie jar**: no `cookies=` kwarg, no `Session.cookies` persistence
  (set a `Cookie` header manually if you must).
- **Hooks**: requests' `hooks=` kwarg is ignored - use gracy hooks instead.
- Also ignored (with a `UserWarning` naming the kwarg): `files=`, `proxies=`,
  `verify=`, `cert=`, `allow_redirects=`, and anything else unknown.
  Redirects are always followed (transport default); TLS/proxy settings
  belong on `configure(..., transport=...)`.
- `auth=` supports only the basic `(user, password)` tuple; digest/custom
  auth objects raise `TypeError`.
- Only **absolute** `http(s)://` URLs.
- Transport failures raise gracy-flavored exceptions (`ConnectionError` /
  `GracyRequestFailed`), not `requests.exceptions.*` classes - catch the
  shim's `RequestException` or plain `Exception`.

## Coming from httpx

The one-line swap:

```python
from gracy.compat import httpx  # instead of: import httpx

async def main() -> None:
    async with httpx.AsyncClient(base_url="https://pokeapi.co/api/v2") as client:
        resp = await client.get("/pokemon/mew", params={"limit": 5})
        resp.raise_for_status()
        data = resp.json()
```

**Parity notes (AsyncClient):**

- Constructor: `base_url=`, `headers=`, `params=`, `timeout=` behave like
  httpx (client-level values merge under per-call values; relative URLs join
  onto `base_url`, absolute URLs pass through).
- Semantics follow httpx, not gracy: **non-2xx responses are returned, not
  raised**; `resp.raise_for_status()` raises an `HTTPStatusError` carrying
  `.response` (and a `.request` attribute that is always `None`). Status flags
  (`is_success`, `is_error`, `is_client_error`, `is_server_error`) match httpx.
- `Client` is the sync twin (same constructor, `with` block), driving the
  async client on a private loop thread.
- The httpx idiom "configuration lives on the client" is preserved: pass
  `config=GracyConfig(retry=..., throttle=...)` to the constructor to turn on
  gracy policies for every request the client makes.

```python
from gracy.compat import httpx
from gracy import GracyConfig, Retry, status

def sync_usage() -> None:
    with httpx.Client(
        base_url="https://pokeapi.co/api/v2",
        config=GracyConfig(retry=Retry(on=status(503), attempts=3)),
    ) as client:
        data = client.get("/pokemon/mew").json()
```

**Testing with an injected transport** - where you used
`httpx.MockTransport`, inject a gracy transport (the compat clients accept
`transport=`, same as `Gracy(...)`):

```python
from gracy.compat import httpx
from gracy.testing import MockTransport

async def test_mew() -> None:
    transport = MockTransport({"*/pokemon/mew": {"name": "mew"}})
    async with httpx.AsyncClient(
        base_url="https://pokeapi.co/api/v2", transport=transport
    ) as client:
        resp = await client.get("/pokemon/mew")
        assert resp.json() == {"name": "mew"}
        assert transport.calls[0].method == "GET"
```

(respx / pytest-httpx keep working too - inject
`gracy.HttpxTransport()` so requests go through a real httpx client.)

**Honest limits:** no streaming API (`client.stream(...)`, `aiter_bytes`) -
bodies are buffered; no cookie jar; no `auth=` flows; no event hooks
(`event_hooks=`); no `follow_redirects=` per call (transport-level default);
the response object is the subset shown above (no `.request`, no
`.next_request`, etc.). The gradual-adoption ladder is the same as for
requests: swap the import → pass `config=` → declare endpoints on a `Gracy`
subclass → full client.

---

## FAQ

**mypy complains about my endpoint stubs (`empty-body`).**
Endpoint declarations are `...`-bodied functions with non-`None` return
annotations; mypy flags that as `[empty-body]`. Silence it for your API
modules:

```toml
# pyproject.toml
[tool.mypy]
disable_error_code = ["empty-body"]

# or scoped to your client modules only:
[[tool.mypy.overrides]]
module = "myapp.clients.*"
disable_error_code = ["empty-body"]
```

(Or per-stub: `async def get_pokemon(self, name: str) -> dict: ...  # type: ignore[empty-body]`.)

**Can I run without the Rust engine?**
Yes. Set `GRACY_ENGINE=python` to force the pure-Python reference scheduler
and the httpx transport (requires `pip install 'gracy[httpx]'`). The two
engines are behavior-identical (the Python implementation is the executable
spec the Rust core mirrors; the full test suite runs against both). Unset,
gracy picks `rust` when the compiled core is importable, else `python`.
`GRACY_ENGINE=rust` makes a missing core a loud `GracyConfigError` instead of
a silent fallback. Check what you're on with `gracy.engine_version()` /
`gracy.engine.current_engine()`.

**How do forks / prefork servers (gunicorn, celery) work?**
Build clients **per worker, after the fork**. An *unstarted* client
(constructed but not built) holds no runtime state and is fork-safe by
construction - module-level instances are fine as long as `build()` happens
in the worker. A client built *before* the fork is poisoned in the child and
raises `GracyForkedClientError` on use (and using a client from a different
event loop raises `GracyWrongLoopError`). Typical pattern: create the client
in the worker's startup hook (`post_fork`, FastAPI `lifespan`, celery
`worker_process_init`) and close it on shutdown.

**Which wheels ship? What about PyPy?**
Prebuilt wheels cover CPython 3.10+ (abi3) on the mainstream platforms
(Linux x86_64/aarch64, macOS, Windows). Anything else - PyPy included -
installs from the **sdist, which compiles the Rust core and therefore needs a
Rust toolchain** (`rustup` + a C linker; the build runs via maturin
automatically under pip). If building the extension isn't an option, the
sdist's pure-Python fallback path plus `GRACY_ENGINE=python` and
`pip install httpx` gives you the full feature set without the compiled core.

**Is there a v1 compatibility shim?**
`gracy.v1compat` maps `GracyConfig(parser=...)` / `@graceful` onto the new
plan with deprecation warnings - it exists only for the alpha/beta cycle to
ease incremental migration and will be removed for the 2.0.0 final.
