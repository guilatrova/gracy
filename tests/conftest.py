"""Shared test infra for the Gracy v2 suite.

Fixtures:
- test_server (session): base URL of a local threaded HTTP server with the
  routes documented on each handler branch below.
- make_client: async factory ``await make_client(Cls, **init_kwargs)`` that
  builds a Gracy client and guarantees ``aclose()`` at teardown.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
import typing as t
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture(scope="session")
def test_server() -> t.Iterator[str]:
    counters: dict[str, int] = {}
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: t.Any) -> None:  # noqa: A002
            pass  # keep pytest output clean

        def _send(
            self,
            status: int,
            payload: t.Any,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _handle(self) -> None:
            split = urlsplit(self.path)
            path = split.path
            query: dict[str, t.Any] = {
                k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(split.query).items()
            }
            length = int(self.headers.get("Content-Length") or 0)
            posted = self.rfile.read(length).decode("utf-8") if length else ""

            # GET /echo/{anything} -> 200 {"path", "query", "headers"}
            if path.startswith("/echo/"):
                self._send(
                    200,
                    {
                        "path": path,
                        "query": query,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                    },
                )

            # GET|POST /status/{code} -> that code, {"status", "body"}
            elif path.startswith("/status/"):
                code = int(path.rsplit("/", 1)[1])
                self._send(code, {"status": code, "body": posted})

            # GET /flaky/{key}?fail_times=N -> N x 503 per key, then 200
            elif path.startswith("/flaky/"):
                key = "flaky:" + path[len("/flaky/") :]
                fail_times = int(query.get("fail_times", 0))
                with lock:
                    counters[key] = n = counters.get(key, 0) + 1
                if n <= fail_times:
                    self._send(503, {"error": "flaky", "calls": n})
                else:
                    self._send(200, {"ok": True, "calls": n})

            # GET /slow?ms=N -> sleeps N ms then 200
            elif path == "/slow":
                time.sleep(float(query.get("ms", 0)) / 1000.0)
                self._send(200, {"ok": True})

            # GET /retry-after/{key}?times=N&seconds=S -> N x 429 + Retry-After, then 200
            elif path.startswith("/retry-after/"):
                key = "retry-after:" + path[len("/retry-after/") :]
                times = int(query.get("times", 0))
                seconds = str(query.get("seconds", "1"))
                with lock:
                    counters[key] = n = counters.get(key, 0) + 1
                if n <= times:
                    self._send(429, {"error": "throttled", "calls": n}, {"Retry-After": seconds})
                else:
                    self._send(200, {"ok": True, "calls": n})

            # POST /reset -> clears every counter
            elif path == "/reset":
                with lock:
                    counters.clear()
                self._send(200, {"ok": True})

            else:
                self._send(404, {"error": "not found", "path": path})

        do_GET = _handle
        do_POST = _handle

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="gracy-test-server", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
async def make_client() -> t.AsyncIterator[t.Callable[..., t.Awaitable[t.Any]]]:
    built: list[t.Any] = []

    async def factory(cls: type, **kwargs: t.Any) -> t.Any:
        client = await cls(**kwargs).build()
        built.append(client)
        return client

    yield factory

    for client in built:
        await client.aclose()
