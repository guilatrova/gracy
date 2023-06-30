from __future__ import annotations

import typing as t
from functools import wraps

import httpx

from gracy._general import extract_request_kwargs
from gracy.exceptions import GracyReplayRequestNotFound

from .storages._base import GracyReplay

httpx_func_type = t.Callable[..., t.Awaitable[httpx.Response]]


def record_mode(replay: GracyReplay, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        httpx_response = await httpx_request_func(*args, **kwargs)
        await replay.storage.record(httpx_response)
        replay.inc_record()

        return httpx_response

    return _wrapper


def replay_mode(replay: GracyReplay, client: httpx.AsyncClient, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        request_kwargs = extract_request_kwargs(kwargs)
        request = client.build_request(*args, **request_kwargs)

        stored_response = await replay.storage.load(
            request,
            replay.discard_replays_older_than,
            replay.discard_bad_responses,
        )
        replay.inc_replay()

        return stored_response

    return _wrapper


def smart_replay_mode(replay: GracyReplay, client: httpx.AsyncClient, httpx_request_func: httpx_func_type):
    @wraps(httpx_request_func)
    async def _wrapper(*args: t.Any, **kwargs: t.Any):
        request_kwargs = extract_request_kwargs(kwargs)
        request = client.build_request(*args, **request_kwargs)

        try:
            stored_response = await replay.storage.load(
                request,
                replay.discard_replays_older_than,
                replay.discard_bad_responses,
            )

        except GracyReplayRequestNotFound:
            httpx_response = await httpx_request_func(*args, **kwargs)
            await replay.storage.record(httpx_response)
            response = httpx_response
            replay.inc_record()

        else:
            response = stored_response
            replay.inc_replay()

        return response

    return _wrapper
