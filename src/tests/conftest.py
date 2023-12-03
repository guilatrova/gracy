from __future__ import annotations

import typing as t

import httpx

from gracy import BaseEndpoint, Gracy, GracyReplay
from gracy.replays.storages.sqlite import SQLiteReplayStorage

MISSING_NAME: t.Final = "doesnt-exist"
"""Should match what we recorded previously to successfully replay"""

PRESENT_POKEMON_NAME: t.Final = "charmander"
"""Should match what we recorded previously to successfully replay"""

PRESENT_BERRY_NAME: t.Final = "cheri"
"""Should match what we recorded previously to successfully replay"""

REPLAY: t.Final = GracyReplay("replay", SQLiteReplayStorage("pokeapi.sqlite3"))


class FakeReplayStorage(SQLiteReplayStorage):
    """Completely ignores the request defined to return a response matching the urls in the order specified"""

    def __init__(self, force_urls: t.List[str]) -> None:
        self._force_urls = force_urls
        self._response_idx = 0
        super().__init__("pokeapi.sqlite3")

    def _find_record(self, request: httpx.Request):
        cur = self._con.cursor()
        url = self._force_urls[self._response_idx]
        self._response_idx += 1

        cur.execute(
            """
            SELECT response, updated_at FROM gracy_recordings
            WHERE
            url = ?""",
            (url,),
        )

        return cur.fetchone()


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"
    GET_BERRY = "/berry/{NAME}"


def assert_one_request_made(gracy_api: Gracy[PokeApiEndpoint]):
    report = gracy_api.get_report()
    assert len(report.requests) == 1


def assert_requests_made(
    gracy_api: Gracy[PokeApiEndpoint], total_requests: int, endpoints_count: int = 1
):
    report = gracy_api.get_report()

    assert len(report.requests) == endpoints_count
    assert report.requests[0].total_requests == total_requests


def assert_muiti_endpoints_requests_made(
    gracy_api: Gracy[PokeApiEndpoint],
    endpoints_count: int,
    *total_requests: int,
):
    report = gracy_api.get_report()

    assert len(report.requests) == endpoints_count

    for i, expected_total in enumerate(total_requests):
        assert report.requests[i].total_requests == expected_total
