"""``gracy.compat.requests`` - a sync drop-in duck-type of the ``requests`` API.

The one-line swap::

    from gracy.compat import requests   # instead of `import requests`

    requests.get("https://api.example.com/thing", params={"q": "x"}).json()

Every call runs through the full gracy pipeline (queue/throttle/retry/replay/
reports) hosted by ONE shared async :class:`gracy.Gracy` client living on a
private daemon-thread event loop. ``configure(GracyConfig(...))`` rebuilds
that client, so retry/throttle policies apply to every compat call without
touching call sites.

Differences from real ``requests`` (kept deliberately small):

* Only absolute ``http(s)://`` URLs are supported.
* Transport failures (timeouts, connect errors) raise
  :class:`gracy.exceptions.GracyRequestFailed` - catch ``rq.RequestException``
  (or plain ``Exception``); ``raise_for_status()`` raises :class:`HTTPError`
  exactly like requests.
* Unsupported keyword arguments are ignored with a ``UserWarning``.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import datetime
import http.client
import json as jsonlib
import threading
import typing as t
import warnings
from collections.abc import Mapping, MutableMapping
from urllib.parse import urlencode

from gracy._types import UNSET, Response, Unset
from gracy.client import Gracy
from gracy.config import GracyConfig
from gracy.exceptions import GracyException, GracyRequestFailed, GracyResponseError

__all__ = [
    "CaseInsensitiveDict",
    "CompatResponse",
    "ConnectionError",
    "HTTPError",
    "RequestException",
    "Session",
    "configure",
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "request",
    "shutdown",
]


# --------------------------------------------------------------------------- headers


class CaseInsensitiveDict(MutableMapping):
    """requests-shaped case-insensitive dict: lookups ignore case, iteration
    preserves the case of the last key set."""

    def __init__(self, data: t.Any = None, **kwargs: t.Any) -> None:
        self._store: dict[str, tuple[str, t.Any]] = {}
        if data is not None:
            self.update(data)
        if kwargs:
            self.update(kwargs)

    def __setitem__(self, key: str, value: t.Any) -> None:
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key: str) -> t.Any:
        return self._store[key.lower()][1]

    def __delitem__(self, key: str) -> None:
        del self._store[key.lower()]

    def __iter__(self) -> t.Iterator[str]:
        return (original for original, _ in self._store.values())

    def __len__(self) -> int:
        return len(self._store)

    def lower_items(self) -> t.Iterator[tuple[str, t.Any]]:
        return ((lower, pair[1]) for lower, pair in self._store.items())

    def __eq__(self, other: t.Any) -> bool:
        if isinstance(other, Mapping):
            other = CaseInsensitiveDict(other)
        else:
            return NotImplemented
        return dict(self.lower_items()) == dict(other.lower_items())

    def copy(self) -> CaseInsensitiveDict:
        return CaseInsensitiveDict(dict(self._store.values()))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict(self.items())!r})"


# --------------------------------------------------------------------------- exceptions


class RequestException(GracyException, IOError):
    """requests-shaped base exception (also a GracyException)."""

    def __init__(self, *args: t.Any, response: CompatResponse | None = None, request: t.Any = None) -> None:
        self.response = response
        self.request = request
        super().__init__(*args)


class HTTPError(RequestException):
    """Raised by :meth:`CompatResponse.raise_for_status` for 4xx/5xx statuses."""


class ConnectionError(RequestException):  # noqa: A001 - requests parity
    """Transport-level failure (connect error, timeout, ...). Wraps GracyRequestFailed."""


# --------------------------------------------------------------------------- response


class CompatResponse:
    """requests-shaped view over a :class:`gracy.Response` (fully buffered)."""

    def __init__(self, response: Response) -> None:
        self._response = response
        self.status_code: int = response.status
        self.url: str = response.url
        self.headers: CaseInsensitiveDict = CaseInsensitiveDict(dict(response.headers))
        self.elapsed: datetime.timedelta = datetime.timedelta(seconds=response.elapsed)

    # -- body accessors

    @property
    def content(self) -> bytes:
        return self._response.body

    @property
    def text(self) -> str:
        return self._response.text

    def json(self, **kwargs: t.Any) -> t.Any:
        if not kwargs:
            return self._response.json()  # reuses gracy's parse cache
        return jsonlib.loads(self._response.body, **kwargs)

    def iter_content(self, chunk_size: int | None = 1, decode_unicode: bool = False) -> t.Iterator[t.Any]:
        """Yield the buffered body in ``chunk_size`` slices (requests parity)."""
        body: t.Any = self.text if decode_unicode else self.content
        if chunk_size is None:
            if body:
                yield body
            return
        for start in range(0, len(body), chunk_size):
            yield body[start : start + chunk_size]

    # -- status accessors

    @property
    def ok(self) -> bool:
        """requests semantics: anything below 400 is ok (gracy's is_success is 2xx-only)."""
        return self.status_code < 400

    @property
    def reason(self) -> str:
        return http.client.responses.get(self.status_code, "")

    def raise_for_status(self) -> None:
        """Raise :class:`HTTPError` for 4xx/5xx, with the requests-style message."""
        if 400 <= self.status_code < 500:
            message = f"{self.status_code} Client Error: {self.reason} for url: {self.url}"
        elif 500 <= self.status_code < 600:
            message = f"{self.status_code} Server Error: {self.reason} for url: {self.url}"
        else:
            return
        raise HTTPError(message, response=self)

    # -- dunders

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"<Response [{self.status_code}]>"


# --------------------------------------------------------------------------- the shared engine

# Requests semantics baked into the shared client:
#  * status_policy stays at the library default (2xx validation) so Retry(on=
#    status(...)) keeps firing - the pipeline only retries on validation errors.
#  * on={"default": identity} then swallows the validation error at decode time
#    and hands back the raw Response for EVERY status: non-2xx never raises,
#    only raise_for_status() does. Transport failures still raise (no response).
#  * log_errors=None keeps expected non-2xx traffic out of the error log.
_COMPAT_BASE: t.Final = GracyConfig(on={"default": lambda response: response}, log_errors=None)


class _CompatClient(Gracy):
    """The shared ad-hoc client behind every compat call. No declared endpoints."""

    base_url = ""


class _SyncBridge:
    """A daemon thread running an asyncio loop hosting ONE shared async client."""

    def __init__(self, config: GracyConfig | None, transport: t.Any) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="gracy-compat-requests", daemon=True
        )
        self._thread.start()
        try:
            self.client: Gracy = self.run(self._build(config, transport), timeout=30)
        except BaseException:
            self._stop_loop()
            raise

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _build(self, config: GracyConfig | None, transport: t.Any) -> Gracy:
        client = _CompatClient(transport=transport)
        effective = _COMPAT_BASE if config is None else config.merged_under(_COMPAT_BASE)
        client.config = effective  # instance attr: build() reads self.config
        return await client.build()

    def run(self, coro: t.Coroutine[t.Any, t.Any, t.Any], timeout: float | None = None) -> t.Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def close(self) -> None:
        try:
            self.run(self.client.aclose(), timeout=30)
        finally:
            self._stop_loop()

    def _stop_loop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)


_lock = threading.RLock()
_bridge: _SyncBridge | None = None
_configured: tuple[GracyConfig | None, t.Any] = (None, None)


def _get_bridge() -> _SyncBridge:
    global _bridge
    with _lock:
        if _bridge is None:
            config, transport = _configured
            _bridge = _SyncBridge(config, transport)
        return _bridge


def configure(config: GracyConfig | None = None, *, transport: t.Any = None) -> None:
    """Tear down the shared client and rebuild it with ``config`` (and optionally
    a custom transport). Retry/throttle/queue policies now apply to every
    compat call. ``configure()`` with no arguments restores the defaults."""
    global _configured
    with _lock:
        shutdown()
        _configured = (config, transport)
        _get_bridge()  # build eagerly so configuration errors surface here


def shutdown() -> None:
    """Close the shared client and its loop thread. Idempotent; the next call
    to any verb lazily rebuilds with the last configure()d settings."""
    global _bridge
    with _lock:
        if _bridge is not None:
            bridge, _bridge = _bridge, None
            bridge.close()


atexit.register(shutdown)


# --------------------------------------------------------------------------- request building


def _prepare_headers(
    session_headers: t.Mapping[str, t.Any] | None,
    headers: t.Mapping[str, t.Any] | None,
    auth: t.Any,
) -> CaseInsensitiveDict:
    merged = CaseInsensitiveDict(session_headers)
    if headers:
        merged.update(headers)
    if auth is not None:
        if not (isinstance(auth, (tuple, list)) and len(auth) == 2):
            raise TypeError(
                f"gracy.compat.requests only supports basic auth as a (user, password) tuple, got {auth!r}"
            )
        user, password = auth
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        merged["Authorization"] = f"Basic {token}"
    return merged


def _prepare_body(
    data: t.Any, json: t.Any, headers: CaseInsensitiveDict
) -> tuple[t.Any, bytes | str | None]:
    """Split requests' data=/json= into gracy's json=/content= pair."""
    if json is not None:
        return json, None
    if data is None:
        return None, None
    if isinstance(data, (bytes, str)):
        return None, data
    if isinstance(data, Mapping) or (
        isinstance(data, (list, tuple)) and all(isinstance(item, tuple) for item in data)
    ):
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return None, urlencode(data, doseq=True)
    raise TypeError(
        f"Unsupported data= value of type {type(data).__name__}; "
        "use a dict / list of pairs (form-encoded) or str / bytes (raw body)"
    )


def _prepare_timeout(timeout: t.Any) -> float | None | Unset:
    if timeout is None:
        return UNSET  # not specified: use the configured/library default
    if isinstance(timeout, (tuple, list)):
        return max(float(value) for value in timeout)  # (connect, read) -> single budget
    return float(timeout)


def _do_request(
    method: str,
    url: str,
    *,
    params: t.Mapping[str, t.Any] | None = None,
    data: t.Any = None,
    json: t.Any = None,
    headers: t.Mapping[str, t.Any] | None = None,
    timeout: t.Any = None,
    auth: t.Any = None,
    session_headers: t.Mapping[str, t.Any] | None = None,
    **ignored: t.Any,
) -> CompatResponse:
    if ignored:
        warnings.warn(
            f"gracy.compat.requests ignores unsupported keyword arguments: {sorted(ignored)}",
            UserWarning,
            stacklevel=3,
        )
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(
            f"gracy.compat.requests only supports absolute URLs, got {url!r} - "
            "include the scheme, e.g. 'https://api.example.com/path'"
        )

    merged_headers = _prepare_headers(session_headers, headers, auth)
    json_body, content = _prepare_body(data, json, merged_headers)

    bridge = _get_bridge()
    try:
        response: Response = bridge.run(
            bridge.client.request(
                method.upper(),
                url,
                params=params,
                headers={str(k): str(v) for k, v in merged_headers.items()},
                content=content,
                json=json_body,
                decode_as=None,
                timeout=_prepare_timeout(timeout),
            )
        )
    except GracyResponseError as exc:
        # Only reachable when a user configure() overrode the compat on= map:
        # keep requests semantics - a completed response never raises here.
        if exc.response is None:
            raise
        response = exc.response
    except GracyRequestFailed as exc:
        # requests idiom parity: `except requests.RequestException` keeps working.
        raise ConnectionError(str(exc)) from exc
    return CompatResponse(response)


# --------------------------------------------------------------------------- module-level verbs


def request(method: str, url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request(method, url, **kwargs)


def get(url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request("GET", url, **kwargs)


def post(url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request("POST", url, **kwargs)


def put(url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request("PUT", url, **kwargs)


def patch(url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request("PATCH", url, **kwargs)


def delete(url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request("DELETE", url, **kwargs)


def head(url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request("HEAD", url, **kwargs)


def options(url: str, **kwargs: t.Any) -> CompatResponse:
    return _do_request("OPTIONS", url, **kwargs)


# --------------------------------------------------------------------------- sessions


class Session:
    """requests-shaped session: persistent headers over the shared engine.

    ``close()`` only drops this session's state - the module-level engine is
    shared and stays alive for other callers (use :func:`shutdown` for that).
    """

    def __init__(self) -> None:
        self.headers: CaseInsensitiveDict = CaseInsensitiveDict()

    def request(self, method: str, url: str, **kwargs: t.Any) -> CompatResponse:
        return _do_request(method, url, session_headers=self.headers, **kwargs)

    def get(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return self.request("OPTIONS", url, **kwargs)

    def close(self) -> None:
        self.headers = CaseInsensitiveDict()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc_info: t.Any) -> None:
        self.close()
