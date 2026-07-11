"""Gracy 2.0 live monitor demo — run this, then watch the dashboard.

Terminal A:  python examples/v2_monitor_demo.py
Terminal B:  python -m gracy.monitor

Everything is self-contained: a local threaded stdlib HTTP server plays the
part of a misbehaving API (slow routes, random 503s, periodic 429s with
Retry-After), and a monitored Gracy client hammers it in bursts so every
dashboard tile lights up: IN-FLIGHT, ON HOLD, THROTTLES, PAUSED, RETRIES
and the occasional ABORT. Ctrl+C stops everything cleanly.
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
import typing as t
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import gracy
from gracy import Gracy, GracyConfig, Queue, Rate, Retry, Throttle, get

RUN_FOR_S = 60.0  # total demo duration
BURST_SIZE = 40  # concurrent requests per wave
LULL_S = 2.0  # quiet gap between waves
LIMITED_EVERY = 15  # every ~15th /limited call answers 429


# --------------------------------------------------------------------- server


class _DemoHandler(BaseHTTPRequestHandler):
    """A tiny API with personality: fast, slow, flaky and rate-limited routes."""

    protocol_version = "HTTP/1.1"
    _limited_calls = 0
    _limited_lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/ok":
            self._reply(200, {"route": "ok"})
        elif route == "/slow":
            ms = int(parse_qs(parsed.query).get("ms", ["400"])[0])
            time.sleep(ms / 1000.0)
            self._reply(200, {"route": "slow", "ms": ms})
        elif route == "/flaky":
            if random.random() < 0.25:
                self._reply(503, {"error": "flaky day"})
            else:
                self._reply(200, {"route": "flaky"})
        elif route == "/limited":
            cls = type(self)
            with cls._limited_lock:
                cls._limited_calls += 1
                limited = cls._limited_calls % LIMITED_EVERY == 0
            if limited:
                self._reply(429, {"error": "slow down"}, headers={"Retry-After": "2"})
            else:
                self._reply(200, {"route": "limited"})
        else:
            self._reply(404, {"error": "no such route"})

    def _reply(self, status: int, body: dict[str, t.Any], headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: t.Any) -> None:  # silence the access log
        pass


def start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DemoHandler)
    thread = threading.Thread(target=server.serve_forever, name="demo-http", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


# --------------------------------------------------------------------- client


class DemoAPI(Gracy):
    # base_url is stamped in main() once the local server picks its port.
    config = GracyConfig(
        retry=Retry(on=gracy.status(503, 429), attempts=3, wait=0.3),
        throttle=Throttle(rules=[Rate(8, per="1s")]),
        queue=Queue(max_at_once=4, pause_on_status={429: "client"}),
        log_errors=None,  # keep the terminal clean; the dashboard tells the story
    )

    @get("/ok")
    async def ok(self) -> dict: ...

    @get("/slow")
    async def slow(self, ms: int) -> dict: ...

    @get("/flaky")
    async def flaky(self) -> dict: ...

    @get("/limited")
    async def limited(self) -> dict: ...


def _mixed_wave(api: DemoAPI) -> list[t.Coroutine[t.Any, t.Any, dict]]:
    """One burst: a weighted mix so every endpoint (and failure mode) shows up."""
    calls: list[t.Coroutine[t.Any, t.Any, dict]] = []
    for _ in range(BURST_SIZE):
        roll = random.random()
        if roll < 0.40:
            calls.append(api.ok())
        elif roll < 0.60:
            calls.append(api.slow(ms=400))
        elif roll < 0.80:
            calls.append(api.flaky())
        else:
            calls.append(api.limited())
    return calls


# --------------------------------------------------------------------- driver


BANNER = """
=========================================================================
  GRACY LIVE MONITOR DEMO
  Traffic is flowing against a local misbehaving API for ~{duration:.0f}s.

  >>> Open a SECOND terminal and run:

      python -m gracy.monitor

  Watch for: IN-FLIGHT (capped at 4), ON HOLD (burst backlog), THROTTLES
  (8 req/s ceiling), PAUSED (429 + Retry-After freezes the client),
  RETRIES (flaky 503s) and ABORTS (retries exhausted).

  Ctrl+C stops the demo cleanly.
=========================================================================
"""


async def main() -> None:
    server, base_url = start_server()
    DemoAPI.base_url = base_url
    print(BANNER.format(duration=RUN_FOR_S), flush=True)
    print(f"local demo API listening on {base_url}", flush=True)

    api = DemoAPI(monitor=True)
    try:
        await api.build()
        deadline = time.monotonic() + RUN_FOR_S
        wave = 0
        while time.monotonic() < deadline:
            wave += 1
            results = await asyncio.gather(*_mixed_wave(api), return_exceptions=True)
            failed = sum(1 for r in results if isinstance(r, BaseException))
            print(f"wave {wave:>2}: {len(results) - failed:>2} ok / {failed} aborted", flush=True)
            await asyncio.sleep(LULL_S)  # short lull so the sparklines breathe
        print("demo finished — the dashboard greys this source out, then reaps it")
    except KeyboardInterrupt:
        print("\ninterrupted — shutting down cleanly")
    finally:
        await api.aclose()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
