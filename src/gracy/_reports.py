import re
from collections import defaultdict
from dataclasses import dataclass, field
from http import HTTPStatus
from statistics import mean
from typing import Final, List, Literal, Set

import httpx
from rich.console import Console
from rich.table import Table

from ._models import GracyRequestContext, ThrottleController


@dataclass(frozen=True)
class GracyRequestResult:
    __slots__ = ("uurl", "response")

    uurl: str
    response: httpx.Response


@dataclass
class ReportGenericAggregatedRequest:
    uurl: str
    """unformatted url"""

    total_requests: int

    resp_2xx: int
    resp_3xx: int
    resp_4xx: int
    resp_5xx: int

    max_latency: float

    @property
    def success_rate(self) -> float:
        if self.total_requests:
            return (self.resp_2xx / self.total_requests) * 100

        return 0

    @property
    def failed_rate(self) -> float:
        if self.total_requests:
            return 100.00 - self.success_rate

        return 0


@dataclass
class ReportAggregatedRequest(ReportGenericAggregatedRequest):
    avg_latency: float = 0
    req_rate_per_sec: float = 0


@dataclass
class GracyReportTotal(ReportGenericAggregatedRequest):
    all_avg_latency: list[float] = field(default_factory=list)
    all_req_rates: list[float] = field(default_factory=list)

    @property
    def avg_latency(self) -> float:
        entries = self.all_avg_latency or [0]
        return mean(entries)

    @property
    def req_rate_per_sec(self) -> float:
        entries = self.all_req_rates or [0]
        return mean(entries)

    def increment_result(self, row: ReportAggregatedRequest) -> None:
        self.total_requests += row.total_requests
        self.resp_2xx += row.resp_2xx
        self.resp_3xx += row.resp_3xx
        self.resp_4xx += row.resp_4xx
        self.resp_5xx += row.resp_5xx

        self.all_avg_latency.append(row.avg_latency)
        if row.req_rate_per_sec > 0:
            self.all_req_rates.append(row.req_rate_per_sec)


class GracyReport:
    def __init__(self) -> None:
        self.requests: list[ReportAggregatedRequest | GracyReportTotal] = []
        self.total = GracyReportTotal(
            "TOTAL",  # serves as title
            total_requests=0,
            resp_2xx=0,
            resp_3xx=0,
            resp_4xx=0,
            resp_5xx=0,
            max_latency=0,
        )

    def add_request(self, request: ReportAggregatedRequest) -> None:
        self.requests.append(request)
        self.total.increment_result(request)


ANY_REGEX: Final = r".+"


def unformatted_url_fits_formatted(unformatted: str, formatted: str) -> bool:
    unformatted_pattern = re.sub(r"{(\w+)}", ANY_REGEX, unformatted)
    match = re.fullmatch(unformatted_pattern, formatted)
    return bool(match)


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


REQUEST_SUM_KEY = HTTPStatus | Literal["total"]
REQUEST_SUM_PER_STATUS_TYPE = dict[str, defaultdict[REQUEST_SUM_KEY, int]]


class RichPrinter:
    def print_report(self, report: GracyReport) -> None:
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


class ReportBuilder:
    def __init__(self) -> None:
        self._results: List[GracyRequestResult] = []

    def track(self, request_context: GracyRequestContext, response: httpx.Response):
        self._results.append(GracyRequestResult(request_context.unformatted_url, response))

    def _calculate_req_rate_for_url(self, unformatted_url: str, throttle_controller: ThrottleController) -> float:
        pattern = re.compile(re.sub(r"{(\w+)}", ANY_REGEX, unformatted_url))
        rate = throttle_controller.calculate_requests_rate(pattern)
        return rate

    def print(self, throttle_controller: ThrottleController):
        requests_by_uurl = defaultdict[str, Set[httpx.Response]](set)
        requests_sum: REQUEST_SUM_PER_STATUS_TYPE = defaultdict(lambda: defaultdict(int))

        for result in self._results:
            requests_by_uurl[result.uurl].add(result.response)
            requests_sum[result.uurl]["total"] += 1
            requests_sum[result.uurl][HTTPStatus(result.response.status_code)] += 1

        requests_sum = dict(sorted(requests_sum.items(), key=lambda item: item[1]["total"], reverse=True))

        report = GracyReport()
        printer = RichPrinter()

        for uurl, data in requests_sum.items():
            all_requests = requests_by_uurl[uurl]

            total_requests = data["total"]
            url_latency = [r.elapsed.total_seconds() for r in all_requests]

            # Rate
            # Use min to handle scenarios like:
            # 10 reqs in a 2 millisecond window would produce a number >1,000 leading the user to think that we're
            # producing 1,000 requests which isn't true.
            rate = min(self._calculate_req_rate_for_url(uurl, throttle_controller), total_requests)

            report_request = ReportAggregatedRequest(
                uurl,
                total_requests,
                # fmt:off
                resp_2xx=sum(count for status, count in data.items() if status != "total" and 200 <= status.value < 300),  # noqa: E501,
                resp_3xx=sum(count for status, count in data.items() if status != "total" and 300 <= status.value < 400),  # noqa: E501,
                resp_4xx=sum(count for status, count in data.items() if status != "total" and 400 <= status.value < 500),  # noqa: E501,
                resp_5xx=sum(count for status, count in data.items() if status != "total" and 500 <= status.value),
                # fmt:on
                avg_latency=mean(url_latency),
                max_latency=max(url_latency),
                req_rate_per_sec=rate,
            )

            report.add_request(report_request)

        printer.print_report(report)
