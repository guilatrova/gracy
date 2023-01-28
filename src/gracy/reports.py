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

        for url, data in requests_sum.items():
            all_requests = requests_by_url[url]
            url_latency = [r.elapsed.total_seconds() for r in all_requests]

            avg_latency = mean(url_latency)
            max_latency = max(url_latency)
            total_requests = data["total"]

            successful_requests = sum(
                [count for status, count in data.items() if status != "total" and 200 <= status.value < 300]
            )
            failed_requests = total_requests - successful_requests
            success_rate = (successful_requests / total_requests) * 100
            failed_rate = (failed_requests / total_requests) * 100

            table.add_row(
                url,
                f"[bold]{total_requests}[/bold]",
                f"[green]{success_rate:.2f}%[/green]",
                f"[bold][red]{failed_rate:.2f}%[/bold][/red]",
                f"{avg_latency:.2f}",
                f"{max_latency:.2f}",
            )

            console.print(table)
