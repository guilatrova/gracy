import typing as t
from functools import wraps

import httpx

from ._storages import GracyReplayStorage

httpx_func_type = t.Callable[..., t.Awaitable[httpx.Response]]


def record_replay_result(strategy: GracyReplayStorage, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        httpx_response = await httpx_request_func(*args, **kwargs)
        await strategy.record(httpx_response)

        return httpx_response

    return _wrapper


def read_replay(strategy: GracyReplayStorage, client: httpx.AsyncClient, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        request = client.build_request(*args, **kwargs)

        stored_response = await strategy.load(request)

        return stored_response

    return _wrapper
