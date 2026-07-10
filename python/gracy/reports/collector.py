"""Per-client-instance metrics aggregation.

PER-CLIENT-INSTANCE by design: v1 kept class-level global state, so two
clients (or two tests) polluted each other's reports. Each Gracy instance
owns one MetricsCollector; cross-client aggregation is an explicit opt-in
at the client layer, never an accident here.

The pipeline calls track() once per ATTEMPT (retries included), so `total`
counts attempts — v1 parity.
"""

from __future__ import annotations

import statistics
import time
import typing as t
from collections import deque
from dataclasses import dataclass, field

from gracy._types import REQUEST_ERROR_STATUS, RequestContext, Response
from gracy.reports import GracyReport, GracyRequestRow

_MAX_LATENCY_SAMPLES: t.Final = 10_000
_MIN_ELAPSED_S: t.Final = 0.001


@dataclass(slots=True)
class _UurlStats:
    """Mutable per-uurl accumulator. Internal to the collector."""

    total: int = 0
    status_counts: dict[int, int] = field(default_factory=dict)
    aborts: int = 0
    retries: int = 0
    replays: int = 0
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=_MAX_LATENCY_SAMPLES))
    first_ts: float | None = None  # wall clock (time.time) of first tracked attempt
    last_ts: float | None = None  # wall clock of most recent tracked attempt


class MetricsCollector:
    """Aggregates one client's request attempts; snapshot() emits a frozen GracyReport."""

    def __init__(self) -> None:
        self._by_uurl: dict[str, _UurlStats] = {}

    # ------------------------------------------------------------------ ingest

    def track(
        self,
        context: RequestContext,
        response: Response | None,
        exc: Exception | None,
        *,
        replayed: bool,
        retry: bool,
    ) -> None:
        """Record one attempt. Called by the pipeline after each attempt completes."""
        stats = self._by_uurl.get(context.uurl)
        if stats is None:
            stats = self._by_uurl[context.uurl] = _UurlStats()

        now = time.time()
        if stats.first_ts is None:
            stats.first_ts = now
        stats.last_ts = now

        stats.total += 1
        status_code = response.status if response is not None else REQUEST_ERROR_STATUS
        stats.status_counts[status_code] = stats.status_counts.get(status_code, 0) + 1
        if exc is not None:
            stats.aborts += 1
        if retry:
            stats.retries += 1
        if replayed:
            stats.replays += 1
        # Replayed responses carry recorded (or zero) latency — excluding them
        # keeps the latency columns about the live wire only.
        if context.elapsed is not None and not replayed:
            stats.latencies.append(context.elapsed)

    # ------------------------------------------------------------------ snapshot

    def snapshot(self, scheduler_stats: dict[str, t.Any] | None = None) -> GracyReport:
        """Build a frozen GracyReport. Throttle counts come from the scheduler's
        stats() dict (key "throttled_by_uurl") when provided."""
        throttled_by_uurl: t.Mapping[str, int] = {}
        if scheduler_stats:
            throttled_by_uurl = scheduler_stats.get("throttled_by_uurl") or {}

        rows = [
            self._build_row(uurl, stats, int(throttled_by_uurl.get(uurl, 0)))
            for uurl, stats in self._by_uurl.items()
        ]
        rows.sort(key=lambda row: row.total, reverse=True)
        replays_active = any(row.replays for row in rows)
        return GracyReport.from_rows(rows, replays_active=replays_active)

    def reset(self) -> None:
        """Drop every tracked metric (replaces v1's dangerously_reset_report)."""
        self._by_uurl.clear()

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _build_row(uurl: str, stats: _UurlStats, throttles: int) -> GracyRequestRow:
        total = stats.total
        successes = sum(
            count for status_code, count in stats.status_counts.items() if 200 <= status_code < 400
        )
        success_rate = (successes / total) * 100.0 if total else 0.0
        failed_rate = 100.0 - success_rate if total else 0.0

        latencies = list(stats.latencies)
        if latencies:
            avg_latency = statistics.fmean(latencies)
            max_latency = max(latencies)
            if len(latencies) < 2:  # quantiles() needs >= 2 data points
                p95_latency = p99_latency = max_latency
            else:
                # inclusive: percentiles stay within [min, max] of the sample
                cuts = statistics.quantiles(latencies, n=100, method="inclusive")
                p95_latency, p99_latency = cuts[94], cuts[98]
        else:
            avg_latency = max_latency = p95_latency = p99_latency = 0.0

        # Wall-clock observed rate; the cap keeps "3 requests in 2ms" from
        # rendering as thousands of reqs/s (v1 parity).
        if stats.first_ts is not None and stats.last_ts is not None:
            window = max(stats.last_ts - stats.first_ts, _MIN_ELAPSED_S)
            req_rate = min(total / window, float(total))
        else:  # pragma: no cover - a tracked uurl always has timestamps
            req_rate = 0.0

        return GracyRequestRow(
            uurl=uurl,
            total=total,
            success_rate=success_rate,
            failed_rate=failed_rate,
            status_counts=tuple(sorted(stats.status_counts.items())),
            aborts=stats.aborts,
            retries=stats.retries,
            throttles=throttles,
            replays=stats.replays,
            avg_latency=avg_latency,
            max_latency=max_latency,
            p95_latency=p95_latency,
            p99_latency=p99_latency,
            req_rate_per_sec=req_rate,
        )
