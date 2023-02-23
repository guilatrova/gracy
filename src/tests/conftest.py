import typing as t

from gracy import BaseEndpoint, Gracy, GracyReplay
from gracy.replays.storages.sqlite import SQLiteReplayStorage

MISSING_NAME: t.Final = "doesnt-exist"
"""Should match what we recorded previously to successfully replay"""

PRESENT_NAME: t.Final = "charmander"
"""Should match what we recorded previously to successfully replay"""

REPLAY: t.Final = GracyReplay("replay", SQLiteReplayStorage("pokeapi.sqlite3"))


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


def assert_one_request_made(gracy_api: Gracy[PokeApiEndpoint]):
    report = gracy_api.get_report()
    assert len(report.requests) == 1


def assert_requests_made(gracy_api: Gracy[PokeApiEndpoint], total_requests: int, endpoints_count: int = 1):
    report = gracy_api.get_report()

    assert len(report.requests) == endpoints_count
    assert report.requests[0].total_requests == total_requests
