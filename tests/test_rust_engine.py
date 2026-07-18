"""Rust engine parity: RustScheduler/RustTransport directly and full clients
on GRACY_ENGINE=rust.

Mirrors the key PyScheduler direct tests from test_queue_throttle.py (same
hand-built plan dicts, same invariants) plus transport behavior against the
conftest test_server, cancellation rollback, and end-to-end client flows
(retry, throttle, replay, report) on the Rust engine.
"""

from __future__ import annotations

import asyncio
import time
import typing as t
import uuid

import pytest

pytest.importorskip("gracy._core")
from gracy import _core  # noqa: E402

pytestmark = pytest.mark.skipif(
    not hasattr(_core, "CoreScheduler"),
    reason="gracy._core lacks CoreScheduler (extension built without the engine)",
)

from gracy import (  # noqa: E402
    Gracy,
    GracyConfig,
    GracyQueueFull,
    GracyRequestFailed,
    Rate,
    RequestSpec,
    Retry,
    Throttle,
    get,
    status,
)
from gracy.engine import RustScheduler, RustTransport  # noqa: E402
from gracy.replay import Replay, SqliteStorage  # noqa: E402

UURL = "https://api.test/thing/{id}"


def make_plan(
    *,
    throttle_rules: t.Sequence[dict[str, t.Any]] = (),
    mode: str = "exact",
    concurrency: t.Sequence[dict[str, t.Any]] = (),
    max_at_once: int | None = None,
    max_pending: int = 10_000,
    on_full: str = "wait",
    throttle_in_hooks: bool = False,
) -> dict[str, t.Any]:
    """The exact scheduler_plan JSON shape from the gracy/plan.py docstring."""
    return {
        "throttle": {"mode": mode, "rules": list(throttle_rules)},
        "concurrency": list(concurrency),
        "queue": {
            "max_at_once": max_at_once,
            "max_pending": max_pending,
            "on_full": on_full,
            "throttle_in_hooks": throttle_in_hooks,
        },
    }


async def grant_times(sched: RustScheduler, n: int, uurl: str = UURL) -> list[float]:
    """Fire n concurrent submits; return sorted monotonic grant instants."""
    times: list[float] = []

    async def one(i: int) -> None:
        permit = await sched.submit(uurl, uurl.replace("{id}", str(i)))
        times.append(time.monotonic())
        permit.release()

    await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(n))), 15)
    return sorted(times)


def assert_no_window_over_limit(times: list[float], limit: int, per: float, eps: float = 0.02) -> None:
    """No trailing window of `per` seconds may contain more than `limit` grants."""
    for i in range(len(times) - limit):
        span = times[i + limit] - times[i]
        assert span >= per - eps, (
            f"{limit + 1} grants within {span:.3f}s violates {limit}/{per}s at index {i}: {times}"
        )


# =========================================================================== sliding window


async def test_sliding_window_invariant_direct():
    sched = RustScheduler(
        make_plan(throttle_rules=[{"id": 0, "match": r".*", "limit": 3, "per": 0.3}])
    )
    await sched.start()
    times = await grant_times(sched, 10)

    assert len(times) == 10
    assert_no_window_over_limit(times, limit=3, per=0.3)
    assert times[-1] - times[0] >= 0.5  # 10 grants at 3/0.3s cannot fit in half a second
    await sched.aclose()


# =========================================================================== priorities


async def test_higher_priority_waiter_is_granted_first():
    sched = RustScheduler(
        make_plan(
            concurrency=[{"id": 0, "match": None, "limit": 1, "per_uurl": False}],
            max_pending=1,
            on_full="wait",
        )
    )
    uurl = "https://api.test/p/{id}"
    order: list[str] = []

    holder = await asyncio.wait_for(sched.submit(uurl, "https://api.test/p/holder"), 15)

    async def tracked(name: str, priority: int) -> None:
        permit = await sched.submit(uurl, f"https://api.test/p/{name}", priority=priority)
        order.append(name)
        permit.release()

    # blocker fills the single pending slot (stuck at the concurrency semaphore)
    blocker = asyncio.create_task(tracked("blocker", 0))
    await asyncio.sleep(0.05)
    # prio-0 items park at the backpressure door...
    lows = [asyncio.create_task(tracked(f"low{i}", 0)) for i in range(3)]
    await asyncio.sleep(0.05)
    # ...then a prio-10 arrives LAST
    high = asyncio.create_task(tracked("high", 10))
    await asyncio.sleep(0.05)

    assert order == []  # nothing granted while the holder occupies the lane
    holder.release()
    await asyncio.wait_for(asyncio.gather(blocker, *lows, high), 15)

    assert order[0] == "blocker"
    high_at = order.index("high")
    for i in range(3):
        assert high_at < order.index(f"low{i}"), order
    await sched.aclose()


# =========================================================================== backpressure


async def test_max_pending_on_full_raise():
    sched = RustScheduler(
        make_plan(
            concurrency=[{"id": 0, "match": None, "limit": 1, "per_uurl": False}],
            max_pending=2,
            on_full="raise",
        )
    )
    uurl = "https://api.test/bp/{id}"

    holder = await asyncio.wait_for(sched.submit(uurl, "https://api.test/bp/0"), 15)
    t1 = asyncio.create_task(sched.submit(uurl, "https://api.test/bp/1"))
    t2 = asyncio.create_task(sched.submit(uurl, "https://api.test/bp/2"))
    await asyncio.sleep(0.05)  # both now occupy the 2 pending slots at the semaphore

    with pytest.raises(GracyQueueFull):
        await sched.submit(uurl, "https://api.test/bp/3")

    holder.release()
    p1 = await asyncio.wait_for(t1, 15)
    p1.release()
    p2 = await asyncio.wait_for(t2, 15)
    p2.release()
    assert sched.stats()["pending"] == 0
    assert sched.stats()["in_flight"] == 0


# =========================================================================== pause gates


async def test_pause_uurl_scope_delays_only_that_uurl():
    sched = RustScheduler(make_plan())
    uurl_a = "https://api.test/a/{id}"
    uurl_b = "https://api.test/b/{id}"
    elapsed: dict[str, float] = {}

    sched.pause(uurl_a, 0.4)
    t0 = time.monotonic()

    async def go(uurl: str, name: str) -> None:
        permit = await sched.submit(uurl, uurl.replace("{id}", "1"))
        elapsed[name] = time.monotonic() - t0
        permit.release()

    await asyncio.wait_for(asyncio.gather(go(uurl_a, "a"), go(uurl_b, "b")), 15)

    assert elapsed["a"] >= 0.35
    assert elapsed["b"] < 0.15  # other uurl unaffected


# =========================================================================== from_hook bypass


async def test_from_hook_bypasses_concurrency_and_throttle():
    sched = RustScheduler(
        make_plan(
            throttle_rules=[{"id": 0, "match": r".*", "limit": 1, "per": 0.5}],
            concurrency=[{"id": 0, "match": None, "limit": 1, "per_uurl": False}],
        )
    )
    url = "https://api.test/thing/1"

    holder = await asyncio.wait_for(sched.submit(UURL, url), 15)  # lane + window token held

    # a hook-mode submit must grant instantly: no semaphore, no throttle
    t0 = time.monotonic()
    hook_permit = await asyncio.wait_for(sched.submit(UURL, url, from_hook=True), 15)
    assert time.monotonic() - t0 < 0.2
    hook_permit.release()
    holder.release()
    await sched.aclose()


# =========================================================================== permit release


async def test_permit_double_release_does_not_leak_capacity():
    sched = RustScheduler(
        make_plan(concurrency=[{"id": 0, "match": None, "limit": 1, "per_uurl": False}])
    )
    url = "https://api.test/thing/1"

    first = await asyncio.wait_for(sched.submit(UURL, url), 15)
    first.release()
    first.release()  # idempotent - must NOT free a second slot
    assert sched.stats()["in_flight"] == 0

    second = await asyncio.wait_for(sched.submit(UURL, url), 15)
    blocked = asyncio.create_task(sched.submit(UURL, url))
    await asyncio.sleep(0.1)
    assert not blocked.done()  # limit=1 still enforced after the double release

    second.release()
    third = await asyncio.wait_for(blocked, 15)
    third.release()
    assert sched.stats()["in_flight"] == 0
    await sched.aclose()


# =========================================================================== closed scheduler


async def test_submit_after_aclose_raises_closed():
    from gracy import GracyClientClosedError

    sched = RustScheduler(make_plan())
    await sched.aclose()
    await sched.aclose()  # idempotent
    with pytest.raises(GracyClientClosedError):
        await sched.submit(UURL, "https://api.test/thing/1")


# =========================================================================== stats


async def test_stats_shape_and_live_values():
    sched = RustScheduler(
        make_plan(
            throttle_rules=[{"id": 0, "match": r".*", "limit": 1, "per": 0.3}],
            concurrency=[{"id": 0, "match": None, "limit": 2, "per_uurl": False}],
        )
    )
    url = "https://api.test/thing/1"

    stats = sched.stats()
    assert {"pending", "in_flight", "paused", "throttle_hits", "throttled_by_uurl"} <= set(stats)
    assert stats["pending"] == 0
    assert stats["in_flight"] == 0
    assert stats["throttle_hits"] == {}
    assert stats["paused"] == {}

    p1 = await asyncio.wait_for(sched.submit(UURL, url), 15)
    assert sched.stats()["in_flight"] == 1

    task2 = asyncio.create_task(sched.submit(UURL, url))  # stuck in the throttle wait
    await asyncio.sleep(0.05)
    stats = sched.stats()
    assert stats["pending"] == 1
    assert stats["throttle_hits"].get(0, 0) >= 1  # int keys, exactly like PyScheduler
    assert stats["throttled_by_uurl"].get(UURL, 0) >= 1

    sched.pause("client", 5.0)
    stats = sched.stats()
    assert "client" in stats["paused"]
    assert 0 < stats["paused"]["client"] <= 5.0

    p2 = await asyncio.wait_for(task2, 15)
    p2.release()
    p1.release()
    stats = sched.stats()
    assert stats["pending"] == 0
    assert stats["in_flight"] == 0


# =========================================================================== cancellation


async def test_cancelled_submit_rolls_back_cleanly():
    """A submit cancelled while blocked on a concurrency slot must not leak
    pending capacity or the slot: after releasing the blocker, a fresh submit
    is granted normally."""
    sched = RustScheduler(
        make_plan(concurrency=[{"id": 0, "match": None, "limit": 1, "per_uurl": False}])
    )
    url = "https://api.test/thing/1"

    blocker = await asyncio.wait_for(sched.submit(UURL, url), 15)

    stuck = asyncio.create_task(sched.submit(UURL, url))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(stuck, 0.1)  # cancels the parked submit

    blocker.release()
    fresh = await asyncio.wait_for(sched.submit(UURL, url), 15)  # no leaked capacity
    fresh.release()

    await asyncio.sleep(0.05)  # let the aborted core task finish its rollback
    stats = sched.stats()
    assert stats["pending"] == 0
    assert stats["in_flight"] == 0
    await sched.aclose()


# =========================================================================== transport (direct)


def _spec(method: str, url: str, **kw: t.Any) -> RequestSpec:
    return RequestSpec(method=method, url=url, uurl=url, **kw)


async def test_transport_get_echo(test_server):
    transport = RustTransport()
    await transport.start()
    try:
        resp = await transport.send(
            _spec("GET", f"{test_server}/echo/rusty?q=1", headers=(("x-gracy-test", "yes"),))
        )
        assert resp.status == 200
        assert resp.http_version == "HTTP/1.1"
        assert resp.elapsed > 0
        assert resp.header("content-type") == "application/json"
        payload = resp.json()
        assert payload["path"] == "/echo/rusty"
        assert payload["query"] == {"q": "1"}
        assert payload["headers"]["x-gracy-test"] == "yes"
    finally:
        await transport.aclose()


async def test_transport_post_with_body(test_server):
    transport = RustTransport()
    await transport.start()
    try:
        resp = await transport.send(
            _spec(
                "POST",
                f"{test_server}/status/201",
                headers=(("content-type", "application/json"),),
                content=b'{"hello": "rust"}',
            )
        )
        assert resp.status == 201
        assert resp.json() == {"status": 201, "body": '{"hello": "rust"}'}
    finally:
        await transport.aclose()


async def test_transport_timeout_raises_raw(test_server):
    transport = RustTransport()
    await transport.start()
    try:
        with pytest.raises(TimeoutError):
            await transport.send(_spec("GET", f"{test_server}/slow?ms=500", timeout=0.1))
    finally:
        await transport.aclose()


async def test_transport_connect_refused_raises_raw():
    transport = RustTransport()
    await transport.start()
    try:
        with pytest.raises(ConnectionError):
            # 127.0.0.1:9 (discard) - nothing listens there in CI or locally
            await transport.send(_spec("GET", "http://127.0.0.1:9/nope", timeout=5.0))
    finally:
        await transport.aclose()


async def test_transport_timeout_wrapped_by_pipeline(test_server, make_client):
    """Pipeline-level: the raw TimeoutError is wrapped into GracyRequestFailed."""

    class Api(Gracy):
        base_url = test_server
        config = GracyConfig(timeout=0.1, retry=None)

        @get("/slow")
        async def slow(self, ms: int = 500) -> dict: ...

    api = await make_client(Api, transport=RustTransport())
    with pytest.raises(GracyRequestFailed) as excinfo:
        await api.slow(ms=500)
    assert isinstance(excinfo.value.original_exc, TimeoutError)


# =========================================================================== end-to-end (rust engine)


@pytest.fixture
def rust_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRACY_ENGINE", "rust")


async def test_client_defaults_to_rust_engine(rust_engine, test_server, make_client):
    class Api(Gracy):
        base_url = test_server

        @get("/echo/{name}")
        async def echo(self, name) -> dict: ...

    api = await make_client(Api)
    assert isinstance(api._scheduler, RustScheduler)
    assert isinstance(api._transport, RustTransport)
    result = await api.echo("end2end")
    assert result["path"] == "/echo/end2end"


async def test_client_flaky_retry_on_rust_engine(rust_engine, test_server, make_client):
    class Api(Gracy):
        base_url = test_server
        config = GracyConfig(retry=Retry(on=status(503), attempts=3, wait=0.01))

        @get("/flaky/{key}")
        async def flaky(self, key, fail_times: int = 0) -> dict: ...

    api = await make_client(Api)
    result = await api.flaky(uuid.uuid4().hex, fail_times=2)
    assert result["ok"] is True
    assert result["calls"] == 3  # 2 x 503 then success


async def test_client_throttle_on_rust_engine(rust_engine, test_server, make_client):
    class Api(Gracy):
        base_url = test_server
        config = GracyConfig(throttle=Throttle(rules=[Rate(limit=1, per=0.4)]))

        @get("/echo/{name}")
        async def echo(self, name) -> dict: ...

    api = await make_client(Api)
    t0 = time.monotonic()
    await asyncio.wait_for(asyncio.gather(api.echo("a"), api.echo("b")), 15)
    assert time.monotonic() - t0 >= 0.35  # second grant waited for the window

    stats = api.queue_stats()
    assert stats["throttle_hits"].get(0, 0) >= 1


async def test_client_replay_record_then_replay_on_rust_engine(rust_engine, test_server, tmp_path):
    db = tmp_path / "rust-cassette.db"

    class Api(Gracy):
        base_url = test_server

        @get("/echo/{name}")
        async def echo(self, name): ...  # no annotation -> raw gracy Response

    async with Api(replay=Replay(mode="record", storage=SqliteStorage(db))) as api:
        live = await api.echo("cassette")
        assert live.status == 200
        assert live.is_replay is False

    # replay: the recorded response is served without touching the wire
    async with Api(replay=Replay(mode="replay", storage=SqliteStorage(db))) as api:
        replayed = await api.echo("cassette")
        assert replayed.is_replay is True
        assert replayed.status == 200
        assert replayed.json() == live.json()


async def test_client_report_populated_on_rust_engine(rust_engine, test_server, make_client):
    class Api(Gracy):
        base_url = test_server

        @get("/echo/{name}")
        async def echo(self, name) -> dict: ...

        @get("/status/{code}", status_policy=None)
        async def code(self, code) -> dict: ...

    api = await make_client(Api)
    await api.echo("one")
    await api.echo("two")
    await api.code(404)

    report = api.report()
    rows = {row.uurl: row for row in report.rows}
    echo_row = rows[f"{test_server}/echo/{{name}}"]
    assert echo_row.total == 2
    assert echo_row.success_rate == 100.0
    code_row = rows[f"{test_server}/status/{{code}}"]
    assert code_row.total == 1
