import logging
from abc import ABC, abstractmethod
from typing import Final, Literal

from ._models import GracyAggregatedTotal, GracyReport

logger = logging.getLogger("gracy")

PRINTERS = Literal["rich", "list", "logger"]


class Titles:
    url: Final = "URL"
    total_requests: Final = "Total Reqs (#)"
    success_rate: Final = "Success (%)"
    failed_rate: Final = "Fail (%)"
    avg_latency: Final = "Avg Latency (s)"
    max_latency: Final = "Max Latency (s)"
    resp_2xx: Final = "2xx Resps"
    resp_3xx: Final = "3xx Resps"
    resp_4xx: Final = "4xx Resps"
    resp_5xx: Final = "5xx Resps"
    req_rate_per_sec: Final = "Avg Reqs/sec"


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


def _print_header():
    print("   ____")
    print("  / ___|_ __ __ _  ___ _   _")
    print(" | |  _| '__/ _` |/ __| | | |")
    print(" | |_| | | | (_| | (__| |_| |")
    print("  \\____|_|  \\__,_|\\___|\\__, |")
    print("                       |___/  Requests Summary Report")


class BasePrinter(ABC):
    @abstractmethod
    def print_report(self, report: GracyReport) -> None:
        pass


class RichPrinter(BasePrinter):
    def print_report(self, report: GracyReport) -> None:
        # Dynamic import so we don't have to require it as dependency
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Gracy Requests Summary")

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
        table.add_column(Titles.req_rate_per_sec, justify="right")

        rows = report.requests
        report.total.uurl = f"[bold]{report.total.uurl}[/bold]"
        rows.append(report.total)

        for idx, request_row in enumerate(rows):
            is_last_line_before_footer = idx < len(rows) - 1 and isinstance(rows[idx + 1], GracyAggregatedTotal)

            table.add_row(
                request_row.uurl,
                _format_int(request_row.total_requests, bold=True),
                _format_value(request_row.success_rate, "green", suffix="%"),
                _format_value(request_row.failed_rate, None, "red", bold=True, suffix="%"),
                _format_value(request_row.avg_latency),
                _format_value(request_row.max_latency),
                _format_int(request_row.resp_2xx),
                _format_int(request_row.resp_3xx),
                _format_int(request_row.resp_4xx, isset_color="red"),
                _format_int(request_row.resp_5xx, isset_color="red"),
                _format_value(request_row.req_rate_per_sec, precision=1, suffix=" reqs/s"),
                end_section=is_last_line_before_footer,
            )

        console.print(table)


class ListPrinter(BasePrinter):
    def print_report(self, report: GracyReport) -> None:
        _print_header()

        entries = report.requests
        entries.append(report.total)

        PAD_PREFIX: Final = 20

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
