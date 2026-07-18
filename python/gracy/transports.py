"""Transports: send one RequestSpec, return a buffered Response. No policy inside.

Implementations of the `gracy._protocols.Transport` protocol:

- HttpxTransport - full httpx client under the hood (escape hatch, ships in 2.0).
  Keeps respx / pytest-httpx / ASGI-transport workflows alive. Swapping the
  transport does NOT bypass the queue: the scheduler permit is granted first,
  then this transport sends.
- MockTransport - fnmatch-glob pattern -> canned response, for tests.
- RustTransport - the 2.0 default (reqwest via gracy._core); implemented in
  gracy.engine and re-exported here.

Transport failures propagate RAW: the pipeline is the single point that wraps
them into GracyRequestFailed.
"""

from __future__ import annotations

import fnmatch
import json
import time
import typing as t
from dataclasses import dataclass, field

from gracy._types import RequestSpec, Response

if t.TYPE_CHECKING:
    import httpx

__all__ = ["TransportConfig", "HttpxTransport", "MockTransport", "RustTransport"]


# --------------------------------------------------------------------------- config


@dataclass(frozen=True, slots=True)
class TransportConfig:
    """Connection-level knobs shared by every transport implementation."""

    base_headers: t.Mapping[str, str] = field(default_factory=dict)
    proxy: str | None = None
    verify_tls: bool = True
    follow_redirects: bool = True
    http2: bool = False


# --------------------------------------------------------------------------- httpx


def _import_httpx() -> t.Any:
    try:
        import httpx as _httpx
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "HttpxTransport requires the 'httpx' package, which is not installed. "
            "Install it with: pip install gracy[httpx]"
        ) from e
    return _httpx


class HttpxTransport:
    """Transport backed by httpx.AsyncClient.

    Use this when you need anything reqwest can't give you: custom SSLContext /
    mTLS, unix domain sockets, httpx.Auth flows, ASGI/WSGI transports, or the
    respx / pytest-httpx mocking ecosystem - inject your own configured
    AsyncClient via `client=` and gracy will use it (and will NOT close it).
    """

    def __init__(
        self,
        config: TransportConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or TransportConfig()
        self._client: httpx.AsyncClient | None = client
        self._owns_client = client is None

    async def start(self) -> None:
        if self._client is not None:
            return
        httpx_mod = _import_httpx()
        self._client = httpx_mod.AsyncClient(
            proxy=self._config.proxy,
            verify=self._config.verify_tls,
            follow_redirects=self._config.follow_redirects,
            http2=self._config.http2,
        )

    async def send(self, spec: RequestSpec) -> Response:
        """httpx exceptions (timeouts, connect errors, ...) propagate raw."""
        if self._client is None:
            await self.start()
        client = self._client
        assert client is not None
        httpx_mod = _import_httpx()

        headers: dict[str, str] = {k.lower(): v for k, v in self._config.base_headers.items()}
        headers.update(dict(spec.headers))

        # RequestSpec.timeout=None is an explicit opt-out (no timeout at all);
        # never fall through to httpx's own client default.
        timeout: t.Any = spec.timeout if spec.timeout is not None else httpx_mod.Timeout(None)

        start = time.monotonic()
        resp = await client.request(
            spec.method,
            spec.url,
            headers=headers,
            content=spec.content,
            timeout=timeout,
        )
        elapsed = time.monotonic() - start

        return Response(
            status=resp.status_code,
            headers=tuple(sorted((k.lower(), v) for k, v in resp.headers.multi_items())),
            body=resp.content,
            url=str(resp.url),
            elapsed=elapsed,
            http_version=resp.http_version or "HTTP/1.1",
        )

    async def aclose(self) -> None:
        """Close the client we created; an injected client stays open (caller owns it)."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- mock


_MockValue = t.Any
"""Response | dict | list (json body) | tuple[int, dict|str|bytes] | int | callable(spec)."""


class MockTransport:
    """Pattern -> canned response transport for tests (also exported via gracy.testing).

    Keys are fnmatch glob patterns, tried against "METHOD url" first, then
    against the plain url - first match (insertion order) wins. Values:

    - dict / list      -> json body, status = default_status
    - (status, body)   -> body is dict (json) | str | bytes
    - int              -> status only, empty body
    - callable(spec)   -> Response | any value above; may raise to simulate
                          transport errors (the pipeline wraps them)
    - Response         -> returned as-is

    Every RequestSpec sent is recorded in `self.calls`, matched or not.
    Unmatched requests get an empty-json 404.
    """

    def __init__(self, responses: t.Mapping[str, _MockValue] | None = None, default_status: int = 200) -> None:
        self.responses: dict[str, _MockValue] = dict(responses or {})
        self.default_status = default_status
        self.calls: list[RequestSpec] = []

    async def start(self) -> None:
        return None

    async def send(self, spec: RequestSpec) -> Response:
        self.calls.append(spec)
        candidates = (f"{spec.method.upper()} {spec.url}", spec.url)
        for candidate in candidates:
            for pattern, value in self.responses.items():
                if fnmatch.fnmatchcase(candidate, pattern):
                    return self._to_response(spec, value)
        return self._build(spec, 404, b"{}", "application/json")

    async def aclose(self) -> None:
        return None

    # -- conversion helpers

    def _to_response(self, spec: RequestSpec, value: _MockValue) -> Response:
        if isinstance(value, Response):
            return value
        if callable(value) and not isinstance(value, type):
            value = value(spec)  # may raise: simulates a transport error
            if isinstance(value, Response):
                return value
        if isinstance(value, bool):
            raise TypeError(f"MockTransport value for {spec.url!r} cannot be a bool: {value!r}")
        if isinstance(value, int):
            return self._build(spec, value, b"", "application/json")
        if isinstance(value, tuple):
            if len(value) != 2 or not isinstance(value[0], int):
                raise TypeError(f"MockTransport tuple values must be (status:int, body), got {value!r}")
            body, content_type = self._encode_body(value[1])
            return self._build(spec, value[0], body, content_type)
        if isinstance(value, (dict, list, str, bytes)):
            body, content_type = self._encode_body(value)
            return self._build(spec, self.default_status, body, content_type)
        raise TypeError(
            f"MockTransport can't build a response from {type(value).__name__!r}; "
            "use dict/list, (status, body), int, callable(spec), or a gracy Response"
        )

    @staticmethod
    def _encode_body(body: t.Any) -> tuple[bytes, str]:
        if isinstance(body, (dict, list)):
            return json.dumps(body).encode("utf-8"), "application/json"
        if isinstance(body, str):
            return body.encode("utf-8"), "text/plain; charset=utf-8"
        if isinstance(body, bytes):
            return body, "application/octet-stream"
        raise TypeError(f"MockTransport body must be dict, list, str or bytes, got {type(body).__name__!r}")

    @staticmethod
    def _build(spec: RequestSpec, status: int, body: bytes, content_type: str) -> Response:
        return Response(
            status=status,
            headers=(("content-type", content_type),),
            body=body,
            url=spec.url,
            elapsed=0.0,
            http_version="HTTP/1.1",
        )


# --------------------------------------------------------------------------- rust

# The real implementation lives in gracy.engine (which owns engine selection
# and must not be imported at the top of this module: engine.py imports
# TransportConfig/HttpxTransport from here lazily). Re-exported so
# `from gracy.transports import RustTransport` (and the gracy top-level
# export) keep working.
from gracy.engine import RustTransport  # noqa: E402
