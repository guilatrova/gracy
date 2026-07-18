"""Retry behavior: status/exception matching, exhaustion modes, Retry-After,
per-status delay overrides, the stale-response regression, RetryState flowing
into after-hooks, and the gracy.testing.retries_off() switch.

Server-backed tests use the shared `test_server` fixture through HttpxTransport;
exception scripting uses gracy.testing.MockTransport callables.
"""

from __future__ import annotations

import asyncio
import time
import typing as t
import uuid

import pytest

import gracy
from gracy import (
    Gracy,
    GracyConfig,
    GracyRequestFailed,
    NonOkResponse,
    Retry,
    RetryState,
    get,
)
from gracy.testing import MockTransport

MOCK_BASE = "https://api.test"

WAIT_FOR_TIMEOUT = 15


def unique_key() -> str:
    return uuid.uuid4().hex


def scripted(*steps: t.Any) -> t.Callable[[t.Any], t.Any]:
    """MockTransport callable: consume `steps` one per call; last step repeats.

    A step that is an exception INSTANCE is raised (simulated transport error);
    anything else is returned as a MockTransport value (tuple/dict/int/...).
    """
    calls = {"n": 0}

    def handler(spec: t.Any) -> t.Any:
        idx = min(calls["n"], len(steps) - 1)
        calls["n"] += 1
        step = steps[idx]
        if isinstance(step, BaseException):
            raise step
        return step

    return handler


# --------------------------------------------------------------------------- status retry (real server)


async def test_retry_on_status_until_success_exact_call_count(test_server, make_client):
    class FlakyApi(Gracy):
        base_url = test_server

        @get("/flaky/{key}", retry=Retry(on=gracy.status(503), attempts=3, wait=0.01))
        async def flaky(self, key, fail_times) -> dict: ...

    api = await make_client(FlakyApi)
    result = await asyncio.wait_for(api.flaky(unique_key(), fail_times=2), WAIT_FOR_TIMEOUT)

    assert result["ok"] is True
    assert result["calls"] == 3  # 1 initial + exactly 2 retries; success stopped the loop


async def test_no_retry_when_status_does_not_match(test_server, make_client):
    key = unique_key()

    class FlakyApi(Gracy):
        base_url = test_server

        @get("/flaky/{key}", retry=Retry(on=gracy.status(429), attempts=3, wait=0.01))
        async def flaky(self, key, fail_times) -> dict: ...

    api = await make_client(FlakyApi)
    with pytest.raises(NonOkResponse):
        await asyncio.wait_for(api.flaky(key, fail_times=1), WAIT_FOR_TIMEOUT)

    # 503 does not match on=status(429): the server saw exactly one call.
    result = await asyncio.wait_for(api.flaky(key, fail_times=1), WAIT_FOR_TIMEOUT)
    assert result["calls"] == 2


# --------------------------------------------------------------------------- exception retry (MockTransport)


async def test_retry_on_exception_class_until_success(make_client):
    transport = MockTransport(
        {
            f"{MOCK_BASE}/thing": scripted(
                ConnectionError("boom 1"),
                ConnectionError("boom 2"),
                {"ok": True},
            )
        }
    )

    class Api(Gracy):
        base_url = MOCK_BASE

        @get("/thing", retry=Retry(on=ConnectionError, attempts=3, wait=0))
        async def thing(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    result = await asyncio.wait_for(api.thing(), WAIT_FOR_TIMEOUT)

    assert result == {"ok": True}
    assert len(transport.calls) == 3  # 2 failures + the succeeding attempt


async def test_exception_not_matching_retry_on_is_not_retried(make_client):
    transport = MockTransport({f"{MOCK_BASE}/thing": scripted(ValueError("nope"), {"ok": True})})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get("/thing", retry=Retry(on=ConnectionError, attempts=3, wait=0))
        async def thing(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    with pytest.raises(GracyRequestFailed) as excinfo:
        await asyncio.wait_for(api.thing(), WAIT_FOR_TIMEOUT)

    assert isinstance(excinfo.value.original_exc, ValueError)
    assert len(transport.calls) == 1


# --------------------------------------------------------------------------- exhaustion modes


async def test_exhausted_raise_propagates(make_client):
    transport = MockTransport({f"{MOCK_BASE}/broken": (503, {"error": "down"})})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get("/broken", retry=Retry(on=gracy.status(503), attempts=2, wait=0, on_exhausted="raise"))
        async def broken(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    with pytest.raises(NonOkResponse):
        await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)

    assert len(transport.calls) == 3  # 1 initial + attempts=2 retries, all exhausted


async def test_exhausted_return_yields_decoded_body_without_raising(make_client):
    transport = MockTransport({f"{MOCK_BASE}/broken": (503, {"error": "down", "detail": "still 503"})})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get("/broken", retry=Retry(on=gracy.status(503), attempts=2, wait=0, on_exhausted="return"))
        async def broken(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    result = await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)

    assert result == {"error": "down", "detail": "still 503"}
    assert len(transport.calls) == 3


async def test_exhausted_return_yields_none_when_no_response_exists(make_client):
    transport = MockTransport({f"{MOCK_BASE}/dead": scripted(ConnectionError("always down"))})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get("/dead", retry=Retry(on=ConnectionError, attempts=2, wait=0, on_exhausted="return"))
        async def dead(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    result = await asyncio.wait_for(api.dead(), WAIT_FOR_TIMEOUT)

    assert result is None
    assert len(transport.calls) == 3


async def test_suppress_never_raises_on_failed_response(make_client):
    transport = MockTransport({f"{MOCK_BASE}/broken": (500, {"partial": True})})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get("/broken", retry=Retry(on=gracy.status(500), attempts=1, wait=0, suppress=True))
        async def broken(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    result = await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)

    assert result == {"partial": True}
    assert len(transport.calls) == 2


async def test_suppress_never_raises_on_transport_failure(make_client):
    transport = MockTransport({f"{MOCK_BASE}/dead": scripted(ConnectionError("always down"))})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get("/dead", retry=Retry(on=ConnectionError, attempts=1, wait=0, suppress=True))
        async def dead(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    result = await asyncio.wait_for(api.dead(), WAIT_FOR_TIMEOUT)

    assert result is None
    assert len(transport.calls) == 2


# --------------------------------------------------------------------------- Retry-After


async def test_respect_retry_after_waits_at_least_header_seconds(test_server, make_client):
    class Api(Gracy):
        base_url = test_server

        @get(
            "/retry-after/{key}",
            retry=Retry(on=gracy.status(429), attempts=2, wait=0.01, respect_retry_after=True),
        )
        async def limited(self, key, times, seconds) -> dict: ...

    api = await make_client(Api)
    start = time.monotonic()
    result = await asyncio.wait_for(api.limited(unique_key(), times=1, seconds=1), WAIT_FOR_TIMEOUT)
    elapsed = time.monotonic() - start

    assert result["ok"] is True
    assert result["calls"] == 2
    assert elapsed >= 0.95  # Retry-After: 1 wins over wait=0.01


async def test_retry_after_ignored_when_respect_disabled(test_server, make_client):
    class Api(Gracy):
        base_url = test_server

        @get(
            "/retry-after/{key}",
            retry=Retry(on=gracy.status(429), attempts=2, wait=0.01, respect_retry_after=False),
        )
        async def limited(self, key, times, seconds) -> dict: ...

    api = await make_client(Api)
    start = time.monotonic()
    result = await asyncio.wait_for(api.limited(unique_key(), times=1, seconds=3), WAIT_FOR_TIMEOUT)
    elapsed = time.monotonic() - start

    assert result["ok"] is True
    assert elapsed < 2.0  # would be >= 3s if the header were respected


# --------------------------------------------------------------------------- per-status overrides


async def test_per_status_override_delay_wins_over_wait(make_client):
    transport = MockTransport({f"{MOCK_BASE}/flappy": scripted((503, {"error": "x"}), {"ok": True})})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get(
            "/flappy",
            retry=Retry(on=gracy.status(503), attempts=3, wait=10.0, overrides={503: 0.3}),
        )
        async def flappy(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    start = time.monotonic()
    result = await asyncio.wait_for(api.flappy(), WAIT_FOR_TIMEOUT)
    elapsed = time.monotonic() - start

    assert result == {"ok": True}
    assert len(transport.calls) == 2
    assert elapsed >= 0.3  # the single retry slept the override delay...
    assert elapsed < 5.0  # ...NOT the 10s base wait


async def test_override_only_applies_to_its_status(make_client):
    transport = MockTransport({f"{MOCK_BASE}/flappy": scripted((429, {"error": "x"}), {"ok": True})})

    class Api(Gracy):
        base_url = MOCK_BASE

        @get(
            "/flappy",
            retry=Retry(
                on=gracy.status(429, 503),
                attempts=3,
                wait=0.3,
                overrides={503: 10.0},
                respect_retry_after=False,
            ),
        )
        async def flappy(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    start = time.monotonic()
    result = await asyncio.wait_for(api.flappy(), WAIT_FOR_TIMEOUT)
    elapsed = time.monotonic() - start

    assert result == {"ok": True}
    assert elapsed >= 0.3  # base wait applied for 429
    assert elapsed < 5.0  # the 503 override was NOT applied to a 429


# --------------------------------------------------------------------------- stale-response regression (v1 bug #2)


async def test_stale_response_never_masks_transport_error(make_client):
    """Attempt 1 -> 503 response; attempt 2 -> transport raises. The final
    outcome must be the transport failure (GracyRequestFailed wrapping the
    ConnectionError), never a re-validation of the stale 503 response."""
    transport = MockTransport(
        {
            f"{MOCK_BASE}/stale": scripted(
                (503, {"error": "server hiccup"}),
                ConnectionError("wire dropped"),
            )
        }
    )

    class Api(Gracy):
        base_url = MOCK_BASE

        @get(
            "/stale",
            retry=Retry(on=(gracy.status(503), ConnectionError), attempts=1, wait=0),
        )
        async def stale(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    with pytest.raises(GracyRequestFailed) as excinfo:
        await asyncio.wait_for(api.stale(), WAIT_FOR_TIMEOUT)

    assert isinstance(excinfo.value.original_exc, ConnectionError)
    assert len(transport.calls) == 2


# --------------------------------------------------------------------------- RetryState -> after hooks


async def test_retry_state_flows_to_after_hooks_with_attempt_numbers(make_client):
    records: list[tuple[RetryState | None, t.Any]] = []
    transport = MockTransport({f"{MOCK_BASE}/broken": (503, {"error": "down"})})

    class Api(Gracy):
        base_url = MOCK_BASE

        async def after(self, context, result, retry_state):
            records.append((retry_state, result))

        @get("/broken", retry=Retry(on=gracy.status(503), attempts=3, wait=0, on_exhausted="return"))
        async def broken(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)

    assert len(records) == 4  # initial attempt + 3 retries
    first_state, _ = records[0]
    assert first_state is None  # initial attempt carries no retry state

    retry_states = [state for state, _ in records[1:]]
    assert [s.attempt for s in retry_states] == [1, 2, 3]
    for state in retry_states:
        assert isinstance(state, RetryState)
        assert state.max_attempts == 3
        assert state.last_status == 503
        assert state.cause == "status 503"
        assert state.delay >= 0


async def test_after_hooks_see_wrapped_exception_on_every_attempt(make_client):
    """v1 bug #13 regression: after-hooks got the RAW exception on retries but
    the wrapped one on the first attempt. v2 always wraps in GracyRequestFailed."""
    records: list[tuple[RetryState | None, t.Any]] = []
    transport = MockTransport({f"{MOCK_BASE}/dead": scripted(ConnectionError("always down"))})

    class Api(Gracy):
        base_url = MOCK_BASE

        async def after(self, context, result, retry_state):
            records.append((retry_state, result))

        @get("/dead", retry=Retry(on=ConnectionError, attempts=2, wait=0, on_exhausted="return"))
        async def dead(self) -> dict: ...

    api = await make_client(Api, transport=transport)
    await asyncio.wait_for(api.dead(), WAIT_FOR_TIMEOUT)

    assert len(records) == 3
    assert [state.attempt if state else None for state, _ in records] == [None, 1, 2]
    for _, result in records:
        assert isinstance(result, GracyRequestFailed)
        assert isinstance(result.original_exc, ConnectionError)


# --------------------------------------------------------------------------- gracy.testing.retries_off()


async def test_retries_off_disables_retries_for_requests_in_block(make_client):
    transport = MockTransport({f"{MOCK_BASE}/broken": (503, {"error": "down"})})

    class Api(Gracy):
        base_url = MOCK_BASE
        config = GracyConfig(retry=Retry(on=gracy.status(503), attempts=2, wait=0))

        @get("/broken")
        async def broken(self) -> dict: ...

    api = await make_client(Api, transport=transport)

    # Sanity: retries active outside the block.
    with pytest.raises(NonOkResponse):
        await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)
    assert len(transport.calls) == 3

    transport.calls.clear()
    with gracy.testing.retries_off():
        with pytest.raises(NonOkResponse):
            await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)
    assert len(transport.calls) == 1  # retry policy stripped: single call

    # And retries come back once the block exits.
    transport.calls.clear()
    with pytest.raises(NonOkResponse):
        await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)
    assert len(transport.calls) == 3


async def test_retries_off_at_build_time_strips_retry_from_plan(make_client):
    transport = MockTransport({f"{MOCK_BASE}/broken": (503, {"error": "down"})})

    class Api(Gracy):
        base_url = MOCK_BASE
        config = GracyConfig(retry=Retry(on=gracy.status(503), attempts=2, wait=0))

        @get("/broken")
        async def broken(self) -> dict: ...

    with gracy.testing.retries_off():
        api = await make_client(Api, transport=transport)
        with pytest.raises(NonOkResponse):
            await asyncio.wait_for(api.broken(), WAIT_FOR_TIMEOUT)

    assert len(transport.calls) == 1
