import typing as t
from functools import wraps

import httpx

from gracy.exceptions import GracyReplayRequestNotFound

from .storages._base import GracyReplay

httpx_func_type = t.Callable[..., t.Awaitable[httpx.Response]]


def record_mode(replay: GracyReplay, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        httpx_response = await httpx_request_func(*args, **kwargs)
        await replay.storage.record(httpx_response)

        return httpx_response

    return _wrapper


def replay_mode(replay: GracyReplay, client: httpx.AsyncClient, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        request = client.build_request(*args, **kwargs)

        stored_response = await replay.storage.load(request, replay.discard_replays_older_than)

        return stored_response

    return _wrapper


def smart_replay_mode(replay: GracyReplay, client: httpx.AsyncClient, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        request = client.build_request(*args, **kwargs)

        try:
            stored_response = await replay.storage.load(request, replay.discard_replays_older_than)

        except GracyReplayRequestNotFound:
            httpx_response = await httpx_request_func(*args, **kwargs)
            await replay.storage.record(httpx_response)
            response = httpx_response

        else:
            response = stored_response

        return response

    return _wrapper
