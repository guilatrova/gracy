import re
from collections import defaultdict
from dataclasses import dataclass
from http import HTTPStatus
from statistics import mean
from typing import List, Literal, Set, TypedDict

import httpx
from rich.console import Console
from rich.table import Table

from ._models import GracyRequestContext, ThrottleController


class FooterTotals(TypedDict):
    URL: str
    total: int
    success: int
    failed: int
    avg_latency: list[float]
    max_latency: float
    resp_2xx: int
    resp_3xx: int
    resp_4xx: int
    resp_5xx: int
    req_rates: list[float]


@dataclass(frozen=True)
class GracyRequestResult:
    __slots__ = ("url", "response")

    url: str
    response: httpx.Response


ANY_REGEX = r".+"


def unformatted_url_fits_formatted(unformatted: str, formatted: str) -> bool:
    unformatted_pattern = re.sub(r"{(\w+)}", ANY_REGEX, unformatted)
    match = re.fullmatch(unformatted_pattern, formatted)
    return bool(match)


REQUEST_SUM_KEY = HTTPStatus | Literal["total"]
REQUEST_SUM_PER_STATUS_TYPE = dict[str, defaultdict[REQUEST_SUM_KEY, int]]


class GracyReport:
    def __init__(self) -> None:
        self._results: List[GracyRequestResult] = []

    def track(self, request_context: GracyRequestContext, response: httpx.Response):
        self._results.append(GracyRequestResult(request_context.unformatted_url, response))

    def _calculate_req_rate_for_url(self, unformatted_url: str, throttle_controller: ThrottleController) -> float:
        pattern = re.compile(re.sub(r"{(\w+)}", ANY_REGEX, unformatted_url))
        rate = throttle_controller.calculate_requests_rate(pattern)
        return rate

    def print(self, throttle_controller: ThrottleController):
        requests_by_url = defaultdict[str, Set[httpx.Response]](set)
        requests_sum: REQUEST_SUM_PER_STATUS_TYPE = defaultdict(lambda: defaultdict(int))

        for result in self._results:
            requests_by_url[result.url].add(result.response)
            requests_sum[result.url]["total"] += 1
            requests_sum[result.url][HTTPStatus(result.response.status_code)] += 1

        requests_sum = dict(sorted(requests_sum.items(), key=lambda item: item[1]["total"], reverse=True))

        console = Console()
        table = Table(title="HTTP Requests Summary", show_lines=True)
        table.add_column("URL", overflow="fold")
        table.add_column("Total Requests (#)", justify="right")
        table.add_column("Successful Requests (%)", justify="right")
        table.add_column("Failed Requests (%)", justify="right")
        table.add_column("Avg Latency (s)", justify="right")
        table.add_column("Max Latency (s)", justify="right")
        table.add_column("2xx Responses", justify="right")
        table.add_column("3xx Responses", justify="right")
        table.add_column("4xx Responses", justify="right")
        table.add_column(">5xx Responses", justify="right")
        table.add_column("Avg Reqs/sec", justify="right")

        footer_totals = FooterTotals(
            URL="[b]TOTAL[/b]",
            total=0,
            success=0,
            failed=0,
            avg_latency=[],
            max_latency=0.0,
            resp_2xx=0,
            resp_3xx=0,
            resp_4xx=0,
            resp_5xx=0,
            req_rates=[],
        )

        for url, data in requests_sum.items():
            all_requests = requests_by_url[url]

            # Latency
            url_latency = [r.elapsed.total_seconds() for r in all_requests]
            avg_latency = mean(url_latency)
            max_latency = max(url_latency)

            # Total and Rates
            total_requests = data["total"]
            successful_requests = sum(
                [count for status, count in data.items() if status != "total" and 200 <= status.value < 300]
            )
            failed_requests = total_requests - successful_requests
            success_rate = (successful_requests / total_requests) * 100
            failed_rate = (failed_requests / total_requests) * 100
            failed_color = "red" if failed_requests else "white"

            # Status Ranges
            # fmt:off
            responses_2xx = sum(count for status, count in data.items() if status != "total" and 200 <= status.value < 300)  # noqa: E501
            responses_3xx = sum(count for status, count in data.items() if status != "total" and 300 <= status.value < 400)  # noqa: E501
            responses_4xx = sum(count for status, count in data.items() if status != "total" and 400 <= status.value < 500)  # noqa: E501
            responses_5xx = sum(count for status, count in data.items() if status != "total" and 500 <= status.value)

            color_4xx = "red" if responses_4xx else "white"
            color_5xx = "red" if responses_5xx else "white"
            # fmt:on

            # Rate
            rate = self._calculate_req_rate_for_url(url, throttle_controller)

            # Footer
            footer_totals["total"] += total_requests
            footer_totals["success"] += successful_requests
            footer_totals["failed"] += total_requests - successful_requests
            footer_totals["avg_latency"] += url_latency
            footer_totals["max_latency"] = max(footer_totals["max_latency"], max_latency)
            footer_totals["resp_2xx"] += responses_2xx
            footer_totals["resp_3xx"] += responses_3xx
            footer_totals["resp_4xx"] += responses_4xx
            footer_totals["resp_5xx"] += responses_5xx
            footer_totals["req_rates"].append(rate)

            # Row
            table.add_row(
                url,
                f"[bold]{total_requests:,}[/bold]",
                f"[green]{success_rate:,.2f}%[/green]",
                f"[bold][{failed_color}]{failed_rate:,.2f}%[/bold][/{failed_color}]",
                f"{avg_latency:,.2f}",
                f"{max_latency:,.2f}",
                str(responses_2xx),
                str(responses_3xx),
                f"[{color_4xx}]{responses_4xx}[/{color_4xx}]",
                f"[{color_5xx}]{responses_5xx}[/{color_5xx}]",
                f"{rate:,.1f} reqs/s",
            )

        # Handle div by 0
        footer_total = footer_totals["total"] or 1
        footer_avg_latency = footer_totals["avg_latency"] or [0]
        footer_avg_rate = footer_totals["req_rates"] or [0]
        table.add_row(
            footer_totals["URL"],
            f"{footer_totals['total']:,}",
            f"{((footer_totals['success'] / footer_total) * 100):,.2f}%",
            f"{((footer_totals['failed'] / footer_total) * 100):,.2f}%",
            f"{mean(footer_avg_latency):,.2f}",
            f"{footer_totals['max_latency']:,.2f}",
            str(footer_totals["resp_2xx"]),
            str(footer_totals["resp_3xx"]),
            str(footer_totals["resp_4xx"]),
            str(footer_totals["resp_5xx"]),
            f"{mean(footer_avg_rate):,.1f} reqs/s",
        )

        console.print(table)
