"""Hooks: ordering, context.state, error swallowing, recursion guard, backoff hooks."""

import asyncio
import logging
import time
import typing as t

import pytest

import gracy
from gracy import (
    Gracy,
    GracyRequestFailed,
    Hook,
    RateLimitBackoff,
    RequestContext,
    Response,
    Retry,
    RetryAfterBackoff,
    allow,
    get,
)
from gracy.scheduler_py import PyScheduler
from gracy.testing import MockTransport

BASE = "https://hooks.test"


# --------------------------------------------------------------------------- helpers


class RecorderHook(Hook):
    """Appends (name, phase, payload...) tuples into a shared list."""

    def __init__(self, name: str, events: list) -> None:
        self.name = name
        self.events = events

    async def before(self, context: RequestContext) -> None:
        self.events.append((self.name, "before", context))

    async def after(self, context, result, retry_state) -> None:
        self.events.append((self.name, "after", context, result, retry_state))


class RecordingScheduler(PyScheduler):
    """Real PyScheduler that records pause() calls."""

    def __init__(self) -> None:
        super().__init__({})
        self.pauses: list[tuple[str, float]] = []

    def pause(self, scope: str, seconds: float) -> None:
        self.pauses.append((scope, seconds))
        super().pause(scope, seconds)


def response_429(spec, retry_after: str | None = None) -> Response:
    headers = [("content-type", "application/json")]
    if retry_after is not None:
        headers.append(("retry-after", retry_after))
    return Response(status=429, headers=tuple(headers), body=b"{}", url=spec.url, elapsed=0.0)


# --------------------------------------------------------------------------- ordering + context


async def test_before_and_after_run_in_registration_order_with_request_context():
    events: list = []
    hook_a = RecorderHook("A", events)
    hook_b = RecorderHook("B", events)

    class Api(Gracy):
        base_url = BASE
        hooks = [hook_a, hook_b]

        @get("/thing/{name}")
        async def get_thing(self, name) -> dict: ...

    transport = MockTransport({f"{BASE}/thing/*": {"ok": True}})
    async with Api(transport=transport) as api:
        result = await api.get_thing("mew")

    assert result == {"ok": True}
    assert [(e[0], e[1]) for e in events] == [
        ("A", "before"),
        ("B", "before"),
        ("A", "after"),
        ("B", "after"),
    ]
    # before receives the RequestContext
    ctx = events[0][2]
    assert isinstance(ctx, RequestContext)
    assert ctx.method == "GET"
    assert ctx.url == f"{BASE}/thing/mew"
    assert ctx.uurl == f"{BASE}/thing/{{name}}"
    assert ctx.endpoint_args == {"name": "mew"}
    # after receives the same context and the Response
    _, _, after_ctx, after_result, retry_state = events[2]
    assert after_ctx is ctx
    assert isinstance(after_result, Response)
    assert after_result.status == 200
    assert retry_state is None


async def test_state_set_in_before_is_visible_in_after():
    seen: dict[str, t.Any] = {}

    class StateHook(Hook):
        async def before(self, context: RequestContext) -> None:
            context.state["stamp"] = "from-before"

        async def after(self, context, result, retry_state) -> None:
            seen["stamp"] = context.state.get("stamp")

    class Api(Gracy):
        base_url = BASE
        hooks = [StateHook()]

        @get("/echo")
        async def echo(self) -> dict: ...

    async with Api(transport=MockTransport({f"{BASE}/echo": {"ok": True}})) as api:
        await api.echo()

    assert seen["stamp"] == "from-before"


# --------------------------------------------------------------------------- error swallowing


async def test_hook_exceptions_are_swallowed_and_logged(caplog):
    class BeforeBoom(Hook):
        async def before(self, context) -> None:
            raise RuntimeError("before boom")

    class AfterBoom(Hook):
        async def after(self, context, result, retry_state) -> None:
            raise RuntimeError("after boom")

    class Api(Gracy):
        base_url = BASE
        hooks = [BeforeBoom(), AfterBoom()]

        @get("/ok")
        async def ok(self) -> dict: ...

    with caplog.at_level(logging.ERROR, logger="gracy"):
        async with Api(transport=MockTransport({f"{BASE}/ok": {"fine": True}})) as api:
            result = await api.ok()

    assert result == {"fine": True}  # request still succeeds
    gracy_errors = [r for r in caplog.records if r.name == "gracy" and r.levelno == logging.ERROR]
    messages = [r.getMessage() for r in gracy_errors]
    assert any("before-hook" in m and "BeforeBoom" in m for m in messages)
    assert any("after-hook" in m and "AfterBoom" in m for m in messages)


# --------------------------------------------------------------------------- recursion guard


async def test_request_inside_before_hook_does_not_retrigger_hooks():
    class InnerRequestHook(Hook):
        def __init__(self) -> None:
            self.client: Gracy | None = None
            self.before_calls = 0
            self.in_hook_flags: list[bool] = []

        async def before(self, context: RequestContext) -> None:
            self.before_calls += 1
            self.in_hook_flags.append(gracy.in_hook_context())
            if context.url.endswith("/outer"):
                assert self.client is not None
                await self.client.request("GET", "/inner")

    hook = InnerRequestHook()

    class Api(Gracy):
        base_url = BASE
        hooks = [hook]

    transport = MockTransport(
        {
            f"{BASE}/outer": {"outer": True},
            f"{BASE}/inner": {"inner": True},
        }
    )

    assert gracy.in_hook_context() is False
    async with Api(transport=transport) as api:
        hook.client = api
        response = await api.request("GET", "/outer")

    assert isinstance(response, Response) and response.status == 200
    # Both requests hit the wire...
    assert [spec.url for spec in transport.calls] == [f"{BASE}/inner", f"{BASE}/outer"] or [
        spec.url for spec in transport.calls
    ] == [f"{BASE}/outer", f"{BASE}/inner"]
    # ...but hooks ran ONLY for the outer request (no re-trigger, no recursion).
    assert hook.before_calls == 1
    assert hook.in_hook_flags == [True]  # in_hook_context() is True inside the hook
    assert gracy.in_hook_context() is False


# --------------------------------------------------------------------------- after() result typing


async def test_after_receives_wrapped_failure_consistently_across_retries():
    calls = {"n": 0}

    def flaky_send(spec):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ConnectionError("nope")
        return {"ok": True}

    results: list = []
    retry_states: list = []

    class Collect(Hook):
        async def after(self, context, result, retry_state) -> None:
            results.append(result)
            retry_states.append(retry_state)

    class Api(Gracy):
        base_url = BASE
        hooks = [Collect()]

        @get("/flaky", retry=Retry(on=(ConnectionError,), attempts=3, wait=0))
        async def flaky(self) -> dict: ...

    async with Api(transport=MockTransport({f"{BASE}/flaky": flaky_send})) as api:
        result = await asyncio.wait_for(api.flaky(), 15)

    assert result == {"ok": True}
    assert calls["n"] == 3
    # Every failed attempt hands after() the SAME wrapper type - never the raw exc.
    assert [type(r) for r in results] == [GracyRequestFailed, GracyRequestFailed, Response]
    for wrapped in results[:2]:
        assert isinstance(wrapped.original_exc, ConnectionError)
        assert wrapped.url == f"{BASE}/flaky"
    # retry_state: None on the initial attempt, then 1-based attempts.
    assert retry_states[0] is None
    assert [rs.attempt for rs in retry_states[1:]] == [1, 2]


# --------------------------------------------------------------------------- RetryAfterBackoff


async def test_retry_after_backoff_pauses_scheduler_using_header_seconds():
    scheduler = RecordingScheduler()

    class Api(Gracy):
        base_url = BASE
        hooks = [RetryAfterBackoff()]

        @get("/limited", status_policy=allow(429))
        async def limited(self) -> dict: ...

    transport = MockTransport({f"{BASE}/limited": lambda spec: response_429(spec, retry_after="7")})
    async with Api(transport=transport, scheduler=scheduler) as api:
        await api.limited()

    assert scheduler.pauses == [("client", 7.0)]


async def test_retry_after_backoff_lock_per_endpoint_scopes_pause_to_uurl():
    scheduler = RecordingScheduler()

    class Api(Gracy):
        base_url = BASE
        hooks = [RetryAfterBackoff(lock_per_endpoint=True)]

        @get("/limited", status_policy=allow(429))
        async def limited(self) -> dict: ...

    transport = MockTransport({f"{BASE}/limited": lambda spec: response_429(spec, retry_after="3")})
    async with Api(transport=transport, scheduler=scheduler) as api:
        await api.limited()

    assert scheduler.pauses == [(f"{BASE}/limited", 3.0)]


async def test_retry_after_backoff_delays_next_call_on_real_scheduler():
    calls = {"n": 0}

    def first_429(spec):
        calls["n"] += 1
        if calls["n"] == 1:
            return response_429(spec, retry_after="0.4")
        return {"ok": True}

    class Api(Gracy):
        base_url = BASE
        hooks = [RetryAfterBackoff()]

        @get("/limited", status_policy=allow(429))
        async def limited(self) -> dict: ...

    async with Api(transport=MockTransport({f"{BASE}/limited": first_429})) as api:
        await asyncio.wait_for(api.limited(), 15)  # 429 -> pause("client", 0.4)

        # The pause gate is live on the scheduler right after the first call.
        paused = api.queue_stats()["paused"]
        assert "client" in paused and paused["client"] > 0

        start = time.monotonic()
        result = await asyncio.wait_for(api.limited(), 15)
        elapsed = time.monotonic() - start

    assert result == {"ok": True}
    assert elapsed >= 0.3  # admission held back by the pause gate (~0.4s, generous margin)


# --------------------------------------------------------------------------- RateLimitBackoff


async def test_rate_limit_backoff_uses_default_delay_without_header():
    scheduler = RecordingScheduler()

    class Api(Gracy):
        base_url = BASE
        hooks = [RateLimitBackoff(delay=0.6)]

        @get("/limited", status_policy=allow(429))
        async def limited(self) -> dict: ...

    # 429 with NO Retry-After header.
    transport = MockTransport({f"{BASE}/limited": lambda spec: response_429(spec)})
    async with Api(transport=transport, scheduler=scheduler) as api:
        await api.limited()

    assert scheduler.pauses == [("client", 0.6)]


async def test_rate_limit_backoff_header_wins_over_default_delay():
    scheduler = RecordingScheduler()

    class Api(Gracy):
        base_url = BASE
        hooks = [RateLimitBackoff(delay=0.6)]

        @get("/limited", status_policy=allow(429))
        async def limited(self) -> dict: ...

    transport = MockTransport({f"{BASE}/limited": lambda spec: response_429(spec, retry_after="2")})
    async with Api(transport=transport, scheduler=scheduler) as api:
        await api.limited()

    assert scheduler.pauses == [("client", 2.0)]
