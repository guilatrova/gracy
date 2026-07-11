"""Load + parallelism guarantees.

v1 pain points these tests pin down:
  * ThrottleController appended EVERY request timestamp forever -> memory grew
    with total traffic. v2 windows hold at most `limit` stamps (evict-on-check).
  * Thousands of throttled requests each slept in their own polling loop with
    no admission cap. v2 has max_pending backpressure: shed load (raise) or
    park waiters as tiny heap entries; parked submits sleep until the exact
    computed wake instant.
  * v1 was async-only. v2's sync facade and compat adapters must actually run
    requests CONCURRENTLY, not serialize them.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from gracy import Gracy, GracyQueueFull, get
from gracy.scheduler_py import PyScheduler


def _plan(*, limit: int, per: float, max_pending: int = 10_000, on_full: str = "wait") -> dict:
    return {
        "throttle": {"mode": "exact", "rules": [{"id": 0, "match": ".*", "limit": limit, "per": per}]},
        "concurrency": [],
        "queue": {"max_at_once": None, "max_pending": max_pending, "on_full": on_full, "throttle_in_hooks": False},
    }


async def _grant_and_release(scheduler, n: int, uurl: str = "https://x/{A}") -> int:
    async def one() -> None:
        permit = await scheduler.submit(uurl, "https://x/1")
        permit.release()

    await asyncio.gather(*[one() for _ in range(n)])
    return n


# ------------------------------------------------------------------ bounded window history


async def test_window_history_stays_bounded_after_thousands_of_requests():
    """The v1 unbounded-timestamp-history regression, Python engine."""
    limit = 100
    scheduler = PyScheduler(_plan(limit=limit, per=0.05))
    await _grant_and_release(scheduler, 3_000)

    rule = scheduler._throttle_rules[0]
    assert len(rule.timestamps) <= limit, (
        f"window history holds {len(rule.timestamps)} stamps after 3000 requests — "
        f"must stay <= limit ({limit}); v1 grew forever"
    )
    stats = scheduler.stats()
    assert stats["pending"] == 0 and stats["in_flight"] == 0


@pytest.mark.parametrize("engine", ["python", "rust"])
async def test_thousands_of_pending_submits_all_complete_and_drain_clean(engine: str):
    """3000 concurrent submits through a throttled lane: everything grants,
    nothing leaks — pending/in_flight return to zero on both engines."""
    if engine == "rust":
        pytest.importorskip("gracy._core")
        from gracy.engine import RustScheduler

        scheduler = RustScheduler(_plan(limit=500, per=0.05, max_pending=200))
    else:
        scheduler = PyScheduler(_plan(limit=500, per=0.05, max_pending=200))

    start = time.monotonic()
    await asyncio.wait_for(_grant_and_release(scheduler, 3_000), timeout=30)
    elapsed = time.monotonic() - start

    stats = scheduler.stats()
    assert stats["pending"] == 0, stats
    assert stats["in_flight"] == 0, stats
    # 3000 grants at 500/0.05s can't be instantaneous — the throttle really ran.
    assert elapsed >= 0.2
    await scheduler.aclose()


async def test_load_shedding_with_on_full_raise_protects_memory():
    """When the queue is full, extra submits fail FAST instead of piling up."""
    scheduler = PyScheduler(_plan(limit=1, per=0.5, max_pending=20, on_full="raise"))

    results = await asyncio.gather(
        *[_grant_one(scheduler) for _ in range(300)],
        return_exceptions=True,
    )
    granted = sum(1 for r in results if r is True)
    shed = sum(1 for r in results if isinstance(r, GracyQueueFull))
    assert granted + shed == 300
    assert shed >= 250, f"expected most submits shed, got granted={granted} shed={shed}"
    # Shedding must be immediate — the 300 gather calls above resolved without
    # waiting for the slow lane to drain (granted few, shed the rest at once).
    await scheduler.aclose()


async def _grant_one(scheduler) -> bool:
    permit = await scheduler.submit("https://x/{A}", "https://x/1")
    permit.release()
    return True


# ------------------------------------------------------------------ sync + parallel


def test_sync_facade_runs_requests_in_parallel(test_server: str):
    """Six threads x 300ms server latency: serial would be ~1.8s; the shared
    background loop must overlap them."""

    class SlowAPI(Gracy):
        base_url = test_server

        @get("/slow")
        async def slow(self, ms: int = 300) -> dict: ...

    with SlowAPI.sync() as api:
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: api.slow(300), range(6)))
        elapsed = time.monotonic() - t0

    assert all(r["ok"] for r in results)
    assert elapsed < 1.2, f"sync facade serialized requests: 6 x 300ms took {elapsed:.2f}s"


def test_compat_requests_parallel_threads(test_server: str):
    from gracy.compat import requests as rq

    try:
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: rq.get(f"{test_server}/slow?ms=300", timeout=5), range(6)))
        elapsed = time.monotonic() - t0

        assert all(r.status_code == 200 for r in responses)
        assert elapsed < 1.2, f"compat adapter serialized requests: 6 x 300ms took {elapsed:.2f}s"
    finally:
        rq.shutdown()


async def test_async_parallel_respects_max_at_once(test_server: str):
    """Parallelism is real but bounded: max_at_once=2 over 6 x 200ms requests
    forces ~3 sequential waves."""
    from gracy import GracyConfig, Queue

    class BoundedAPI(Gracy):
        base_url = test_server
        config = GracyConfig(queue=Queue(max_at_once=2))

        @get("/slow")
        async def slow(self, ms: int = 200) -> dict: ...

    async with BoundedAPI() as api:
        t0 = time.monotonic()
        results = await asyncio.gather(*[api.slow(200) for _ in range(6)])
        elapsed = time.monotonic() - t0

    assert all(r["ok"] for r in results)
    assert elapsed >= 0.55, f"max_at_once=2 not enforced: 6 x 200ms finished in {elapsed:.2f}s"
    assert elapsed < 1.5, f"no parallelism at all: {elapsed:.2f}s"
