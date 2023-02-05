import re
from collections import defaultdict
from http import HTTPStatus
from statistics import mean
from typing import Final, List, Literal, Set

import httpx

from .._models import GracyRequestContext, ThrottleController
from ._models import GracyAggregatedRequest, GracyReport, GracyRequestResult

ANY_REGEX: Final = r".+"

REQUEST_SUM_KEY = HTTPStatus | Literal["total"]
REQUEST_SUM_PER_STATUS_TYPE = dict[str, defaultdict[REQUEST_SUM_KEY, int]]


class ReportBuilder:
    def __init__(self) -> None:
        self._results: List[GracyRequestResult] = []

    def track(self, request_context: GracyRequestContext, response: httpx.Response):
        self._results.append(GracyRequestResult(request_context.unformatted_url, response))

    def _calculate_req_rate_for_url(self, unformatted_url: str, throttle_controller: ThrottleController) -> float:
        pattern = re.compile(re.sub(r"{(\w+)}", ANY_REGEX, unformatted_url))
        rate = throttle_controller.calculate_requests_rate(pattern)
        return rate

    def build(self, throttle_controller: ThrottleController) -> GracyReport:
        requests_by_uurl = defaultdict[str, Set[httpx.Response]](set)
        requests_sum: REQUEST_SUM_PER_STATUS_TYPE = defaultdict(lambda: defaultdict(int))

        for result in self._results:
            requests_by_uurl[result.uurl].add(result.response)
            requests_sum[result.uurl]["total"] += 1
            requests_sum[result.uurl][HTTPStatus(result.response.status_code)] += 1

        requests_sum = dict(sorted(requests_sum.items(), key=lambda item: item[1]["total"], reverse=True))

        report = GracyReport()

        for uurl, data in requests_sum.items():
            all_requests = requests_by_uurl[uurl]

            total_requests = data["total"]
            url_latency = [r.elapsed.total_seconds() for r in all_requests]

            # Rate
            # Use min to handle scenarios like:
            # 10 reqs in a 2 millisecond window would produce a number >1,000 leading the user to think that we're
            # producing 1,000 requests which isn't true.
            rate = min(self._calculate_req_rate_for_url(uurl, throttle_controller), total_requests)

            report_request = GracyAggregatedRequest(
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

        return report
