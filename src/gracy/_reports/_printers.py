from typing import Literal

from ._models import GracyReport, GracyReportTotal

PRINTERS = Literal["rich", "list"]


def _format_value(
    val: float,
    color: str | None = None,
    isset_color: str | None = None,
    precision: int = 2,
    bold: bool = False,
    suffix: str = "",
) -> str:
    cur = f"{val:,.{precision}f}{suffix}"

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
    suffix: str = "",
) -> str:
    cur = f"{val:,}{suffix}"

    if bold:
        cur = f"[bold]{cur}[/bold]"

    if val and isset_color:
        cur = f"[{isset_color}]{cur}[/{isset_color}]"
    elif color:
        cur = f"[{color}]{cur}[/{color}]"

    return cur


class RichPrinter:
    def print_report(self, report: GracyReport) -> None:
        # Dynamic import so we don't have to require it as dependency
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Gracy Requests Summary")

        table.add_column("URL", overflow="fold")
        table.add_column("Total Reqs (#)", justify="right")
        table.add_column("Success (%)", justify="right")
        table.add_column("Fail (%)", justify="right")
        table.add_column("Avg Latency (s)", justify="right")
        table.add_column("Max Latency (s)", justify="right")
        table.add_column("2xx Resps", justify="right")
        table.add_column("3xx Resps", justify="right")
        table.add_column("4xx Resps", justify="right")
        table.add_column("5xx Resps", justify="right")
        table.add_column("Avg Reqs/sec", justify="right")

        rows = report.requests
        report.total.uurl = f"[bold]{report.total.uurl}[/bold]"
        rows.append(report.total)

        for idx, request_row in enumerate(rows):
            is_last_line_before_footer = idx < len(rows) - 1 and isinstance(rows[idx + 1], GracyReportTotal)

            table.add_row(
                request_row.uurl,
                f"[bold]{request_row.total_requests:,}[/bold]",
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


def print_report(report: GracyReport, method: PRINTERS):
    printer = None
    if method == "rich":
        printer = RichPrinter()

    if printer:
        printer.print_report(report)
