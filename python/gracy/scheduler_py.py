"""Pure-Python reference Scheduler — the executable spec the Rust engine must match.

Implements the ``Scheduler`` protocol from gracy._protocols on top of plain
asyncio primitives. Kept deliberately readable: differential tests run the
same schedules against this and ``CoreScheduler`` (Rust) and diff the grants.

Per-submit order (V2_PLAN.md §6):

    backpressure admission (max_pending)
    -> acquire ALL matching concurrency semaphores, in rule-id order
    -> pause gate (checked BEFORE throttle so paused requests spend no tokens)
    -> throttle wait + atomic reserve
    -> Permit granted (in_flight += 1)

``from_hook=True`` bypasses concurrency semaphores AND pause gates (hook
requests must never deadlock behind the very lane they are trying to heal),
and bypasses throttle unless the plan sets ``queue.throttle_in_hooks``.
``no_throttle=True`` bypasses throttle only (replay-hit path).

Everything is single-threaded asyncio: a check followed by a reserve with no
``await`` in between is atomic by construction.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import re
import time
import typing as t
from collections import deque

from gracy.exceptions import GracyClientClosedError, GracyQueueFull

_GLOBAL_SCOPE: t.Final = "global"


class _ThrottleRule:
    """One compiled sliding-window rule: max `limit` grants in any trailing `per` seconds."""

    __slots__ = ("id", "regex", "limit", "per", "timestamps")

    def __init__(self, rule: dict[str, t.Any]) -> None:
        self.id: int = rule["id"]
        self.regex: re.Pattern[str] = re.compile(rule["match"])  # compiled once
        self.limit: int = int(rule["limit"])
        self.per: float = float(rule["per"])
        self.timestamps: deque[float] = deque()  # monotonic grant instants, oldest first

    def next_allowed(self, now: float) -> float:
        """Exact sliding window: evict expired stamps, then either now (room left)
        or the instant the oldest in-window stamp expires. Never in the past."""
        cutoff = now - self.per
        while self.timestamps and self.timestamps[0] <= cutoff:
            self.timestamps.popleft()
        if len(self.timestamps) < self.limit:
            return now
        return self.timestamps[0] + self.per


class _ConcurrencyRule:
    __slots__ = ("id", "regex", "limit", "per_uurl")

    def __init__(self, id: int, match: str | None, limit: int, per_uurl: bool) -> None:
        self.id = id
        self.regex: re.Pattern[str] | None = re.compile(match) if match is not None else None
        self.limit = int(limit)
        self.per_uurl = bool(per_uurl)

    def matches(self, uurl: str) -> bool:
        return self.regex is None or self.regex.search(uurl) is not None


class _Waiter:
    """A submit parked at the backpressure door (on_full='wait')."""

    __slots__ = ("event", "reserved")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.reserved = False  # True once the waker reserved pending capacity for us


class PyPermit:
    """Held admission: throttle tokens spent + concurrency slots acquired.

    ``release()`` is idempotent — the pipeline calls it in a finally block.
    """

    __slots__ = ("_scheduler", "_held", "_released")

    def __init__(self, scheduler: PyScheduler, held: list[asyncio.Semaphore]) -> None:
        self._scheduler = scheduler
        self._held = held
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        for sem in reversed(self._held):
            sem.release()
        self._scheduler._in_flight -= 1


class PyScheduler:
    """Reference implementation of the Scheduler protocol (see gracy._protocols).

    ``plan`` is the scheduler_plan dict emitted by gracy.plan.compile_plan
    (the same JSON shape handed to the Rust core).
    """

    def __init__(self, plan: dict[str, t.Any]) -> None:
        throttle = plan.get("throttle", {})
        queue = plan.get("queue", {})

        # mode="smooth" (GCRA) is a Rust-side optimization; the reference
        # implementation treats it exactly like "exact" — same external contract
        # ("never more than N grants in any trailing window"), stricter timing.
        self._mode: str = throttle.get("mode", "exact")
        self._throttle_rules = [_ThrottleRule(r) for r in throttle.get("rules", ())]

        self._conc_rules = [
            _ConcurrencyRule(r["id"], r["match"], r["limit"], r["per_uurl"])
            for r in plan.get("concurrency", ())
        ]
        # queue.max_at_once is an implicit GLOBAL concurrency rule. id=-1 keeps
        # it first in rule-id acquisition order, ahead of every user rule.
        max_at_once = queue.get("max_at_once")
        if max_at_once is not None:
            self._conc_rules.append(_ConcurrencyRule(-1, None, max_at_once, False))
        self._conc_rules.sort(key=lambda r: r.id)  # acquisition order: rule-id ascending
        self._semaphores: dict[tuple[int, str, str], asyncio.Semaphore] = {}  # created on demand

        self._max_pending: int = queue.get("max_pending", 10_000)
        self._on_full: str = queue.get("on_full", "wait")
        self._throttle_in_hooks: bool = queue.get("throttle_in_hooks", False)

        self._pending = 0  # submits occupying max_pending capacity (admitted, not yet granted)
        self._in_flight = 0  # granted permits not yet released
        self._waiters: list[tuple[int, int, _Waiter]] = []  # heap of (-priority, seq, waiter)
        self._seq = itertools.count()  # FIFO tie-break for equal priorities
        self._paused: dict[str, float] = {}  # scope ("client" | uurl) -> monotonic deadline
        self._throttle_hits: dict[int, int] = {}
        self._throttled_by_uurl: dict[str, int] = {}
        self._closed = False

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """No-op: this scheduler has no dispatcher task (submit() does the work inline)."""

    async def aclose(self) -> None:
        self._closed = True
        # Wake everyone parked at the door WITHOUT reserving capacity —
        # _admit sees reserved=False + closed and raises GracyClientClosedError.
        for _, _, waiter in self._waiters:
            waiter.event.set()
        self._waiters.clear()

    # ------------------------------------------------------------ admission

    async def submit(
        self,
        uurl: str,
        url: str,
        *,
        priority: int = 0,
        from_hook: bool = False,
        no_throttle: bool = False,
        conc_extra: str = "",
    ) -> PyPermit:
        if self._closed:
            raise GracyClientClosedError("submit() on a closed scheduler")

        await self._admit(priority)  # occupies pending capacity on success
        held: list[asyncio.Semaphore] = []
        try:
            if not from_hook:
                for rule in self._conc_rules:  # already sorted by rule id
                    if rule.matches(uurl):
                        sem = self._semaphore_for(rule, uurl, conc_extra)
                        await sem.acquire()
                        held.append(sem)
                # Pause gate BEFORE throttle: paused requests spend no window
                # tokens (they hold their concurrency slots — gates are short).
                await self._pause_gate(uurl)
            if not (no_throttle or (from_hook and not self._throttle_in_hooks)):
                await self._throttle(uurl, url)
            self._in_flight += 1
            return PyPermit(self, held)
        except BaseException:
            for sem in reversed(held):
                sem.release()
            raise
        finally:
            self._pending -= 1
            self._wake_next_waiter()

    async def _admit(self, priority: int) -> None:
        """max_pending backpressure. When full: raise, or park in priority order."""
        if self._pending < self._max_pending:
            self._pending += 1
            return
        if self._on_full == "raise":
            raise GracyQueueFull(f"queue is full ({self._max_pending} pending) and on_full='raise'")

        waiter = _Waiter()
        entry = (-priority, next(self._seq), waiter)
        heapq.heappush(self._waiters, entry)
        try:
            await waiter.event.wait()
        except asyncio.CancelledError:
            if waiter.reserved:  # capacity was already handed to us — pass it on
                self._pending -= 1
                self._wake_next_waiter()
            else:
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)
            raise
        if not waiter.reserved:  # only aclose() wakes without reserving
            raise GracyClientClosedError("scheduler closed while waiting for queue capacity")

    def _wake_next_waiter(self) -> None:
        """Hand freed capacity to the highest-priority (then oldest) parked submit."""
        while self._waiters and self._pending < self._max_pending:
            _, _, waiter = heapq.heappop(self._waiters)
            waiter.reserved = True
            self._pending += 1  # reserved on the waiter's behalf; it wakes up already admitted
            waiter.event.set()

    # ------------------------------------------------------------ concurrency

    def _semaphore_for(self, rule: _ConcurrencyRule, uurl: str, conc_extra: str) -> asyncio.Semaphore:
        key = (rule.id, uurl if rule.per_uurl else _GLOBAL_SCOPE, conc_extra)
        sem = self._semaphores.get(key)
        if sem is None:
            sem = self._semaphores[key] = asyncio.Semaphore(rule.limit)
        return sem

    # ------------------------------------------------------------ pause gates

    def pause(self, scope: str, seconds: float) -> None:
        """Dispatcher-level gate. scope: "client" or a uurl. Extends the current pause if longer."""
        until = time.monotonic() + seconds
        self._paused[scope] = max(self._paused.get(scope, 0.0), until)

    async def _pause_gate(self, uurl: str) -> None:
        while True:
            now = time.monotonic()
            until = max(self._paused.get("client", 0.0), self._paused.get(uurl, 0.0))
            if until <= now:
                return
            await asyncio.sleep(until - now)  # re-check: the pause may have been extended

    # ------------------------------------------------------------ throttle

    async def _throttle(self, uurl: str, url: str) -> None:
        """Sliding-window wait + reserve. The final check and the reservation
        happen with NO await in between — atomic under single-threaded asyncio,
        so admission can never over-commit a window."""
        matching = [rule for rule in self._throttle_rules if rule.regex.search(url)]
        if not matching:
            return

        hit_rules: set[int] = set()  # each rule counted at most once per submit
        counted_uurl = False
        while True:
            now = time.monotonic()
            wait = 0.0
            for rule in matching:
                rule_wait = max(0.0, rule.next_allowed(now) - now)  # NEVER negative
                wait = max(wait, rule_wait)
                if rule_wait > 0 and rule.id not in hit_rules:
                    hit_rules.add(rule.id)
                    self._throttle_hits[rule.id] = self._throttle_hits.get(rule.id, 0) + 1
            if wait <= 0:
                for rule in matching:  # reserve — no await since the check above
                    rule.timestamps.append(now)
                return
            if not counted_uurl:
                counted_uurl = True
                self._throttled_by_uurl[uurl] = self._throttled_by_uurl.get(uurl, 0) + 1
            await asyncio.sleep(wait)

    # ------------------------------------------------------------ observability

    def stats(self) -> dict[str, t.Any]:
        now = time.monotonic()
        return {
            # pending = every submit not yet granted: capacity holders + parked waiters
            "pending": self._pending + len(self._waiters),
            "in_flight": self._in_flight,
            "throttle_hits": dict(self._throttle_hits),
            "throttled_by_uurl": dict(self._throttled_by_uurl),
            "paused": {scope: until - now for scope, until in self._paused.items() if until > now},
        }
