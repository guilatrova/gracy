"""Gracy client lifecycle: build/aclose guards, options() overlays, sync facade,
fork/loop guards, and post-build observability. All against MockTransport."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import typing as t

import pytest

from gracy import Gracy, GracyReport, Retry, Throttle, get, status
from gracy.exceptions import (
    GracyClientClosedError,
    GracyConfigError,
    GracyForkedClientError,
    GracyWrongLoopError,
    NonOkResponse,
)
from gracy.testing import MockTransport

BASE = "https://api.test"


class MiniAPI(Gracy):
    base_url = BASE

    @get("/thing/{name}")
    async def get_thing(self, name: str) -> dict: ...


class FlakyAPI(Gracy):
    base_url = BASE

    @get("/flaky", retry=Retry(on=status(503), attempts=3, wait=0.0))
    async def get_flaky(self) -> dict: ...


def ok_transport() -> MockTransport:
    return MockTransport({f"GET {BASE}/thing/*": {"ok": True}})


def flaky_transport(fail_times: int) -> MockTransport:
    """First `fail_times` sends get 503, then 200 {"ok": true, "calls": n}."""
    state = {"calls": 0}

    def responder(spec: t.Any) -> tuple[int, dict[str, t.Any]]:
        state["calls"] += 1
        if state["calls"] <= fail_times:
            return (503, {"error": "unavailable"})
        return (200, {"ok": True, "calls": state["calls"]})

    return MockTransport({f"GET {BASE}/flaky": responder})


# --------------------------------------------------------------------- lifecycle


async def test_call_before_build_raises_closed_error():
    api = MiniAPI(transport=ok_transport())
    with pytest.raises(GracyClientClosedError):
        await api.get_thing("mew")


async def test_async_with_builds_and_double_aclose_is_idempotent():
    api = MiniAPI(transport=ok_transport())
    async with api:
        assert await api.get_thing("mew") == {"ok": True}
    # __aexit__ already closed once; extra acloses are no-ops
    await api.aclose()
    await api.aclose()
    with pytest.raises(GracyClientClosedError):
        await api.get_thing("mew")
    # a closed client cannot be rebuilt
    with pytest.raises(GracyClientClosedError):
        await api.build()


async def test_aclose_on_never_built_client_is_a_noop():
    api = MiniAPI(transport=ok_transport())
    await api.aclose()  # nothing started, nothing to close
    await api.aclose()


async def test_build_is_idempotent():
    transport = ok_transport()
    api = MiniAPI(transport=transport)
    try:
        assert await api.build() is api
        assert await api.build() is api  # second build: same client, no error
        assert await api.get_thing("mew") == {"ok": True}
        assert len(transport.calls) == 1
    finally:
        await api.aclose()


def test_wrong_loop_use_raises_wrong_loop_error():
    api = MiniAPI(transport=ok_transport())

    async def build_only() -> None:
        await api.build()
        assert await api.get_thing("mew") == {"ok": True}  # same loop: fine

    asyncio.run(build_only())

    async def call_from_new_loop() -> t.Any:
        return await api.get_thing("mew")

    with pytest.raises(GracyWrongLoopError):
        asyncio.run(call_from_new_loop())


async def test_fork_guard_raises_forked_client_error(monkeypatch: pytest.MonkeyPatch):
    async with MiniAPI(transport=ok_transport()) as api:
        real_pid = os.getpid()
        monkeypatch.setattr(os, "getpid", lambda: real_pid + 1)
        with pytest.raises(GracyForkedClientError):
            await api.get_thing("mew")
        monkeypatch.undo()
        assert await api.get_thing("mew") == {"ok": True}  # same pid again: fine


# --------------------------------------------------------------------- options()


async def test_options_retry_none_disables_retry_inside_block_only():
    transport = flaky_transport(fail_times=2)
    async with FlakyAPI(transport=transport) as api:
        with api.options(retry=None):
            with pytest.raises(NonOkResponse):
                await api.get_flaky()
        assert len(transport.calls) == 1  # no retries happened inside the block

        # outside the block the endpoint's Retry applies again:
        # call 2 -> 503 (retryable) -> call 3 -> 200
        result = await api.get_flaky()
        assert result == {"ok": True, "calls": 3}
        assert len(transport.calls) == 3


async def test_options_overlay_applies_to_nested_calls():
    transport = flaky_transport(fail_times=2)
    async with FlakyAPI(transport=transport) as api:

        async def helper() -> dict:
            return await api.get_flaky()  # nested coroutine still sees the overlay

        with api.options(retry=None):
            with pytest.raises(NonOkResponse):
                await helper()
        assert len(transport.calls) == 1


async def test_options_priority_accepted():
    transport = ok_transport()
    async with MiniAPI(transport=transport) as api:
        with api.options(priority=10):
            assert await api.get_thing("mew") == {"ok": True}
        async with api.options(priority=10):  # async with works too
            assert await api.get_thing("mew") == {"ok": True}
        assert len(transport.calls) == 2


async def test_options_throttle_is_a_config_error():
    async with MiniAPI(transport=ok_transport()) as api:
        with pytest.raises(GracyConfigError):
            api.options(throttle=Throttle())


async def test_options_unknown_kwarg_is_a_config_error():
    async with MiniAPI(transport=ok_transport()) as api:
        with pytest.raises(GracyConfigError):
            api.options(retries=None)  # typo of retry=


# --------------------------------------------------------------------- v1 migration guard


def test_v1_style_nested_config_class_raises_at_instantiation():
    class V1Style(Gracy):
        base_url = BASE

        class Config:  # v1 pattern, banned in v2
            BASE_URL = BASE

    with pytest.raises(GracyConfigError, match="class Config"):
        V1Style()


def test_v1_nested_config_inherited_from_parent_also_raises():
    class V1Parent(Gracy):
        base_url = BASE

        class Config:
            pass

    class Child(V1Parent):
        pass

    with pytest.raises(GracyConfigError):
        Child()


# --------------------------------------------------------------------- sync facade


def test_sync_facade_roundtrip_and_thread_cleanup():
    transport = ok_transport()
    baseline = threading.active_count()

    with MiniAPI.sync(transport=transport) as api:
        assert api.get_thing("mew") == {"ok": True}  # decoded dict, not a coroutine
        assert api.get_thing("ditto") == {"ok": True}  # works twice
        assert any(th.name.startswith("gracy-sync-") for th in threading.enumerate())

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        alive = [th for th in threading.enumerate() if th.name.startswith("gracy-sync-")]
        if not alive and threading.active_count() <= baseline:
            break
        time.sleep(0.02)
    assert not [th for th in threading.enumerate() if th.name.startswith("gracy-sync-")]
    assert threading.active_count() <= baseline
    assert len(transport.calls) == 2


# --------------------------------------------------------------------- observability


async def test_report_and_queue_stats_post_build():
    transport = ok_transport()
    async with MiniAPI(transport=transport) as api:
        await api.get_thing("mew")
        await api.get_thing("ditto")

        report = api.report()
        assert isinstance(report, GracyReport)
        assert report.total_row.total == 2
        assert len(report.rows) == 1
        assert report.rows[0].total == 2
        assert dict(report.rows[0].status_counts) == {200: 2}

        stats = api.queue_stats()
        assert stats["pending"] == 0
        assert stats["in_flight"] == 0
        assert "throttle_hits" in stats
        assert "paused" in stats


async def test_queue_stats_empty_before_build():
    api = MiniAPI(transport=ok_transport())
    assert api.queue_stats() == {}
