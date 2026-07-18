"""Queue + throttle behavior: PyScheduler directly (hand-built plan dicts per the
gracy/plan.py docstring contract) and through Gracy clients.

Covers: exact sliding-window invariant, v1 negative-wait regression, multi-rule
enforcement, rule regex scoping, priority ordering at the backpressure door,
max_pending raise/wait, pause gates (direct + Queue.pause_on_status), the
from_hook concurrency bypass (deadlock regression), no_throttle / throttle_off,
and stats() shape.
"""

from __future__ import annotations

import asyncio
import time
import typing as t
import uuid

import pytest

import gracy
from gracy import (
    Concurrency,
    Gracy,
    GracyConfig,
    GracyQueueFull,
    Queue,
    Rate,
    Retry,
    Throttle,
    get,
    status,
)
from gracy.scheduler_py import PyScheduler, _ThrottleRule
from gracy.transports import MockTransport

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


async def grant_times(sched: PyScheduler, n: int, uurl: str = UURL) -> list[float]:
    """Fire n concurrent submits; return sorted monotonic grant instants."""
    times: list[float] = []

    async def one(i: int) -> None:
        permit = await sched.submit(uurl, uurl.replace("{id}", str(i)))
        times.append(time.monotonic())
        permit.release()

    await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(n))), 15)
    return sorted(times)


def assert_no_window_over_limit(times: list[float], limit: int, per: float, eps: float = 0.02) -> None:
    """No trailing window of `per` seconds may contain more than `limit` grants.

    Equivalent form: any (limit+1) consecutive grants must span more than `per`.
    """
    for i in range(len(times) - limit):
        span = times[i + limit] - times[i]
        assert span >= per - eps, (
            f"{limit + 1} grants within {span:.3f}s violates {limit}/{per}s at index {i}: {times}"
        )


# =========================================================================== sliding window


async def test_sliding_window_invariant_direct():
    sched = PyScheduler(
        make_plan(throttle_rules=[{"id": 0, "match": r".*", "limit": 3, "per": 0.3}])
    )
    await sched.start()
    times = await grant_times(sched, 10)

    assert len(times) == 10
    assert_no_window_over_limit(times, limit=3, per=0.3)
    assert times[-1] - times[0] >= 0.5  # 10 grants at 3/0.3s cannot fit in half a second
    await sched.aclose()


async def test_sliding_window_invariant_through_client(make_client):
    times: list[float] = []

    class Api(Gracy):
        base_url = "https://api.test"
        config = GracyConfig(throttle=Throttle(rules=[Rate(limit=3, per=0.3)]))

        @get("/thing/{name}")
        async def thing(self, name) -> dict: ...

    api = await make_client(Api, transport=MockTransport({"*": {"ok": True}}))

    async def one(i: int) -> None:
        result = await api.thing(f"n{i}")
        times.append(time.monotonic())
        assert result == {"ok": True}

    await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(10))), 15)
    times.sort()

    assert_no_window_over_limit(times, limit=3, per=0.3, eps=0.05)
    assert times[-1] - times[0] >= 0.5

    stats = api.queue_stats()
    assert {"pending", "in_flight", "paused", "throttle_hits"} <= set(stats)
    assert stats["throttle_hits"].get(0, 0) >= 1  # the rule actually throttled


# =========================================================================== negative-wait regression


def test_next_allowed_is_never_in_the_past():
    """v1 computed a negative wait when bursting over the limit; the exact
    sliding window must clamp: next_allowed(now) >= now, always."""
    rule = _ThrottleRule({"id": 0, "match": r".*", "limit": 2, "per": 0.3})
    now = time.monotonic()

    # ancient stamps (way past the window) must be evicted, not go negative
    rule.timestamps.extend([now - 10.0, now - 9.0])
    assert rule.next_allowed(now) == now

    rule.timestamps.clear()
    rule.timestamps.extend([now - 0.1, now - 0.05])  # window full
    allowed = rule.next_allowed(now)
    assert allowed >= now
    assert allowed == pytest.approx((now - 0.1) + 0.3)


async def test_burst_over_limit_still_throttles():
    sched = PyScheduler(
        make_plan(throttle_rules=[{"id": 0, "match": r".*", "limit": 1, "per": 0.25}])
    )
    times = await grant_times(sched, 4)  # burst of 4 against 1/0.25s

    for earlier, later in zip(times, times[1:]):
        assert later - earlier >= 0.25 - 0.02  # every consecutive gap honors the window
    assert times[-1] - times[0] >= 0.75 - 0.05


# =========================================================================== multiple rules


async def test_two_rules_burst_and_sustained_both_enforced():
    sched = PyScheduler(
        make_plan(
            throttle_rules=[
                {"id": 0, "match": r".*", "limit": 3, "per": 0.2},  # burst
                {"id": 1, "match": r".*", "limit": 5, "per": 0.8},  # sustained
            ]
        )
    )
    times = await grant_times(sched, 10)

    assert_no_window_over_limit(times, limit=3, per=0.2)
    assert_no_window_over_limit(times, limit=5, per=0.8)
    # the sustained rule dominates: 10 grants at 5/0.8s need >= ~0.8s beyond the first five
    assert times[-1] - times[0] >= 0.8 - 0.05


# =========================================================================== regex scoping


async def test_rule_regex_scoping_direct_non_matching_url_never_throttled():
    sched = PyScheduler(
        make_plan(
            throttle_rules=[
                {"id": 0, "match": r"https://api\.test/limited/.*", "limit": 1, "per": 0.5}
            ]
        )
    )

    # 5 concurrent submits to a NON-matching url: all instant, no hits recorded
    t0 = time.monotonic()
    times = await grant_times(sched, 5, uurl="https://api.test/open/{id}")
    assert time.monotonic() - t0 < 0.2
    assert len(times) == 5
    assert sched.stats()["throttle_hits"] == {}

    # matching urls DO throttle
    limited = await grant_times(sched, 2, uurl="https://api.test/limited/{id}")
    assert limited[1] - limited[0] >= 0.5 - 0.02
    assert sched.stats()["throttle_hits"].get(0, 0) >= 1


async def test_rule_regex_scoping_through_client(make_client):
    """An endpoint-scoped Throttle binds its `.*` rule to that endpoint's URL
    shape at compile time; other endpoints are never throttled by it."""

    class Api(Gracy):
        base_url = "https://api.test"

        @get("/limited/{name}", throttle=Throttle(rules=[Rate(limit=1, per=0.4)]))
        async def limited(self, name) -> dict: ...

        @get("/open/{name}")
        async def open_(self, name) -> dict: ...

    api = await make_client(Api, transport=MockTransport({"*": {"ok": True}}))

    t0 = time.monotonic()
    await asyncio.wait_for(asyncio.gather(*(api.open_(f"o{i}") for i in range(5))), 15)
    assert time.monotonic() - t0 < 0.3  # open endpoint untouched by the limited rule

    t1 = time.monotonic()
    await asyncio.wait_for(asyncio.gather(api.limited("a"), api.limited("b")), 15)
    assert time.monotonic() - t1 >= 0.4 - 0.05  # limited endpoint throttled


# =========================================================================== priorities


async def test_higher_priority_waiter_is_granted_first():
    sched = PyScheduler(
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
    sched = PyScheduler(
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


async def test_max_pending_on_full_wait_eventually_grants_all():
    sched = PyScheduler(
        make_plan(
            concurrency=[{"id": 0, "match": None, "limit": 1, "per_uurl": False}],
            max_pending=2,
            on_full="wait",
        )
    )
    uurl = "https://api.test/bpw/{id}"

    holder = await asyncio.wait_for(sched.submit(uurl, "https://api.test/bpw/0"), 15)
    tasks = [
        asyncio.create_task(sched.submit(uurl, f"https://api.test/bpw/{i}")) for i in (1, 2, 3)
    ]
    await asyncio.sleep(0.1)
    assert not any(task.done() for task in tasks)  # third parked at the door, none granted

    holder.release()
    for task in tasks:
        permit = await asyncio.wait_for(task, 15)
        permit.release()

    stats = sched.stats()
    assert stats["pending"] == 0
    assert stats["in_flight"] == 0


# =========================================================================== pause gates


async def test_pause_uurl_scope_delays_only_that_uurl():
    sched = PyScheduler(make_plan())
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


async def test_pause_client_scope_delays_every_uurl():
    sched = PyScheduler(make_plan())
    elapsed: dict[str, float] = {}

    sched.pause("client", 0.4)
    t0 = time.monotonic()

    async def go(uurl: str, name: str) -> None:
        permit = await sched.submit(uurl, uurl)
        elapsed[name] = time.monotonic() - t0
        permit.release()

    await asyncio.wait_for(
        asyncio.gather(go("https://api.test/a/1", "a"), go("https://api.test/b/1", "b")), 15
    )

    assert elapsed["a"] >= 0.35
    assert elapsed["b"] >= 0.35


async def test_pause_on_status_through_client(test_server, make_client):
    """Queue(pause_on_status={429: 'endpoint'}): the 429's Retry-After pauses the
    endpoint's uurl, so BOTH the retry attempt and a concurrent same-endpoint
    request are gated for >= the header seconds."""

    class Api(Gracy):
        base_url = test_server
        config = GracyConfig(
            queue=Queue(pause_on_status={429: "endpoint"}),
            # respect_retry_after=False isolates the pause GATE: the retry itself
            # sleeps only 10ms, so any >=0.5s delay must come from the scheduler.
            retry=Retry(on=status(429), attempts=2, wait=0.01, respect_retry_after=False),
        )

        @get("/retry-after/{key}")
        async def hit(self, key, times: int = 0, seconds: str = "0") -> dict: ...

    api = await make_client(Api)
    key_429 = uuid.uuid4().hex
    key_ok = uuid.uuid4().hex
    done: dict[str, float] = {}
    t0 = time.monotonic()

    async def first() -> None:
        result = await api.hit(key_429, times=1, seconds="0.6")
        done["first"] = time.monotonic() - t0
        assert result["ok"] is True

    async def second() -> None:
        await asyncio.sleep(0.15)  # let the 429 land and set the pause gate
        result = await api.hit(key_ok)  # times=0 -> server answers 200 immediately
        done["second"] = time.monotonic() - t0
        assert result["ok"] is True

    await asyncio.wait_for(asyncio.gather(first(), second()), 15)

    assert done["first"] >= 0.5  # retry re-entered admission and hit the gate
    assert done["second"] >= 0.5  # same-uurl request gated too


# =========================================================================== from_hook bypass


async def test_from_hook_bypasses_concurrency_no_deadlock(test_server, make_client):
    """Deadlock regression: with Concurrency(limit=1) and the single lane held by
    a slow request, a before-hook issuing a request through the SAME client must
    complete (from_hook bypasses the semaphore) instead of queueing behind it."""
    events: dict[str, float] = {}

    class Api(Gracy):
        base_url = test_server
        config = GracyConfig(concurrency=Concurrency(limit=1))

        async def before(self, context) -> None:
            if "/echo/outer" in context.url:
                await self.request("GET", "/echo/hook")
                events["hook_done"] = time.monotonic()

    api = await make_client(Api)

    async def slow() -> None:
        await api.request("GET", "/slow", params={"ms": 400})
        events["slow_done"] = time.monotonic()

    async def outer() -> None:
        await api.request("GET", "/echo/outer")
        events["outer_done"] = time.monotonic()

    t0 = time.monotonic()
    slow_task = asyncio.create_task(slow())
    await asyncio.sleep(0.1)  # slow now holds the single concurrency slot on the wire
    await asyncio.wait_for(asyncio.gather(slow_task, outer()), 15)

    assert "hook_done" in events  # the hook's request completed at all
    assert events["hook_done"] < events["slow_done"]  # ...WHILE the lane was held
    assert events["outer_done"] - t0 >= 0.35  # the outer request itself did wait for the lane


# =========================================================================== throttle bypasses


async def test_no_throttle_skips_token_spend():
    sched = PyScheduler(
        make_plan(throttle_rules=[{"id": 0, "match": r".*", "limit": 1, "per": 0.5}])
    )
    url = "https://api.test/thing/1"

    t0 = time.monotonic()
    for _ in range(5):
        permit = await asyncio.wait_for(sched.submit(UURL, url, no_throttle=True), 15)
        permit.release()
    assert time.monotonic() - t0 < 0.2  # 5 grants against 1/0.5s: throttle fully bypassed

    # no tokens were spent: the first REGULAR submit is instant...
    t1 = time.monotonic()
    permit = await asyncio.wait_for(sched.submit(UURL, url), 15)
    permit.release()
    assert time.monotonic() - t1 < 0.2

    # ...and only now is the window occupied
    t2 = time.monotonic()
    permit = await asyncio.wait_for(sched.submit(UURL, url), 15)
    permit.release()
    assert time.monotonic() - t2 >= 0.5 - 0.05


async def test_throttle_off_makes_tight_rate_finish_fast(make_client):
    class Api(Gracy):
        base_url = "https://tight.test"
        config = GracyConfig(throttle=Throttle(rules=[Rate(limit=1, per=0.5)]))

        @get("/r/{name}")
        async def r(self, name) -> dict: ...

    # built OUTSIDE the with-block: rules are compiled into the running scheduler
    api = await make_client(Api, transport=MockTransport({"*": {"ok": True}}))

    t0 = time.monotonic()
    await asyncio.wait_for(asyncio.gather(api.r("a"), api.r("b")), 15)
    assert time.monotonic() - t0 >= 0.45  # baseline: the rate really is tight

    with gracy.testing.throttle_off():  # runtime switch -> submits with no_throttle=True
        t1 = time.monotonic()
        results = await asyncio.wait_for(
            asyncio.gather(*(api.r(f"x{i}") for i in range(4))), 15
        )
        assert time.monotonic() - t1 < 0.4  # would be >= 1.5s if still throttled
    assert all(result == {"ok": True} for result in results)


# =========================================================================== stats


async def test_stats_shape_and_live_values():
    sched = PyScheduler(
        make_plan(
            throttle_rules=[{"id": 0, "match": r".*", "limit": 1, "per": 0.3}],
            concurrency=[{"id": 0, "match": None, "limit": 2, "per_uurl": False}],
        )
    )
    url = "https://api.test/thing/1"

    stats = sched.stats()
    assert {"pending", "in_flight", "paused", "throttle_hits"} <= set(stats)
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
    assert stats["throttle_hits"].get(0, 0) >= 1
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
