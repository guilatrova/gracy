"""Frozen report models (Reports v2, V2_PLAN.md §10).

`GracyReport` is an immutable snapshot produced by
`gracy.reports.collector.MetricsCollector.snapshot()`. Printers are pure
functions over it — the v1 "printing mutates the report / double-print"
bug class cannot exist here.

Documented v2 change: **success = 2xx + 3xx** responses (v1 counted only
2xx). Dashboards tracking success_rate will shift when upgrading.
"""

from __future__ import annotations

import logging
import typing as t
from dataclasses import dataclass

__all__ = ["GracyReport", "GracyRequestRow", "TOTAL_LABEL", "build_total_row"]

TOTAL_LABEL: t.Final = "TOTAL"


@dataclass(frozen=True, slots=True)
class GracyRequestRow:
    """Aggregated request metrics for one unformatted URL template (uurl)."""

    uurl: str
    total: int
    success_rate: float  # percentage 0-100; success = 2xx + 3xx (v2 change)
    failed_rate: float  # percentage 0-100; complement of success_rate
    status_counts: tuple[tuple[int, int], ...]  # ((status, count), ...) sorted by status
    aborts: int  # attempts that ended in a transport error (no response)
    retries: int  # attempts that were retries (attempt > 0)
    throttles: int  # scheduler throttle hits for this uurl
    replays: int  # responses served from replay storage
    avg_latency: float  # seconds; replayed attempts excluded
    max_latency: float  # seconds
    p95_latency: float  # seconds
    p99_latency: float  # seconds
    req_rate_per_sec: float  # observed request rate, capped at total


def build_total_row(rows: t.Sequence[GracyRequestRow]) -> GracyRequestRow:
    """Weighted aggregate across rows: rates and latency averages weighted by
    each row's total, max of maxes, plain sums everywhere else."""
    total = sum(row.total for row in rows)

    def weighted(values: t.Iterable[float]) -> float:
        if not total:
            return 0.0
        return sum(value * row.total for value, row in zip(values, rows)) / total

    merged_statuses: dict[int, int] = {}
    for row in rows:
        for status_code, count in row.status_counts:
            merged_statuses[status_code] = merged_statuses.get(status_code, 0) + count

    return GracyRequestRow(
        uurl=TOTAL_LABEL,
        total=total,
        success_rate=weighted(row.success_rate for row in rows),
        failed_rate=weighted(row.failed_rate for row in rows),
        status_counts=tuple(sorted(merged_statuses.items())),
        aborts=sum(row.aborts for row in rows),
        retries=sum(row.retries for row in rows),
        throttles=sum(row.throttles for row in rows),
        replays=sum(row.replays for row in rows),
        avg_latency=weighted(row.avg_latency for row in rows),
        max_latency=max((row.max_latency for row in rows), default=0.0),
        p95_latency=weighted(row.p95_latency for row in rows),
        p99_latency=weighted(row.p99_latency for row in rows),
        req_rate_per_sec=weighted(row.req_rate_per_sec for row in rows),
    )


@dataclass(frozen=True, slots=True)
class GracyReport:
    """Immutable metrics snapshot. Printing NEVER mutates it."""

    rows: tuple[GracyRequestRow, ...]  # sorted by total desc; TOTAL not included
    total_row: GracyRequestRow
    replays_active: bool = False

    @classmethod
    def from_rows(
        cls, rows: t.Iterable[GracyRequestRow], *, replays_active: bool = False
    ) -> GracyReport:
        frozen_rows = tuple(rows)
        return cls(
            rows=frozen_rows,
            total_row=build_total_row(frozen_rows),
            replays_active=replays_active,
        )

    def print(self, kind: str = "list", logger: logging.Logger | None = None) -> t.Any:
        """Render via gracy.reports.printers. kind: "list" | "logger" | "rich" | "plotly"."""
        from gracy.reports import printers

        if kind == "list":
            return printers.print_list(self)
        if kind == "logger":
            return printers.print_logger(self, logger)
        if kind == "rich":
            return printers.print_rich(self)
        if kind == "plotly":
            return printers.to_plotly(self)
        raise ValueError(
            f"Unknown report printer {kind!r}; expected 'list', 'logger', 'rich' or 'plotly'"
        )

    def to_plotly(self) -> t.Any:
        """Build (without showing) a plotly figure of totals per uurl."""
        from gracy.reports.printers import to_plotly

        return to_plotly(self)
