from collections import defaultdict
from threading import Lock
from time import time
from typing import Pattern

lock = Lock()


class ThrottleController:
    def __init__(self) -> None:
        self._control: dict[str, list[float]] = defaultdict[str, list[float]](list)

    def init_request(self, url: str):
        with lock:
            self._control[url].append(time())  # This should always keep it sorted asc

    def calculate_requests_per_second(self, url_pattern: Pattern[str]) -> float:
        with lock:
            requests_per_second_aggregate = 0.0

            for url, started_ats in self._control.items():
                if url_pattern.match(url):
                    requests_per_second = len(started_ats) / (started_ats[-1] - started_ats[0])
                    requests_per_second_aggregate += requests_per_second

            return requests_per_second_aggregate
