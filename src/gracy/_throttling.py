import itertools
from collections import defaultdict
from datetime import datetime
from threading import Lock
from time import time
from typing import Pattern

from rich.console import Console
from rich.table import Table

lock = Lock()


class ThrottleController:
    def __init__(self) -> None:
        self._control: dict[str, list[float]] = defaultdict[str, list[float]](list)

    def init_request(self, url: str):
        with lock:
            self._control[url].append(time())  # This should always keep it sorted asc

    def calculate_requests_per_second(self, url_pattern: Pattern[str]) -> float:
        with lock:
            up_to_one_sec = time() - 1
            requests_per_second = 0.0
            coalesced_started_ats = sorted(
                itertools.chain(*[started_ats for url, started_ats in self._control.items() if url_pattern.match(url)])
            )

            if coalesced_started_ats:
                requests = [request for request in coalesced_started_ats if request >= up_to_one_sec]
                requests_per_second = len(requests) / 1

            return requests_per_second

    def debug_print(self):
        console = Console()
        table = Table(title="Throttling Summary")
        table.add_column("URL", overflow="fold")
        table.add_column("Times", justify="right")

        for url, times in self._control.items():
            human_times = [datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f") for ts in times]
            table.add_row(url, str(human_times))

        console.print(table)
