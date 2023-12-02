from __future__ import annotations

import typing as t

RESP_T = t.TypeVar("RESP_T")
TOKEN_T = t.TypeVar("TOKEN_T")


class GracyPaginator(t.Generic[RESP_T, TOKEN_T]):
    def __init__(
        self,
        gracy_func: t.Callable[..., t.Awaitable[RESP_T]],
        has_next: t.Callable[[t.Optional[RESP_T]], bool],
        initial_token: TOKEN_T,
        page_size: int = 20,
        get_next_token: t.Optional[t.Callable[[RESP_T, TOKEN_T], TOKEN_T]] = None,
        get_prev_token: t.Optional[t.Callable[[TOKEN_T], TOKEN_T]] = None,
        prepare_params: t.Optional[t.Callable[[TOKEN_T, int], t.Dict[str, t.Any]]] = None,
        has_prev: t.Optional[t.Callable[[TOKEN_T], bool]] = None,
    ):
        self.has_next = has_next

        self._endpoint_func = gracy_func
        self._custom_has_prev = has_prev
        self._prepare_endpoint_params = prepare_params
        self._get_next_token = get_next_token
        self._get_prev_token = get_prev_token

        self._token = initial_token
        self._page_size = page_size
        self._cur_resp: t.Optional[RESP_T] = None

    def _prepare_params(
        self,
        token: TOKEN_T,
        page_size: int,
    ) -> t.Dict[str, t.Any]:
        if self._prepare_endpoint_params:
            return self._prepare_endpoint_params(token, page_size)

        params = dict(token=token, limit=self._page_size)
        return params

    async def _fetch_page(self) -> RESP_T:
        params = self._prepare_params(self._token, page_size=20)
        self._cur_resp = await self._endpoint_func(**params)
        return self._cur_resp

    def has_prev(self, token: TOKEN_T) -> bool:
        if self._custom_has_prev:
            return self._custom_has_prev(token)

        return False

    def _calculate_next_token(self, resp: RESP_T, token: TOKEN_T) -> TOKEN_T:
        if self._get_next_token:
            return self._get_next_token(resp, token)

        raise NotImplementedError("GracyPaginator requires you to setup get_next_token")  # noqa: TRY003

    def _calculate_prev_token(self, token: TOKEN_T) -> TOKEN_T:
        if self._get_prev_token:
            return self._get_prev_token(token)

        raise NotImplementedError("GracyPaginator requires you to setup get_prev_token")  # noqa: TRY003

    def set_page(self, token: TOKEN_T) -> None:
        self._token = token

    async def next_page(self) -> RESP_T | None:
        if not self.has_next(self._cur_resp):
            return None

        page_result = await self._fetch_page()

        self._token = self._calculate_next_token(page_result, self._token)

        return page_result

    async def prev_page(self):
        if not self.has_prev(self._token):
            return None

        self._token = self._calculate_prev_token(self._token)

        page_result = await self._fetch_page()

        return page_result

    def __aiter__(self):
        return self

    async def __anext__(self):
        page = await self.next_page()
        if page is None:
            raise StopAsyncIteration
        return page


class GracyOffsetPaginator(t.Generic[RESP_T], GracyPaginator[RESP_T, int]):
    def __init__(
        self,
        gracy_func: t.Callable[..., t.Awaitable[RESP_T]],
        has_next: t.Callable[[RESP_T | None], bool],
        page_size: int = 20,
        prepare_params: t.Callable[[int, int], t.Dict[str, t.Any]] | None = None,
        has_prev: t.Callable[[int], bool] | None = None,
    ):
        super().__init__(
            gracy_func,
            has_next,
            initial_token=0,
            page_size=page_size,
            prepare_params=prepare_params,
            has_prev=has_prev,
        )

    def _prepare_params(
        self,
        token: int,
        page_size: int,
    ) -> t.Dict[str, t.Any]:
        if self._prepare_endpoint_params:
            return self._prepare_endpoint_params(token, page_size)

        params = dict(offset=token, limit=self._page_size)
        return params

    def has_prev(self, token: int) -> bool:
        if self._custom_has_prev:
            return self._custom_has_prev(token)

        return token > 0

    def _calculate_next_token(self, resp: RESP_T, token: int) -> int:
        return token + self._page_size

    def _calculate_prev_token(self, token: int) -> int:
        return token - self._page_size
