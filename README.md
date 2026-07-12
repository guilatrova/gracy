<p align="center">
    <img src="https://raw.githubusercontent.com/guilatrova/gracy/main/img/logo.png">
</p>

<h2 align="center">Python's most graceful API Client Framework</h2>

<p align="center">
  <!-- CI --><a href="https://github.com/guilatrova/gracy/actions"><img alt="Actions Status" src="https://github.com/guilatrova/gracy/workflows/CI/badge.svg"></a>
  <!-- PyPI --><a href="https://pypi.org/project/gracy/"><img alt="PyPI" src="https://img.shields.io/pypi/v/gracy"/></a>
  <!-- Supported Python versions --><img alt="python version" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <!-- Rust core --><img alt="core: rust" src="https://img.shields.io/badge/core-rust%20%F0%9F%A6%80-orange">
  <!-- PyPI downloads --><a href="https://pepy.tech/project/gracy/"><img alt="Downloads" src="https://static.pepy.tech/badge/gracy/week"/></a>
  <!-- LICENSE --><a href="https://github.com/guilatrova/gracy/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/github/license/guilatrova/gracy"/></a>
  <!-- Tryceratops --><a href="https://github.com/guilatrova/tryceratops"><img alt="try/except style: tryceratops" src="https://img.shields.io/badge/try%2Fexcept%20style-tryceratops%20%F0%9F%A6%96%E2%9C%A8-black" /></a>
  <!-- Typing --><a href="https://github.com/microsoft/pyright"><img alt="Types: pyright" src="https://img.shields.io/badge/types-pyright-blue.svg"/></a>
  <!-- Follow handle --><a href="https://twitter.com/intent/user?screen_name=guilatrova"><img alt="Follow guilatrova" src="https://img.shields.io/twitter/follow/guilatrova?style=social"/></a>
  <!-- Sponsor --><a href="https://github.com/sponsors/guilatrova"><img alt="Sponsor guilatrova" src="https://img.shields.io/github/sponsors/guilatrova?logo=GitHub%20Sponsors&style=social"/></a>
</p>

Gracy handles failures, retries, throttling, parsing, replaying, and reporting for all your HTTP interactions.

**Gracy is Rust-powered.** 🦀 The hot path (a priority request **queue** with exact sliding-window throttling, the HTTP transport (tokio + reqwest), metrics, and replay storage) runs in a compiled Rust core. Everything you touch stays plain Python: typed `@get`/`@post` endpoint decorators, hooks, validators, parsers, and config. The queue IS the throttle: no request reaches the wire without a permit, so rate limits, concurrency caps, priorities, and 429-pauses are one mechanism, not scattered sleeps.

> "Let Gracy do the boring stuff while you focus on your application"

---

**Summary**

- [🧑‍💻 Get started](#-get-started)
  - [Installation](#installation)
  - [Quickstart](#quickstart)
  - [Sync? Also one line](#sync-also-one-line)
- [🔁 One-line drop-in](#-one-line-drop-in)
  - [Coming from requests](#coming-from-requests)
  - [Coming from httpx](#coming-from-httpx)
- [🧪 Interactive mode (`gracy explore`)](#-interactive-mode-gracy-explore)
  - [vs Postman](#vs-postman)
- [⚙️ Feature tour](#️-feature-tour)
  - [Status policies](#status-policies)
  - [Per-status actions: on= and raises()](#per-status-actions-on-and-raises)
  - [Retry + Backoff](#retry--backoff)
  - [Throttling](#throttling)
  - [Concurrency](#concurrency)
  - [The queue](#the-queue)
  - [Priorities & scoped overrides](#priorities--scoped-overrides)
  - [Hooks](#hooks)
  - [Validators](#validators)
  - [Decoders](#decoders)
  - [Replay requests](#replay-requests)
  - [Reports](#reports)
  - [📟 Live terminal dashboard](#-live-terminal-dashboard)
  - [📚 Generate docs from your client](#-generate-docs-from-your-client)
  - [Pagination](#pagination)
  - [Namespaces](#namespaces)
  - [Testing helpers](#testing-helpers)
  - [Engine selection](#engine-selection)
- [🏗️ Architecture](#️-architecture)
- [🚚 Migrating](#-migrating)
- [🛠️ Development](#️-development)
- [Change log](#change-log)
- [License](#license)
- [Credits](#credits)

## 🧑‍💻 Get started

### Installation

**As a Python library:**

```
pip install --pre gracy
```

Wheels ship with the compiled Rust core for all major platforms: no toolchain needed. Zero required Python dependencies.

**As a standalone CLI (no Python needed)**, for `gracy explore`, the terminal API client. Grab a single self-contained binary:

```sh
# macOS / Linux: installs to ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/guilatrova/gracy/main/packaging/install.sh | sh
```

Or download the binary for your platform straight from [Releases](https://github.com/guilatrova/gracy/releases) (`gracy-macos-arm64`, `gracy-macos-x86_64`, `gracy-linux-x86_64`, `gracy-windows-x86_64.exe`), `chmod +x`, and run. Each ships with a `.sha256` checksum.

Already have Python tooling? Skip the install entirely:

```sh
uvx gracy explore https://pokeapi.co/api/v2   # or: pipx run gracy explore ...
```

### Quickstart

Examples use the [PokeAPI](https://pokeapi.co). Declare endpoints with decorators, and let return annotations drive decoding, so your IDE sees real types, zero casts:

```py
import asyncio
from http import HTTPStatus
from typing import Annotated

from pydantic import BaseModel

from gracy import Backoff, Gracy, GracyConfig, Path, PydanticDecoder, Retry, get, status


class Pokemon(BaseModel):
    name: str
    order: int


class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    config = GracyConfig(
        decoder=PydanticDecoder(),  # 👈 decodes bytes into your return annotations
        retry=Retry(
            on=(status(429, 502, 503), TimeoutError),
            attempts=3,
            wait=Backoff(initial=1.0, multiplier=1.5, max=10.0),
        ),
    )

    # 👇 404 becomes None instead of raising, and the type says so
    @get("/pokemon/{name}", on={HTTPStatus.NOT_FOUND: None})
    async def get_pokemon(self, name: Annotated[str, Path]) -> Pokemon | None: ...


async def main() -> None:
    async with PokeAPI() as api:            # build → compile plan → start scheduler
        mew = await api.get_pokemon("mew")          # -> Pokemon | None
        print(mew)                                  # name='mew' order=248

        missing = await api.get_pokemon("agumon")   # -> None (it's a Digimon 🙊)
        print(missing)

        api.report().print("list")           # or "rich" / "logger"


asyncio.run(main())
```

That's retries, typed parsing, 404-to-None, metrics, and an explicit lifecycle, with no `try/except` boilerplate in sight.

### Sync? Also one line

The sync facade runs the **real** async client on a private background loop, so hooks, retries, and throttling are all included:

```py
with PokeAPI.sync() as api:
    mew = api.get_pokemon("mew")
```

## 🔁 One-line drop-in

Not ready to declare endpoints? Swap one import and your existing code runs through the full Gracy pipeline (queue, throttle, retry, replay, reports).

### Coming from requests

```diff
- import requests
+ from gracy.compat import requests

  resp = requests.get("https://pokeapi.co/api/v2/pokemon/mew", timeout=5)
  resp.raise_for_status()
  data = resp.json()
```

`Session()`, `params=`, `json=`, `data=`, `headers=`, `auth=`, and friends keep working. Then, when you want superpowers **without touching a single call site**:

```py
from gracy import Backoff, GracyConfig, Rate, Retry, Throttle, status
from gracy.compat import requests

requests.configure(GracyConfig(
    retry=Retry(on=status(429, 502, 503), attempts=3, wait=Backoff(initial=1, multiplier=2)),
    throttle=Throttle(rules=[Rate(10, per="1s")]),
))

requests.get("https://pokeapi.co/api/v2/berry/cheri")  # now retried + throttled 🎉
```

### Coming from httpx

```diff
- import httpx
+ from gracy.compat import httpx

  async with httpx.AsyncClient(base_url="https://pokeapi.co/api/v2") as client:
      resp = await client.get("/pokemon/mew")
      resp.raise_for_status()
```

Pass `config=GracyConfig(...)` to the client constructor to enable policies (a sync `Client` twin ships too). Semantics follow httpx: non-2xx responses are returned, not raised.

## 🧪 Interactive mode (`gracy explore`)

Exploring or reverse-engineering an API? `gracy explore` is a REPL where every request runs through the real pipeline, gets recorded, and is mined for types, then **`save` compiles the whole session into a typed client + tests that pass offline**. It's a terminal-native Postman that hands you production Python instead of a JSON collection.

**Reach for it when you're:**

- **Poking at an unfamiliar API**: fire requests, see responses, and let gracy infer the models and `{param}` templates as you go.
- **Bootstrapping a client**: walk the endpoints once, `save`, and ship the typed `Gracy` class you'd otherwise hand-write.
- **Locking down behavior**: `save --tests` gives you replay-backed tests that run in CI with zero network.
- **Driving it from an AI agent**: `gracy x '<cmd>' --json` is scriptable and stateful; an agent can map an API overnight and leave you a reviewed client, passing tests, and OpenAPI docs.

```
$ gracy explore https://pokeapi.co/api/v2
gracy› get /pokemon/pikachu
GET .../pokemon/pikachu -> 200 (81 ms)
{ "id": 25, "name": "pikachu", ... }
gracy› endpoint get_pokemon      # turn the last request into an endpoint
gracy› get /pokemon/mew          # a 2nd call infers the {param} template
gracy› endpoint get_pokemon      # repeat to fold it in -> /pokemon/{pokemon}
gracy› param 1 as name           # rename the template param -> /pokemon/{name}
gracy› on 404 none               # map 404 -> None (typed as Pokemon | None)
gracy› model Pokemon             # name the inferred response model
gracy› list                      # see your endpoints (alias of `show endpoints`)
gracy› save pokeapi.py --tests
wrote pokeapi.py, test_pokeapi.py, pokeapi.cassette.db
```

The generated `pokeapi.py` is exactly the typed client you'd hand-write (`@get("/pokemon/{name}", on={404: None}) async def get_pokemon(...) -> Pokemon | None: ...`), and `test_pokeapi.py` replays the recorded responses, so it's **green with no network**. `endpoint` said `created` the first time and `folded into` the second; `rename endpoint <old> <new>` (and `rename model`) fix a name after the fact.

The REPL suggests as you type: an inline grey **ghost text** shows the rest of the likely word (type `g`, see `et`), which `→` accepts; **Tab** lists all matches. Both read your live session: commands, `show` targets, endpoint names (`endpoint <Tab>`), `on` actions, paths you've already hit (`get <Tab>`), and files for `save`. History persists in `~/.gracy_history`. Ghost text needs `prompt_toolkit` (`pip install 'gracy[explore]'`); without it the REPL falls back to plain Tab completion.

It also keeps you oriented: a dim **right-prompt** shows the *active endpoint* (the one `model`/`on`/`param` will change), and a **bottom bar** previews the impact of the line you're typing before you run it: `model Pokemon` shows `names the response model of get_pokemon → Pokemon`, `save api.py --tests` shows `writes api.py + tests + cassette · 2 endpoints`. No more guessing what a command will do.

**Bodies** use httpie syntax on `post`/`put`/`patch`: `k==v` query · `k=v` string field · `k:=v` raw JSON · `@file` · `{...}` inline · `-H 'K: v'` header. `$VAR` resolves at send time but is stored unresolved, so secrets never hit disk.

**Captures** chain requests together: `set <name> <path>` grabs a value from the last response (a dot/bracket path into the JSON), and `{{name}}` reuses it in any later request. Not sure what a path resolves to? `peek <path>` shows the value without capturing it, and the bottom bar previews the real value live as you type `set` or `peek`. It's distinct from `$VAR` (an env var, resolved at send time) and `{param}` (a path-template placeholder): a capture is concrete data pulled from a real response and written to the session file, so use it for ids and names to chain on, not for secrets (use `$VAR` for those).

```
gracy› get /berry
GET .../berry -> 200 (60.0 ms)
gracy› set berry results[0].name            # grab a value from the last response
captured berry = cheri (from results[0].name)
gracy› get /berry/{{berry}}                 # {{berry}} expands to cheri before the request
GET .../berry/cheri -> 200 (58.0 ms)
```

**Agent mode**: same engine, no TTY, machine-readable output (this is how an AI agent drives it):

```
$ gracy x 'get /pokemon/ditto' --base https://pokeapi.co/api/v2 --session poke.json --json
{"step_id": 1, "status": 200, "matched_endpoint": null, "body_preview": {...}}
$ gracy x 'save pokeapi.py --tests' --session poke.json --json
{"ok": true, "files": ["pokeapi.py", "test_pokeapi.py", "pokeapi.cassette.db"]}
```

For a long agent run, `gracy explore --stdio` keeps **one live process**: it reads a JSON command per stdin line and emits a JSON result per stdout line (JSONL, the same framing LSP and MCP use), so the session stays in memory with zero per-command startup:

```
→ {"cmd": "get /pokemon/pikachu"}
← {"step_id": 1, "status": 200, "matched_endpoint": null, "body_preview": {...}}
→ {"cmd": "save pokeapi.py --tests"}
← {"ok": true, "files": ["pokeapi.py", "test_pokeapi.py", "pokeapi.cassette.db"]}
```

**Catch drift in CI**: `gracy explore --check` re-probes every named endpoint against the live API and diffs the response shape against the recording. It exits non-zero when a field is added, removed, or changes type, so a broken upstream fails your build:

```
$ gracy explore --check --session poke.json
  ✗ get_pokemon  GET /pokemon/{name}
      - removed: base_experience
      ~ type:    weight: int -> str

1/3 endpoints drifted        # exit code 1
```

### vs Postman

| | Postman | `gracy explore` |
|---|---|---|
| Where it lives | GUI + cloud account | Your terminal, offline |
| Saved work | Proprietary collection (JSON) | A session file **and typed Python**, both diff in git |
| Tests | Written in JS, run in the app | Generated pytest, replay-backed, green in CI with no network |
| Environments / secrets | Cloud-synced variables | `$VAR` env refs, resolved at send, **never written to disk** |
| Docs | Add-on | `gracy docs` on the generated client → OpenAPI 3.1 + HTML, free |
| Automation | Newman (separate runner) | `gracy x … --json`, scriptable & AI-agent friendly out of the box |
| Output | A collection | **A production SDK** |

Not yet (on the roadmap): serving a recorded session as a local mock server. Full walkthrough (POST bodies + agent flow): [examples/v2_explore_demo.md](examples/v2_explore_demo.md).

## ⚙️ Feature tour

### Status policies

By default any 2xx passes validation. Tighten or widen that per endpoint (or client-wide):

```py
from gracy import allow, strict

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    # ONLY 200 passes; even 201 would fail validation
    @get("/pokemon/{name}", status_policy=strict(HTTPStatus.OK))
    async def only_200(self, name: Annotated[str, Path]) -> dict: ...

    # 2xx OR 404 pass; the on= map decides what a 404 becomes
    @get("/pokemon/{name}", status_policy=allow(HTTPStatus.NOT_FOUND), on={404: None})
    async def maybe(self, name: Annotated[str, Path]) -> dict | None: ...
```

Combining strict + allow is a build-time error, so you never silently get one policy when you meant the other.

### Per-status actions: on= and raises()

`on={status: action}` maps statuses to outcomes: a literal (returned as-is), a callable over the response, or `raises()` for [readable custom exceptions](https://guicommits.com/how-to-structure-exception-in-python-like-a-pro/):

```py
import gracy
from gracy import raises

class PokemonNotFound(gracy.GracyUserDefinedException):
    # placeholders: {URL} {STATUS} {METHOD} ... + your endpoint args UPPERCASED
    BASE_MESSAGE = "Unable to find [{NAME}] at {URL} due to {STATUS}"

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    @get("/pokemon/{name}", on={HTTPStatus.NOT_FOUND: None})
    async def get_pokemon(self, name: Annotated[str, Path]) -> Pokemon | None: ...

    @get("/pokemon/{name}", on={HTTPStatus.NOT_FOUND: raises(PokemonNotFound)})
    async def get_pokemon_strict(self, name: Annotated[str, Path]) -> Pokemon: ...
```

### Retry + Backoff

Who doesn't hate flaky APIs? 🙋 Declare the policy once; every attempt re-enters the queue (so retries are throttled and respect pauses too):

```py
from gracy import Backoff, Retry, status

Retry(
    on=(status(429, 502, 503), TimeoutError),  # statuses and/or exception types
    attempts=3,
    wait=Backoff(initial=1.0, multiplier=1.5, max=10.0, jitter=True),
    respect_retry_after=True,     # honor the server's Retry-After header
    on_exhausted="raise",         # or "return" the last response
)
```

### Throttling

Rate limiting issues? No more. Rules are **exact sliding windows** enforced by the Rust queue: never N+1 requests in any trailing window, and never over-waiting either:

```py
from gracy import Rate, Throttle

Throttle(rules=[
    Rate(10, per="1s", match=r".*/pokemon/.*"),  # regex vs the formatted URL
    Rate(600, per="1m"),                         # burst + sustained compose
])
```

Prefer evenly-spaced requests over bursts? `Throttle(rules=[...], mode="smooth")` switches to GCRA pacing.

### Concurrency

Cap in-flight requests globally, per endpoint, or partitioned by an argument:

```py
from gracy import Concurrency

@get("/pokemon", concurrency=Concurrency(limit=2))
async def list_pokemon(self, offset: Annotated[int, Query] = 0,
                       limit: Annotated[int, Query] = 20) -> dict: ...

# per-tenant partitioning: one semaphore per distinct `org` value
Concurrency(limit=5, key_by=("org",))
```

### The queue

Every request is admitted through one scheduler: throttles, semaphores, priorities, backpressure, and pauses are all admission control:

```py
from gracy import Queue

Queue(
    max_at_once=10,                       # global in-flight cap
    max_pending=5_000,                    # backpressure: queue depth
    on_full="wait",                       # or "raise" -> GracyQueueFull
    pause_on_status={429: "endpoint"},    # a 429 pauses that endpoint's lane
)
```

Peek inside anytime with `api.queue_stats()`: pending, in-flight, throttle hits, active pauses.

### Priorities & scoped overrides

Per-call knobs live on `api.request()` (the ad-hoc escape hatch) and `api.options()`:

```py
# jump the queue for an urgent call
page = await api.request(
    "GET", "/pokemon/{NAME}", path={"NAME": "pikachu"},
    decode_as=Pokemon, priority=10,
)

# scoped override, applies to nested calls too
async with api.options(retry=None, on={404: None}):
    await api.get_pokemon("missingno")
```

URL-shaped overrides replace scattering decorators across methods:

```py
config = GracyConfig(overrides={
    "*/pokemon/*": GracyConfig(throttle=Throttle(rules=[Rate(5, per="1s")])),
})
```

### Hooks

Override `before`/`after` on your client (it becomes a hook itself), or register ordered hook objects:

```py
import time
from gracy import RetryAfterBackoff

class PokeAPI(Gracy):
    hooks = [RetryAfterBackoff(lock_per_endpoint=True)]  # 👈 built-in 429/503 backoff

    async def before(self, context: gracy.RequestContext) -> None:
        context.state["t0"] = time.monotonic()  # per-request scratch space

    async def after(self, context, result: gracy.Response | Exception,
                    retry_state: gracy.RetryState | None) -> None:
        ...  # exceptions arrive consistently GracyRequestFailed-wrapped
```

`RetryAfterBackoff` (and `RateLimitBackoff`, its fixed-delay sibling) drive **scheduler pause gates**: a 429 with `Retry-After` genuinely pauses admission for the endpoint (or whole client), including retries already in flight. Requests issued *inside* hooks skip hooks and semaphores by default, so a hook that issues its own request can never deadlock behind the lane it's trying to heal.

### Validators

Decide "failed" beyond status codes: a failing validator triggers retries like any error:

```py
class NoErrorField(gracy.Validator):
    def check(self, response: gracy.Response) -> None:
        if response.json().get("error"):
            raise MyDomainError(response)

config = GracyConfig(
    validators=NoErrorField(),
    retry=Retry(on=MyDomainError, attempts=3),
)
```

### Decoders

Return annotations drive decoding: `dict`/`list`/`str`/`bytes`/scalars and plain dataclasses work out of the box. For models, plug a decoder:

```py
from gracy.parsing import PydanticDecoder, MsgspecDecoder

config = GracyConfig(decoder=PydanticDecoder())   # pip install gracy[pydantic]
# or MsgspecDecoder()                             # pip install gracy[msgspec]

@get("/pokemon/{name}")
async def get_pokemon(self, name: Annotated[str, Path]) -> Pokemon: ...  # validated model out
```

### Replay requests

Record real traffic once, replay it forever: tests without latency, rate limits, or flakiness. Storage is pickle-free SQLite (schema v2): diffable, inspectable, safe to commit.

```py
from gracy import Replay, Scrub, SqliteStorage

record = Replay(mode="record", storage=SqliteStorage("tests/pokeapi.db"))
async with PokeAPI(replay=record) as api:
    await api.get_pokemon("mew")             # hits the API, recorded

replay = Replay(
    mode="replay",                           # or "smart-replay": replay hits, record misses
    storage=SqliteStorage("tests/pokeapi.db"),
    scrub=Scrub(headers=["authorization"]),  # secrets scrubbed ON by default
    disable_throttling=True,
)
async with PokeAPI(replay=replay) as api:
    await api.get_pokemon("mew")             # served from storage, zero network
```

Replay hits never spend throttle tokens, and parsers/retries/validators run as usual. Recordings are a plain, pickle-free SQLite schema you can inspect with any client. MongoDB storage ships too (`pip install gracy[mongo]`).

### Reports

`api.report()` returns a **frozen** snapshot you can print as many times as you like:

```py
api.report().print("rich")      # pretty table  (pip install gracy[rich])
api.report().print("logger")    # one-liners for production logs
api.report().print("list")      # plain stdout

fig = api.report().to_plotly()  # pip install gracy[plotly]
fig.show()
```

![Report](https://raw.githubusercontent.com/guilatrova/gracy/main/img/report-rich-example.png)

Columns cover totals, success rate, per-status counts, retries, throttles, replays, and latency avg/max/**p95/p99** per endpoint template.

### 📟 Live terminal dashboard

Reports tell you what happened; the monitor shows what's happening **right now**. Every monitored client publishes ~4 snapshots/s to a tiny spool file, and `python -m gracy.monitor` renders them as a live dashboard: queue depth, in-flight requests, throttle waits, pauses, retries, and per-endpoint stats, aggregated across every running client (and process) on the machine.

Enable it per client or globally via env var:

```py
async with PokeAPI(monitor=True) as api: ...   # per client
```

```sh
GRACY_MONITOR=1 python my_app.py               # any client, no code change
```

Then watch from any other terminal (requires `pip install gracy[rich]`):

```sh
python -m gracy.monitor        # or the installed alias: gracy-monitor
```

```
╭──────────────────────────────────────────────────────────────────────────╮
│ ⚡ GRACY MONITOR                       1 source   rust   up 42s  7.9 req/s│
╰──────────────────────────────────────────────────────────────────────────╯
╭─────────╮╭─────────╮╭─────────╮╭────────╮╭────────╮╭─────────╮╭─────────╮
│    4    ││   25    ││   19    ││  2.0s  ││   0    ││    3    ││    0    │
│IN-FLIGHT││ ON HOLD ││THROTTLES││ PAUSED ││ ABORTS ││ RETRIES ││ REPLAYS │
╰─────────╯╰─────────╯╰─────────╯╰────────╯╰────────╯╰─────────╯╰─────────╯
╭─ activity · last 60s ────────────────────────────────────────────────────╮
│ in-flight  ▁▁▂▄██▅▃▂▁▁▁▃▅███▆▄▂▁▁▁▂▄▆██▇▅▃▂▁▁▁▂▄▆███▅▃▂▁▁▂▄▆██        4  │
│ on hold    ▁▁▅███▇▅▃▂▁▁▁▄▇██▆▄▂▁▁▁▃▆███▆▄▂▁▁▁▃▅███▇▅▃▁▁▁▃▅███        25  │
│ req/s      ▁▂▄▆▇███▇▆▅▄▄▅▆▇██▇▆▅▄▄▅▆▇███▇▆▅▄▄▅▆▇██▇▆▅▄▄▅▆▇██        7.9  │
╰──────────────────────────────────────────────────────────────────────────╯
╭─ endpoints ──────────────────────────────────────────────────────────────╮
│ endpoint                    reqs    ok%  retries   thr  aborts p95 ms req/s│
│ ────────────────────────────────────────────────────────────────────────  │
│ http://127.0.0.1:6060/ok     121  100.0        0    11       0    210  3.1│
│ http://127.0.0.1:6060/slow    64  100.0        0     5       0   1264  1.7│
│ http://127.0.0.1:6060/flaky   58   74.1        9     7       2    952  1.5│
╰──────────────────────────────────────────────────────────────────────────╯
● LIVE  DemoAPI pid 25562 (rust)
ctrl+c to quit
```

Zero overhead when off (nothing is imported), and a broken snapshot can never take your app down: publish errors are swallowed and logged. Flags: `--dir` (spool dir, default `$GRACY_MONITOR_DIR` or the system temp dir), `--fps`, `--window`, `--once` (render one frame and exit, great for CI logs).

Want to see it shine without writing code? Run [examples/v2_monitor_demo.py](./examples/v2_monitor_demo.py) in one terminal and `python -m gracy.monitor` in another; it spins a local misbehaving API and fires bursty traffic that lights up every tile.

### 📚 Generate docs from your client

Your class **is** the spec. Every path, param kind, return type, `on=` status action, retry/throttle policy, and docstring is already declared on your Gracy client, so gracy can generate API documentation from it, statically. No instance is created, nothing is started, no network is touched: it works from the class alone.

```sh
python -m gracy.docs myapp.api:PokeAPI --format yaml -o openapi.yaml
```

That emits **OpenAPI 3.1**, the format we recommend, because it plugs your client straight into the whole ecosystem: Swagger UI, Redoc, Postman imports, client/server codegen, contract testing. Gracy-specific behavior (retry, throttle, per-endpoint overrides) rides along in `x-gracy` extension blocks. `--format json` gives the same document as JSON.

Prefer a human-readable page? Render a self-contained static HTML site (zero assets, dark/light theme, copy-able usage snippets), or serve it right away:

```sh
python -m gracy.docs myapp.api:PokeAPI --format html -o docs.html
python -m gracy.docs myapp.api:PokeAPI --format html --serve 8000
```

Or do it programmatically:

```py
from gracy.docs import to_yaml, to_html

open("openapi.yaml", "w").write(to_yaml(PokeAPI))
open("docs.html", "w").write(to_html(PokeAPI))
```

Docstrings become descriptions: the class docstring is the API title + overview, and each endpoint method's docstring becomes that operation's summary/description. Document your code, get documented APIs for free.

### Pagination

```py
from gracy import GracyOffsetPaginator

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    @get("/pokemon")
    async def list_pokemon(self, offset: Annotated[int, Query] = 0,
                           limit: Annotated[int, Query] = 20) -> dict: ...

    def paginate(self, limit: int = 20) -> GracyOffsetPaginator[dict]:
        return GracyOffsetPaginator[dict](
            gracy_func=self.list_pokemon,
            has_next=lambda r: True if r is None else bool(r["next"]),
            page_size=limit,
        )

async with PokeAPI() as api:
    paginator = api.paginate(limit=5)
    first = await paginator.next_page()   # one page
    async for page in paginator:          # ...or all of them
        print(page["results"])
```

### Namespaces

Group endpoints with explicit descriptors: configs merge under the client's, and two clients never share namespace state:

```py
from gracy import GracyNamespace

class BerryNamespace(GracyNamespace):
    path_prefix = "/berry"

    @get("/{name}")
    async def get_one(self, name: Annotated[str, Path]) -> dict: ...

class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"
    berry = BerryNamespace()               # 👈 explicit, no annotation magic

async with PokeAPI() as api:
    cheri = await api.berry.get_one("cheri")
```

### Testing helpers

Kill retries/throttling in tests and mock the transport, and the whole pipeline still runs:

```py
import gracy

with gracy.testing.retries_off(), gracy.testing.throttle_off():
    transport = gracy.testing.MockTransport({"*/pokemon/*": {"name": "mew", "order": 1}})
    async with PokeAPI(transport=transport) as api:
        assert (await api.get_pokemon("mew")).name == "mew"
        assert len(transport.calls) == 1
```

Need the httpx ecosystem (respx, pytest-httpx, ASGI transports, mTLS)? Inject `HttpxTransport`, and the queue/retry/replay treatment still applies:

```py
from gracy import HttpxTransport

async with PokeAPI(transport=HttpxTransport(client=my_httpx_client)) as api: ...
```

### Engine selection

The Rust engine is the default. A pure-Python reference engine (same semantics, tested differentially in CI) is one env var away:

```sh
GRACY_ENGINE=rust    # default when the compiled core is present (raises loudly if missing)
GRACY_ENGINE=python  # pure-Python scheduler + httpx transport
```

## 🏗️ Architecture

The queue IS the throttle: nothing reaches the wire without a permit:

```
            Python (policy)                        Rust core (gracy._core)
 ┌────────────────────────────────┐      ┌────────────────────────────────────┐
 │ @get endpoints · config · plan │      │            SCHEDULER               │
 │                                │      │  priority heap → concurrency       │
 │  before hooks ── replay check ─┼──────┼─► permits → sliding-window         │
 │                                │submit│  throttle → delay wheel · pauses   │
 │  after hooks · validators      │      ├────────────────────────────────────┤
 │  retry decision ─(re-admit)─┐  │◄─────┼─ TRANSPORT (tokio + reqwest)       │
 │  decode into annotations ◄──┘  │ send │  METRICS (hdrhistogram)            │
 └────────────────────────────────┘      │  REPLAY (SQLite, WAL)              │
                                         └────────────────────────────────────┘
```

**Rust** owns the hot primitives: the priority queue with exact sliding-window throttling, concurrency semaphores, pause gates, the reqwest transport, latency histograms, and replay storage. **Python** owns everything you customize: endpoint declarations, config/plan compilation, the per-request pipeline, hooks, validators, and decoders, all plain awaited callables, no FFI in sight. Rust is entered exactly twice per attempt (permit, send).

Design deep-dive: [V2_PLAN.md](./V2_PLAN.md).

## 🚚 Migrating

- **From Gracy v1:** see [MIGRATING.md](./MIGRATING.md): a before/after cookbook for every breaking change (`class Config` → class attributes, `parser=` → `on=`, `@graceful` → decorator kwargs + `options()`, replay DB migration, and more).
- **From requests/httpx:** start with [the one-line drop-in](#-one-line-drop-in), then graduate to declared endpoints at your own pace.
- **v1 docs:** the v1 README is preserved in git history (see the `main` branch history / v1 tags).

## 🛠️ Development

```sh
uv venv && source .venv/bin/activate
uv pip install maturin

maturin develop            # build the Rust core into the venv (add --release to benchmark)

cargo test                 # Rust unit + property tests (crates/gracy-core is plain Rust, no PyO3)

pytest                     # full Python suite on the default (rust) engine
GRACY_ENGINE=python pytest # same suite on the pure-Python reference engine
GRACY_ENGINE=rust pytest   # force the compiled core
```

Eyeball the live dashboard end-to-end: run `python examples/v2_monitor_demo.py` in one terminal and `python -m gracy.monitor` in another (the demo needs no network; it spins its own local server).

<!-- ## Contributing -->
<!-- Thank you for considering making Gracy better for everyone! -->
<!-- Refer to [Contributing docs](docs/CONTRIBUTING.md).-->

## Change log

See [CHANGELOG](CHANGELOG.md).

## License

MIT

## Credits

Thanks to the last three startups I worked which forced me to do the same things and resolve the same problems over and over again. I got sick of it and built this lib.

Most importantly: **Thanks to God**, who allowed me (a random 🇧🇷 guy) to work for many different 🇺🇸 startups. This is ironic since due to God's grace, I was able to build Gracy. 🙌

Also, thanks to the [tokio](https://tokio.rs), [reqwest](https://github.com/seanmonstar/reqwest), [PyO3](https://pyo3.rs), and [rich](https://github.com/Textualize/rich) projects for the beautiful and simple APIs that power Gracy.
