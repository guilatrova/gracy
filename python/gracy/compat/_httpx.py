"""gracy.compat httpx adapter - duck-typed httpx clients over the gracy pipeline.

One-line swap::

    from gracy.compat import httpx        # instead of `import httpx`

    async with httpx.AsyncClient(base_url="https://api.example.com") as client:
        resp = await client.get("/pokemon/mew", params={"limit": 5})
        resp.raise_for_status()
        data = resp.json()

Every request runs through the FULL gracy pipeline (queue, throttle, retry,
metrics, replay) - pass ``config=GracyConfig(...)`` to the client constructor
to enable policies (the httpx idiom: configuration lives on the client).

Semantics follow httpx, not gracy: non-2xx responses are RETURNED, not
raised - call :meth:`CompatResponse.raise_for_status` to get an
:class:`HTTPStatusError`. Transport failures (timeouts, connect errors)
still raise ``gracy.GracyRequestFailed``.

Pure sugar over the public Gracy API - no pipeline forks (V2_PLAN.md §16.1).
"""

from __future__ import annotations

import asyncio
import threading
import typing as t
from datetime import timedelta
from urllib.parse import urlencode

from gracy._types import UNSET, Response, Unset
from gracy.client import Gracy
from gracy.config import GracyConfig
from gracy.exceptions import GracyClientClosedError, GracyException, GracyResponseError

if t.TYPE_CHECKING:
    from gracy._protocols import Transport

__all__ = [
    "AsyncClient",
    "Client",
    "CompatResponse",
    "HTTPStatusError",
    "Headers",
]

_QueryValue = t.Any
_QueryParams = t.Mapping[str, _QueryValue]
_HeaderMap = t.Mapping[str, str]


# --------------------------------------------------------------------------- errors


class HTTPStatusError(GracyException):
    """httpx-shaped error raised by :meth:`CompatResponse.raise_for_status`.

    Carries ``.response`` (the :class:`CompatResponse`) and ``.request``
    (always ``None`` here - gracy does not expose a request object, but the
    attribute exists so httpx-style handlers keep working).
    """

    def __init__(
        self,
        message: str,
        *,
        request: t.Any = None,
        response: CompatResponse | None = None,
    ) -> None:
        self.request = request
        self.response = response
        super().__init__(message)


# --------------------------------------------------------------------------- headers


class Headers(t.Mapping[str, str]):
    """Case-insensitive read-only header mapping (httpx-shaped)."""

    def __init__(self, raw: tuple[tuple[str, str], ...]) -> None:
        # gracy Response headers already carry lowercase keys.
        self._store: dict[str, str] = {k.lower(): v for k, v in raw}

    def __getitem__(self, key: str) -> str:
        return self._store[key.lower()]

    def get(self, key: str, default: t.Any = None) -> t.Any:
        return self._store.get(key.lower(), default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in self._store

    def __iter__(self) -> t.Iterator[str]:
        return iter(self._store)

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"Headers({self._store!r})"


# --------------------------------------------------------------------------- response


class CompatResponse:
    """httpx-shaped view over a gracy :class:`~gracy._types.Response`."""

    def __init__(self, raw: Response) -> None:
        self._raw = raw
        self.status_code: int = raw.status
        self.headers: Headers = Headers(raw.headers)
        self.url: str = raw.url
        self.elapsed: timedelta = timedelta(seconds=raw.elapsed)
        self.http_version: str = raw.http_version

    # -- status flags (httpx semantics)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        return 500 <= self.status_code < 600

    @property
    def is_error(self) -> bool:
        return self.is_client_error or self.is_server_error

    # -- body

    @property
    def content(self) -> bytes:
        return self._raw.body

    @property
    def text(self) -> str:
        return self._raw.text

    def json(self) -> t.Any:
        return self._raw.json()

    # -- raising

    def raise_for_status(self) -> CompatResponse:
        if self.is_success:
            return self
        raise HTTPStatusError(
            f"{self.status_code} error for url {self.url}",
            request=None,
            response=self,
        )

    def __repr__(self) -> str:
        return f"<CompatResponse [{self.status_code}] {self.url}>"


# --------------------------------------------------------------------------- async client


class AsyncClient:
    """httpx.AsyncClient duck-type running requests through a private Gracy client.

    ``params``/``headers`` given here apply to every request and merge UNDER
    per-call values. Relative ``url`` arguments join onto ``base_url``;
    absolute http(s) urls pass through untouched.
    """

    def __init__(
        self,
        base_url: str = "",
        headers: _HeaderMap | None = None,
        timeout: float | None = None,
        params: _QueryParams | None = None,
        config: GracyConfig | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._base_url = base_url
        self._headers: dict[str, str] = dict(headers or {})
        self._params: dict[str, _QueryValue] = dict(params or {})
        self._timeout = timeout
        self._config = config
        self._transport = transport
        self._gracy: Gracy | None = None

    # -- lifecycle

    async def __aenter__(self) -> AsyncClient:
        if self._gracy is None:
            client = Gracy(transport=self._transport)
            # Instance attributes shadow Gracy's declarative class attrs -
            # build() reads self.base_url / self.config, so no subclass needed.
            client.base_url = self._base_url
            base = GracyConfig(log_errors=None)  # httpx never logs; errors are return values
            if self._timeout is not None:
                base = GracyConfig(log_errors=None, timeout=self._timeout)
            client.config = self._config.merged_under(base) if self._config is not None else base
            await client.build()
            self._gracy = client
        return self

    async def __aexit__(self, *exc_info: t.Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._gracy is not None:
            await self._gracy.aclose()
            self._gracy = None

    # -- core request

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: _QueryParams | None = None,
        json: t.Any = None,
        content: bytes | str | None = None,
        data: t.Any = None,
        headers: _HeaderMap | None = None,
        timeout: float | None = None,
    ) -> CompatResponse:
        if self._gracy is None:
            raise GracyClientClosedError(
                "AsyncClient not started - use 'async with AsyncClient(...) as client:'"
            )

        merged_params: dict[str, _QueryValue] = {**self._params, **dict(params or {})}
        merged_headers: dict[str, str] = {
            **{k.lower(): v for k, v in self._headers.items()},
            **{k.lower(): v for k, v in (headers or {}).items()},
        }

        body: bytes | str | None = content
        if data is not None:
            if isinstance(data, t.Mapping):
                body = urlencode(data)
                merged_headers.setdefault("content-type", "application/x-www-form-urlencoded")
            else:
                body = data

        call_timeout: float | None | Unset = UNSET if timeout is None else timeout

        try:
            result = await self._gracy.request(
                method,
                url,
                params=merged_params or None,
                headers=merged_headers or None,
                content=body,
                json=json,
                timeout=call_timeout,
            )
        except GracyResponseError as exc:
            # httpx semantics: non-2xx is a RESPONSE, not an exception -
            # unwrap gracy's status-policy failure back into a response.
            if exc.response is None:
                raise
            return CompatResponse(exc.response)
        assert isinstance(result, Response)  # decode_as=None -> raw gracy Response
        return CompatResponse(result)

    # -- verbs

    async def get(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: t.Any) -> CompatResponse:
        return await self.request("OPTIONS", url, **kwargs)


# --------------------------------------------------------------------------- sync client


class Client:
    """httpx.Client duck-type: drives an :class:`AsyncClient` through a private
    daemon loop thread (same pattern as ``gracy.SyncGracy``)."""

    def __init__(
        self,
        base_url: str = "",
        headers: _HeaderMap | None = None,
        timeout: float | None = None,
        params: _QueryParams | None = None,
        config: GracyConfig | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._async = AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            params=params,
            config=config,
            transport=transport,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle

    def __enter__(self) -> Client:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="gracy-compat-httpx", daemon=True
        )
        self._thread.start()
        self._submit(self._async.__aenter__()).result(30)
        return self

    def __exit__(self, *exc_info: t.Any) -> bool:
        try:
            self._submit(self._async.aclose()).result(30)
        finally:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(5)
            self._loop = None
            self._thread = None
        return False

    def close(self) -> None:
        self.__exit__()

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro: t.Coroutine[t.Any, t.Any, t.Any]) -> t.Any:
        if self._loop is None:
            raise GracyClientClosedError("Client used outside its 'with' block")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # -- core request

    def request(self, method: str, url: str, **kwargs: t.Any) -> CompatResponse:
        return t.cast(CompatResponse, self._submit(self._async.request(method, url, **kwargs)).result())

    # -- verbs

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
