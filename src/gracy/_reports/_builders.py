from __future__ import annotations

import re
import typing as t
from collections import defaultdict
from statistics import mean

import httpx

from .._models import GracyRequestContext, ThrottleController
from ..replays.storages._base import GracyReplay, is_replay
from ._models import GracyAggregatedRequest, GracyReport, GracyRequestCounters, GracyRequestResult

ANY_REGEX: t.Final = r".+"

REQUEST_ERROR_STATUS: t.Final = 0
REQUEST_SUM_KEY = t.Union[int, t.Literal["total", "retries", "throttles", "replays", 0]]
REQUEST_SUM_PER_STATUS_TYPE = t.Dict[str, t.DefaultDict[REQUEST_SUM_KEY, int]]


class ReportBuilder:
    def __init__(self) -> None:
        self._results: t.List[GracyRequestResult] = []
        self._counters = t.DefaultDict[str, GracyRequestCounters](GracyRequestCounters)

    def track(self, request_context: GracyRequestContext, response_or_exc: httpx.Response | Exception):
        self._results.append(GracyRequestResult(request_context.unformatted_url, response_or_exc))
        if isinstance(response_or_exc, httpx.Response) and is_replay(response_or_exc):
            self.replayed(request_context)

    def retried(self, request_context: GracyRequestContext):
        self._counters[request_context.unformatted_url].retries += 1

    def throttled(self, request_context: GracyRequestContext):
        self._counters[request_context.unformatted_url].throttles += 1

    def replayed(self, request_context: GracyRequestContext):
        self._counters[request_context.unformatted_url].replays += 1

    def _calculate_req_rate_for_url(self, unformatted_url: str, throttle_controller: ThrottleController) -> float:
        pattern = re.compile(re.sub(r"{(\w+)}", ANY_REGEX, unformatted_url))
        rate = throttle_controller.calculate_requests_per_sec(pattern)
        return rate

    def build(self, throttle_controller: ThrottleController, replay_settings: GracyReplay | None) -> GracyReport:
        requests_by_uurl = t.DefaultDict[str, t.Set[t.Union[httpx.Response, Exception]]](set)
        requests_sum: REQUEST_SUM_PER_STATUS_TYPE = defaultdict(lambda: defaultdict(int))

        for result in self._results:
            requests_by_uurl[result.uurl].add(result.response)
            requests_sum[result.uurl]["total"] += 1
            if isinstance(result.response, httpx.Response):
                requests_sum[result.uurl][result.response.status_code] += 1
            else:
                requests_sum[result.uurl][REQUEST_ERROR_STATUS] += 1

        for uurl, counters in self._counters.items():
            requests_sum[uurl]["throttles"] = counters.throttles
            requests_sum[uurl]["retries"] = counters.retries
            requests_sum[uurl]["replays"] = counters.replays

        requests_sum = dict(sorted(requests_sum.items(), key=lambda item: item[1]["total"], reverse=True))

        report = GracyReport(replay_settings)

        for uurl, data in requests_sum.items():
            all_requests = {req for req in requests_by_uurl[uurl] if isinstance(req, httpx.Response)}

            total_requests = data["total"]
            url_latency = [r.elapsed.total_seconds() for r in all_requests]

            # Rate
            # Use min to handle scenarios like:
            # 10 reqs in a 2 millisecond window would produce a number >1,000 leading the user to think that we're
            # producing 1,000 requests which isn't true.
            rate = min(self._calculate_req_rate_for_url(uurl, throttle_controller), total_requests)

            resp_2xx = 0
            resp_3xx = 0
            resp_4xx = 0
            resp_5xx = 0
            aborted = 0
            retries = 0
            throttles = 0
            replays = 0

            for maybe_status, count in data.items():
                if maybe_status == "total":
                    continue

                if maybe_status == REQUEST_ERROR_STATUS:
                    aborted += count
                    continue

                if maybe_status == "throttles":
                    throttles += count
                    continue

                if maybe_status == "retries":
                    retries += count
                    continue

                if maybe_status == "replays":
                    replays += count
                    continue

                status = maybe_status
                if 200 <= status < 300:
                    resp_2xx += count
                elif 300 <= status < 400:
                    resp_3xx += count
                elif 400 <= status < 500:
                    resp_4xx += count
                elif 500 <= status:
                    resp_5xx += count

            report_request = GracyAggregatedRequest(
                uurl,
                total_requests,
                # Responses
                resp_2xx=resp_2xx,
                resp_3xx=resp_3xx,
                resp_4xx=resp_4xx,
                resp_5xx=resp_5xx,
                reqs_aborted=aborted,
                retries=retries,
                throttles=throttles,
                replays=replays,
                # General
                avg_latency=mean(url_latency) if url_latency else 0,
                max_latency=max(url_latency) if url_latency else 0,
                req_rate_per_sec=rate,
            )

            report.add_request(report_request)

        return report
