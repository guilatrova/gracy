"""Async paginators over gracy endpoint functions.

v2 fixes over v1 (`src/gracy/_paginator.py`):
  * ``page_size`` is honored — v1 hardcoded ``page_size=20`` in its fetch
    path regardless of what the user configured (V2_PLAN.md §12, bug #4).
  * Extra ``kwargs`` are forwarded to the endpoint function on every fetch.
  * Typed generics: ``GracyPaginator[RESP_T]`` works under pyright strict.

IMPORTANT — the ``has_next`` contract:
    ``has_next`` is called with ``None`` BEFORE the first fetch (there is no
    response yet). It MUST return ``True`` for ``None``, otherwise the
    paginator never fetches a single page::

        def has_next(resp: dict | None) -> bool:
            if resp is None:
                return True  # first page not fetched yet
            return resp["next"] is not None
"""

from __future__ import annotations

import typing as t

RESP_T = t.TypeVar("RESP_T")


class GracyPaginator(t.Generic[RESP_T]):
    """Generic async paginator. Iterate with ``async for`` or call ``next_page()``.

    Either pass ``prepare_params`` AND ``get_next_token``, or use a subclass
    that provides defaults (e.g. :class:`GracyOffsetPaginator`).

    ``has_next`` is invoked with ``None`` before the first fetch and must
    return ``True`` for ``None`` (see module docstring).
    """

    def __init__(
        self,
        gracy_func: t.Callable[..., t.Awaitable[RESP_T]],
        has_next: t.Callable[[RESP_T | None], bool],
        page_size: int = 20,
        prepare_params: t.Callable[[t.Any, int], dict[str, t.Any]] | None = None,
        get_next_token: t.Callable[[t.Any, RESP_T], t.Any] | None = None,
        initial_token: t.Any = 0,
        kwargs: dict[str, t.Any] | None = None,
    ) -> None:
        self._gracy_func = gracy_func
        self._has_next = has_next
        self._page_size = page_size
        self._prepare_params = prepare_params
        self._get_next_token = get_next_token
        self._token: t.Any = initial_token
        self._kwargs: dict[str, t.Any] = dict(kwargs) if kwargs else {}
        self._cur_resp: RESP_T | None = None

    # ------------------------------------------------------------- overridables

    def _default_prepare(self, token: t.Any, page_size: int) -> dict[str, t.Any]:
        raise NotImplementedError(
            "GracyPaginator does not know how to build request params. "
            "Pass prepare_params=... or use GracyOffsetPaginator."
        )

    def _default_next(self, token: t.Any, resp: RESP_T) -> t.Any:
        raise NotImplementedError(
            "GracyPaginator does not know how to advance the page token. "
            "Pass get_next_token=... or use GracyOffsetPaginator."
        )

    def _step_back(self, token: t.Any) -> t.Any:
        raise NotImplementedError(
            "GracyPaginator does not know how to step the token back. "
            "Use GracyOffsetPaginator or override _step_back()."
        )

    # ------------------------------------------------------------------- fetch

    async def _fetch(self) -> RESP_T:
        prepare = self._prepare_params or self._default_prepare
        params = prepare(self._token, self._page_size)  # configured page_size (v1 hardcoded 20)
        merged = {**params, **self._kwargs}
        resp = await self._gracy_func(**merged)
        self._cur_resp = resp
        return resp

    # -------------------------------------------------------------- public API

    async def next_page(self) -> RESP_T | None:
        """Fetch the next page, or ``None`` when ``has_next`` says we're done."""
        if not self._has_next(self._cur_resp):
            return None

        resp = await self._fetch()
        advance = self._get_next_token or self._default_next
        self._token = advance(self._token, resp)
        return resp

    async def prev_page(self) -> RESP_T | None:
        """Step the token back one page and fetch it (no ``has_next`` gating)."""
        self._token = self._step_back(self._token)
        return await self._fetch()

    def set_page(self, token: t.Any) -> None:
        """Jump to a raw token. Resets the current response, so ``has_next``
        is evaluated against ``None`` on the following ``next_page()``."""
        self._token = token
        self._cur_resp = None

    def __aiter__(self) -> GracyPaginator[RESP_T]:
        return self

    async def __anext__(self) -> RESP_T:
        page = await self.next_page()
        if page is None:
            raise StopAsyncIteration
        return page


class GracyOffsetPaginator(GracyPaginator[RESP_T]):
    """Offset/limit paginator: token is an ``int`` offset starting at 0.

    Defaults (each overridable via the base-class arguments):
      * params: ``{"offset": token, "limit": page_size}``
      * next token: ``token + page_size``
      * step back: ``max(0, token - page_size)``
    """

    def __init__(
        self,
        gracy_func: t.Callable[..., t.Awaitable[RESP_T]],
        has_next: t.Callable[[RESP_T | None], bool],
        page_size: int = 20,
        prepare_params: t.Callable[[int, int], dict[str, t.Any]] | None = None,
        get_next_token: t.Callable[[int, RESP_T], int] | None = None,
        initial_token: int = 0,
        kwargs: dict[str, t.Any] | None = None,
    ) -> None:
        super().__init__(
            gracy_func,
            has_next,
            page_size=page_size,
            prepare_params=prepare_params,
            get_next_token=get_next_token,
            initial_token=initial_token,
            kwargs=kwargs,
        )

    def _default_prepare(self, token: int, page_size: int) -> dict[str, t.Any]:
        return {"offset": token, "limit": page_size}

    def _default_next(self, token: int, resp: RESP_T) -> int:
        return token + self._page_size

    def _step_back(self, token: int) -> int:
        return max(0, token - self._page_size)
