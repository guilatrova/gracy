"""Pure report renderers. Every function takes a frozen GracyReport and NEVER
mutates it (v1 printers appended the TOTAL row into the report's own list, so
printing twice duplicated rows).

rich / plotly are optional extras — imported lazily with instructive errors.
"""

from __future__ import annotations

import logging
import typing as t

from gracy._types import REQUEST_ERROR_STATUS

if t.TYPE_CHECKING:
    from gracy.reports import GracyReport, GracyRequestRow

_HEADER: t.Final = "Gracy Report"

_COLUMN_TITLES: t.Final[tuple[str, ...]] = (
    "URL",
    "Total Reqs (#)",
    "Success (%)",
    "Fail (%)",
    "Statuses",
    "Aborts",
    "Retries",
    "Throttles",
    "Replays",
    "Avg Latency (s)",
    "Max Latency (s)",
    "P95 Latency (s)",
    "P99 Latency (s)",
    "Avg Reqs/sec",
)


# --------------------------------------------------------------------------- shared formatting


def _format_statuses(row: GracyRequestRow) -> str:
    parts: list[str] = []
    for status_code, count in row.status_counts:
        label = "ERR" if status_code == REQUEST_ERROR_STATUS else str(status_code)
        parts.append(f"{label}:{count}")
    return ", ".join(parts) if parts else "-"


def _cells(row: GracyRequestRow) -> tuple[str, ...]:
    """One formatted value per _COLUMN_TITLES entry."""
    return (
        row.uurl,
        f"{row.total:,}",
        f"{row.success_rate:,.2f}%",
        f"{row.failed_rate:,.2f}%",
        _format_statuses(row),
        f"{row.aborts:,}",
        f"{row.retries:,}",
        f"{row.throttles:,}",
        f"{row.replays:,}",
        f"{row.avg_latency:,.3f}",
        f"{row.max_latency:,.3f}",
        f"{row.p95_latency:,.3f}",
        f"{row.p99_latency:,.3f}",
        f"{row.req_rate_per_sec:,.1f}",
    )


def _all_rows(report: GracyReport) -> tuple[GracyRequestRow, ...]:
    """NEW tuple with TOTAL appended — report.rows is never touched."""
    return (*report.rows, report.total_row)


# --------------------------------------------------------------------------- logger


def print_logger(report: GracyReport, logger: logging.Logger | None = None) -> None:
    """One INFO line per row, TOTAL last."""
    log = logger if logger is not None else logging.getLogger("gracy")
    for row in _all_rows(report):
        log.info(
            "%s | total=%d | success=%.2f%% | fail=%.2f%% | statuses=%s | aborts=%d | "
            "retries=%d | throttles=%d | replays=%d | avg=%.3fs | max=%.3fs | "
            "p95=%.3fs | p99=%.3fs | rate=%.1f reqs/s",
            row.uurl,
            row.total,
            row.success_rate,
            row.failed_rate,
            _format_statuses(row),
            row.aborts,
            row.retries,
            row.throttles,
            row.replays,
            row.avg_latency,
            row.max_latency,
            row.p95_latency,
            row.p99_latency,
            row.req_rate_per_sec,
        )


# --------------------------------------------------------------------------- plain list


def print_list(report: GracyReport) -> None:
    """Plain-text blocks: header, one block per row (all fields), TOTAL last."""
    print(_HEADER)
    print("=" * len(_HEADER))
    if report.replays_active:
        print("(some responses were replayed; latencies cover live requests only)")

    entries = _all_rows(report)
    last_index = len(entries)
    for index, row in enumerate(entries, 1):
        title = row.uurl if index == last_index else f"{index}. {row.uurl}"
        print(f"\n{title}")
        for name, value in zip(_COLUMN_TITLES[1:], _cells(row)[1:]):
            print(f"  {name + ':':<17} {value}")


# --------------------------------------------------------------------------- rich


def print_rich(report: GracyReport) -> None:
    """rich table with all columns; TOTAL as a bold final row."""
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError as e:  # pragma: no cover - depends on environment
        raise ImportError(
            "The 'rich' report printer requires the rich package. "
            "Install it with: pip install gracy[rich]"
        ) from e

    title = "Gracy Requests Summary"
    if report.replays_active:
        title += " [yellow](replays active)[/yellow]"
    table = Table(title=title)

    table.add_column(_COLUMN_TITLES[0], overflow="fold")
    for column_title in _COLUMN_TITLES[1:]:
        table.add_column(column_title, justify="right")

    # A NEW list of renderables — report.rows is never appended to or reordered.
    renderables: list[tuple[str, ...]] = [_cells(row) for row in report.rows]
    for cells in renderables:
        table.add_row(*cells)

    table.add_section()
    table.add_row(*(f"[bold]{cell}[/bold]" for cell in _cells(report.total_row)))

    Console().print(table)


# --------------------------------------------------------------------------- plotly


def to_plotly(report: GracyReport) -> t.Any:
    """Bar chart of total requests per uurl. Returns the figure WITHOUT showing it."""
    try:
        import pandas as pd  # pyright: ignore[reportMissingModuleSource]
        import plotly.express as px  # pyright: ignore[reportMissingImports]
    except ImportError as e:  # pragma: no cover - depends on environment
        raise ImportError(
            "The plotly report requires plotly and pandas. "
            "Install them with: pip install gracy[plotly]"
        ) from e

    df = pd.DataFrame(
        {
            "URL": [row.uurl for row in report.rows],
            "Total Requests": [row.total for row in report.rows],
        }
    )
    fig = px.bar(df, x="URL", y="Total Requests", title="Gracy Requests per URL")
    return fig
