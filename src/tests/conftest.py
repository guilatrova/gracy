import typing as t

from gracy import Gracy

Endpoint = t.TypeVar("Endpoint", bound=str)


def assert_one_request_made(gracy_api: Gracy[Endpoint]):
    report = gracy_api.get_report()
    assert len(report.requests) == 1


def assert_requests_made(gracy_api: Gracy[Endpoint], total_requests: int, endpoints_count: int = 1):
    report = gracy_api.get_report()

    assert len(report.requests) == endpoints_count
    assert report.requests[0].total_requests == total_requests
