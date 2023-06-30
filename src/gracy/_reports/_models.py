from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

import httpx

from ..replays.storages._base import GracyReplay


@dataclass(frozen=True)
class GracyRequestResult:
    __slots__ = ("uurl", "response")

    uurl: str
    response: httpx.Response | Exception


@dataclass
class GracyRequestCounters:
    throttles: int = 0
    retries: int = 0
    replays: int = 0


@dataclass
class ReportGenericAggregatedRequest:
    uurl: str
    """unformatted url"""

    total_requests: int

    resp_2xx: int
    resp_3xx: int
    resp_4xx: int
    resp_5xx: int
    reqs_aborted: int

    retries: int
    throttles: int
    replays: int

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
class GracyAggregatedRequest(ReportGenericAggregatedRequest):
    avg_latency: float = 0
    req_rate_per_sec: float = 0


@dataclass
class GracyAggregatedTotal(ReportGenericAggregatedRequest):
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

    def increment_result(self, row: GracyAggregatedRequest) -> None:
        self.total_requests += row.total_requests
        self.resp_2xx += row.resp_2xx
        self.resp_3xx += row.resp_3xx
        self.resp_4xx += row.resp_4xx
        self.resp_5xx += row.resp_5xx
        self.reqs_aborted += row.reqs_aborted
        self.throttles += row.throttles
        self.retries += row.retries
        self.replays += row.replays

        self.all_avg_latency.append(row.avg_latency)
        if row.req_rate_per_sec > 0:
            self.all_req_rates.append(row.req_rate_per_sec)


class GracyReport:
    def __init__(self, replay_settings: GracyReplay | None) -> None:
        self.requests: list[GracyAggregatedRequest | GracyAggregatedTotal] = []
        self.total = GracyAggregatedTotal(
            "TOTAL",  # serves as title
            total_requests=0,
            resp_2xx=0,
            resp_3xx=0,
            resp_4xx=0,
            resp_5xx=0,
            reqs_aborted=0,
            retries=0,
            throttles=0,
            replays=0,
            max_latency=0,
        )
        self.replay_settings = replay_settings

    def add_request(self, request: GracyAggregatedRequest) -> None:
        self.requests.append(request)
        self.total.increment_result(request)
