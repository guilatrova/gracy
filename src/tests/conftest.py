import typing as t

from gracy import Gracy, GracyReplay, SQLiteReplayStorage

Endpoint = t.TypeVar("Endpoint", bound=str)

MISSING_NAME: t.Final = "doesnt-exist"
"""Should match what we recorded previously to successfully replay"""

PRESENT_NAME: t.Final = "charmander"
"""Should match what we recorded previously to successfully replay"""

REPLAY: t.Final = GracyReplay("replay", SQLiteReplayStorage("pokeapi.sqlite3"))


def assert_one_request_made(gracy_api: Gracy[Endpoint]):
    report = gracy_api.get_report()
    assert len(report.requests) == 1


def assert_requests_made(gracy_api: Gracy[Endpoint], total_requests: int, endpoints_count: int = 1):
    report = gracy_api.get_report()

    assert len(report.requests) == endpoints_count
    assert report.requests[0].total_requests == total_requests
