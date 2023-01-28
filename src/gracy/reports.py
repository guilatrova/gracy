from collections import defaultdict
from dataclasses import dataclass
from http import HTTPStatus
from statistics import mean
from typing import Dict, List, Literal, Set

import httpx
from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class GracyRequestResult:
    __slots__ = ("url", "response")

    url: str
    response: httpx.Response


class GracyReport:
    def __init__(self) -> None:
        self._results: List[GracyRequestResult] = []

    def track(self, request_result: GracyRequestResult):
        self._results.append(request_result)

    def print(self):
        requests_by_url = defaultdict[str, Set[httpx.Response]](set)
        requests_sum = defaultdict[str, Dict[HTTPStatus | Literal["total"], int]](
            lambda: defaultdict[HTTPStatus | Literal["total"], int](int)
        )

        for result in self._results:
            requests_by_url[result.url].add(result.response)
            requests_sum[result.url]["total"] += 1
            requests_sum[result.url][HTTPStatus(result.response.status_code)] += 1

        console = Console()
        table = Table(title="HTTP Requests Summary", show_lines=True)
        table.add_column("URL")
        table.add_column("Total Requests (#)", justify="right")
        table.add_column("Successful Requests (%)", justify="right")
        table.add_column("Failed Requests (%)", justify="right")
        table.add_column("Avg Latency (s)", justify="right")
        table.add_column("Max Latency (s)", justify="right")
        table.add_column("2xx Responses", justify="right")
        table.add_column("3xx Responses", justify="right")
        table.add_column("4xx Responses", justify="right")
        table.add_column(">5xx Responses", justify="right")

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
            failed_color = "red" if failed_requests else "green"

            # Status Ranges
            # fmt:off
            responses_2xx = sum(count for status, count in data.items() if status != "total" and 200 <= status.value < 300)  # noqa: E501
            responses_3xx = sum(count for status, count in data.items() if status != "total" and 300 <= status.value < 400)  # noqa: E501
            responses_4xx = sum(count for status, count in data.items() if status != "total" and 400 <= status.value < 500)  # noqa: E501
            responses_5xx = sum(count for status, count in data.items() if status != "total" and 500 <= status.value)
            # fmt:on

            table.add_row(
                url,
                f"[bold]{total_requests}[/bold]",
                f"[green]{success_rate:.2f}%[/green]",
                f"[bold][{failed_color}]{failed_rate:.2f}%[/bold][/{failed_color}]",
                f"{avg_latency:.2f}",
                f"{max_latency:.2f}",
                str(responses_2xx),
                str(responses_3xx),
                str(responses_4xx),
                str(responses_5xx),
            )

            console.print(table)
