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
                    # we use `or 1` to avoid cases where there's just 1 req which results in 0, which is divided
                    start_end_diff = (started_ats[-1] - started_ats[0]) or 1
                    requests_per_second = len(started_ats) / start_end_diff
                    requests_per_second_aggregate += requests_per_second

            return requests_per_second_aggregate
