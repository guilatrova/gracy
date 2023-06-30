from __future__ import annotations

import logging
import typing as t
from abc import ABC, abstractmethod

from ..replays.storages._base import GracyReplay
from ._models import GracyAggregatedTotal, GracyReport

logger = logging.getLogger("gracy")

PRINTERS = t.Literal["rich", "list", "logger"]


class Titles:
    url: t.Final = "URL"
    total_requests: t.Final = "Total Reqs (#)"
    success_rate: t.Final = "Success (%)"
    failed_rate: t.Final = "Fail (%)"
    avg_latency: t.Final = "Avg Latency (s)"
    max_latency: t.Final = "Max Latency (s)"
    resp_2xx: t.Final = "2xx Resps"
    resp_3xx: t.Final = "3xx Resps"
    resp_4xx: t.Final = "4xx Resps"
    resp_5xx: t.Final = "5xx Resps"
    reqs_aborted: t.Final = "Aborts"
    retries: t.Final = "Retries"
    throttles: t.Final = "Throttles"
    replays: t.Final = "Replays"
    req_rate_per_sec: t.Final = "Avg Reqs/sec"


def _getreplays_warn(replay_settings: GracyReplay | None) -> str:
    res = ""
    if replay_settings and replay_settings.display_report:
        if replay_settings.records_made:
            res = f"{replay_settings.records_made:,} Requests Recorded"

        if replay_settings.replays_made:
            if res:
                res += " / "

            res += f"{replay_settings.replays_made:,} Requests Replayed"

    if res:
        return f"({res})"

    return res


def _format_value(
    val: float,
    color: str | None = None,
    isset_color: str | None = None,
    precision: int = 2,
    bold: bool = False,
    prefix: str = "",
    suffix: str = "",
    padprefix: int = 0,
) -> str:
    cur = f"{prefix.rjust(padprefix)}{val:,.{precision}f}{suffix}"

    if bold:
        cur = f"[bold]{cur}[/bold]"

    if val and isset_color:
        cur = f"[{isset_color}]{cur}[/{isset_color}]"
    elif color:
        cur = f"[{color}]{cur}[/{color}]"

    return cur


def _format_int(
    val: int,
    color: str | None = None,
    isset_color: str | None = None,
    bold: bool = False,
    prefix: str = "",
    suffix: str = "",
    padprefix: int = 0,
) -> str:
    cur = f"{prefix.rjust(padprefix)}{val:,}{suffix}"

    if bold:
        cur = f"[bold]{cur}[/bold]"

    if val and isset_color:
        cur = f"[{isset_color}]{cur}[/{isset_color}]"
    elif color:
        cur = f"[{color}]{cur}[/{color}]"

    return cur


def _print_header(report: GracyReport):
    print("   ____")
    print("  / ___|_ __ __ _  ___ _   _")
    print(" | |  _| '__/ _` |/ __| | | |")
    print(" | |_| | | | (_| | (__| |_| |")
    print("  \\____|_|  \\__,_|\\___|\\__, |")
    print(f"                       |___/  Requests Summary Report {_getreplays_warn(report.replay_settings)}")


class BasePrinter(ABC):
    @abstractmethod
    def print_report(self, report: GracyReport) -> None:
        pass


class RichPrinter(BasePrinter):
    def print_report(self, report: GracyReport) -> None:
        # Dynamic import so we don't have to require it as dependency
        from rich.console import Console
        from rich.table import Table

        in_replay_mode = report.replay_settings and report.replay_settings.display_report

        console = Console()
        title_warn = f"[yellow]{_getreplays_warn(report.replay_settings)}[/yellow]" if in_replay_mode else ""
        table = Table(title=f"Gracy Requests Summary {title_warn}")

        table.add_column(Titles.url, overflow="fold")
        table.add_column(Titles.total_requests, justify="right")
        table.add_column(Titles.success_rate, justify="right")
        table.add_column(Titles.failed_rate, justify="right")
        table.add_column(Titles.avg_latency, justify="right")
        table.add_column(Titles.max_latency, justify="right")
        table.add_column(Titles.resp_2xx, justify="right")
        table.add_column(Titles.resp_3xx, justify="right")
        table.add_column(Titles.resp_4xx, justify="right")
        table.add_column(Titles.resp_5xx, justify="right")
        table.add_column(Titles.reqs_aborted, justify="right")
        table.add_column(Titles.retries, justify="right")
        table.add_column(Titles.throttles, justify="right")

        if in_replay_mode:
            table.add_column(Titles.replays, justify="right")

        table.add_column(Titles.req_rate_per_sec, justify="right")

        rows = report.requests
        report.total.uurl = f"[bold]{report.total.uurl}[/bold]"
        rows.append(report.total)

        for idx, request_row in enumerate(rows):
            is_last_line_before_footer = idx < len(rows) - 1 and isinstance(rows[idx + 1], GracyAggregatedTotal)

            row_values: tuple[str, ...] = (
                _format_int(request_row.total_requests, bold=True),
                _format_value(request_row.success_rate, "green", suffix="%"),
                _format_value(request_row.failed_rate, None, "red", bold=True, suffix="%"),
                _format_value(request_row.avg_latency),
                _format_value(request_row.max_latency),
                _format_int(request_row.resp_2xx),
                _format_int(request_row.resp_3xx),
                _format_int(request_row.resp_4xx, isset_color="red"),
                _format_int(request_row.resp_5xx, isset_color="red"),
                _format_int(request_row.reqs_aborted, isset_color="red"),
                _format_int(request_row.retries, isset_color="yellow"),
                _format_int(request_row.throttles, isset_color="yellow"),
            )

            if in_replay_mode:
                row_values = (
                    *row_values,
                    _format_int(request_row.replays, isset_color="yellow"),
                )

            table.add_row(
                request_row.uurl,
                *row_values,
                _format_value(request_row.req_rate_per_sec, precision=1, suffix=" reqs/s"),
                end_section=is_last_line_before_footer,
            )

        console.print(table)


class ListPrinter(BasePrinter):
    def print_report(self, report: GracyReport) -> None:
        _print_header(report)

        entries = report.requests
        entries.append(report.total)
        in_replay_mode = report.replay_settings and report.replay_settings.display_report

        PAD_PREFIX: t.Final = 20

        for idx, entry in enumerate(entries, 1):
            title = entry.uurl if idx == len(entries) else f"{idx}. {entry.uurl}"
            print(f"\n\n{title}")

            print(_format_int(entry.total_requests, padprefix=PAD_PREFIX, prefix=f"{Titles.total_requests}: "))
            print(
                _format_value(entry.success_rate, padprefix=PAD_PREFIX, prefix=f"{Titles.success_rate}: ", suffix="%")
            )
            print(_format_value(entry.failed_rate, padprefix=PAD_PREFIX, prefix=f"{Titles.failed_rate}: ", suffix="%"))
            print(_format_value(entry.avg_latency, padprefix=PAD_PREFIX, prefix=f"{Titles.avg_latency}: "))
            print(_format_value(entry.max_latency, padprefix=PAD_PREFIX, prefix=f"{Titles.max_latency}: "))
            print(_format_int(entry.resp_2xx, padprefix=PAD_PREFIX, prefix=f"{Titles.resp_2xx}: "))
            print(_format_int(entry.resp_3xx, padprefix=PAD_PREFIX, prefix=f"{Titles.resp_3xx}: "))
            print(_format_int(entry.resp_4xx, padprefix=PAD_PREFIX, prefix=f"{Titles.resp_4xx}: "))
            print(_format_int(entry.resp_5xx, padprefix=PAD_PREFIX, prefix=f"{Titles.resp_5xx}: "))
            print(_format_int(entry.reqs_aborted, padprefix=PAD_PREFIX, prefix=f"{Titles.reqs_aborted}: "))
            print(_format_int(entry.retries, padprefix=PAD_PREFIX, prefix=f"{Titles.retries}: "))
            print(_format_int(entry.throttles, padprefix=PAD_PREFIX, prefix=f"{Titles.throttles}: "))

            if in_replay_mode:
                print(_format_int(entry.replays, padprefix=PAD_PREFIX, prefix=f"{Titles.replays}: "))

            print(
                _format_value(
                    entry.req_rate_per_sec,
                    precision=1,
                    padprefix=PAD_PREFIX,
                    prefix=f"{Titles.req_rate_per_sec}: ",
                    suffix=" reqs/s",
                )
            )


class LoggerPrinter(BasePrinter):
    def print_report(self, report: GracyReport) -> None:
        # the first entry should be the most frequent URL hit
        if not report.requests:
            logger.warning("No requests were triggered")
            return

        first_entry, *_ = report.requests
        total = report.total

        logger.info(
            f"Gracy tracked that '{first_entry.uurl}' was hit {_format_int(first_entry.total_requests)} time(s) "
            f"with a success rate of {_format_value(first_entry.success_rate, suffix='%')}, "
            f"avg latency of {_format_value(first_entry.avg_latency)}s, "
            f"and a rate of {_format_value(first_entry.req_rate_per_sec, precision=1, suffix=' reqs/s')}."
        )

        logger.info(
            f"Gracy tracked a total of {_format_int(total.total_requests)} requests "
            f"with a success rate of {_format_value(total.success_rate, suffix='%')}, "
            f"avg latency of {_format_value(total.avg_latency)}s, "
            f"and a rate of {_format_value(total.req_rate_per_sec, precision=1, suffix=' reqs/s')}."
        )

        if replay := report.replay_settings:
            if replay.mode == "record":
                logger.info("All requests were recorded with GracyReplay")
            else:
                logger.warning("All requests were REPLAYED (no HTTP interaction) with GracyReplay")


def print_report(report: GracyReport, method: PRINTERS):
    printer: BasePrinter | None = None
    if method == "rich":
        printer = RichPrinter()
    elif method == "list":
        printer = ListPrinter()
    elif method == "logger":
        printer = LoggerPrinter()

    if printer:
        printer.print_report(report)
