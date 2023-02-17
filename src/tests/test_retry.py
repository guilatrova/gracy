import pytest
import typing as t
from http import HTTPStatus

from gracy import BaseEndpoint, GracefulRetry, Gracy, GracyConfig, GracyReplay, SQLiteReplayStorage, graceful

MISSING_NAME: t.Final = "doesnt-exist"
"""Should match what we recorded previously to successfully replay"""
REPLAY = GracyReplay("replay", SQLiteReplayStorage("pokeapi.sqlite3"))
RETRY = GracefulRetry(delay=0.1, max_attempts=0, retry_on=HTTPStatus.NOT_FOUND, behavior="pass")
"""NOTE: Max attempts will be patched later in fixture"""


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            retry=RETRY,
            strict_status_code={HTTPStatus.NOT_FOUND},
            parser={HTTPStatus.NOT_FOUND: None},
        )

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(strict_status_code={HTTPStatus.OK})
    async def get_pokemon_with_strict_status(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


@pytest.fixture()
def make_pokeapi():
    def factory(max_attempts: int):
        Gracy.dangerously_reset_report()

        api = GracefulPokeAPI(REPLAY)
        api._base_config.retry.max_attempts = max_attempts  # type: ignore

        return api

    return factory


async def test_ensure_replay_is_enabled(make_pokeapi: t.Callable[[int], GracefulPokeAPI]):
    pokeapi = make_pokeapi(0)
    result = await pokeapi.get_pokemon(MISSING_NAME)
    report = pokeapi.get_report()

    assert result is None
    assert report.replay_settings is not None
    assert report.replay_settings.mode == "replay"
    assert len(report.requests) == 1
    assert report.requests[0].total_requests == 1


@pytest.mark.parametrize("max_retries", [2, 4, 6])
async def test_pokemon_not_found(max_retries: int, make_pokeapi: t.Callable[[int], GracefulPokeAPI]):
    EXPECTED_REQS: t.Final = 1 + max_retries  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(max_retries)
    result = await pokeapi.get_pokemon(MISSING_NAME)
    report = pokeapi.get_report()

    assert result is None

    assert len(report.requests) == 1
    assert report.requests[0].total_requests == EXPECTED_REQS


@pytest.mark.parametrize("max_retries", [2, 4, 6])
async def test_pokemon_not_found_with_strict_status(max_retries: int, make_pokeapi: t.Callable[[int], GracefulPokeAPI]):
    EXPECTED_REQS: t.Final = 1 + max_retries  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(max_retries)
    result = await pokeapi.get_pokemon_with_strict_status(MISSING_NAME)
    report = pokeapi.get_report()

    assert result is None

    assert len(report.requests) == 1
    assert report.requests[0].total_requests == EXPECTED_REQS
