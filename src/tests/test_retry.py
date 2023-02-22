import pytest
import typing as t
from http import HTTPStatus

import httpx

from gracy import (
    BaseEndpoint,
    GracefulRetry,
    GracefulValidator,
    Gracy,
    GracyConfig,
    GracyReplay,
    SQLiteReplayStorage,
    graceful,
)
from gracy.exceptions import NonOkResponse
from tests.conftest import assert_requests_made

MISSING_NAME: t.Final = "doesnt-exist"
"""Should match what we recorded previously to successfully replay"""
PRESENT_NAME: t.Final = "charmander"

REPLAY: t.Final = GracyReplay("replay", SQLiteReplayStorage("pokeapi.sqlite3"))


class CustomValidator(GracefulValidator):
    def check(self, response: httpx.Response) -> None:
        if response.json()["order"] != 47:
            raise ValueError("Pokemon #order should be 47")  # noqa: TC003


RETRY: t.Final = GracefulRetry(delay=0.1, max_attempts=0, retry_on={HTTPStatus.NOT_FOUND, ValueError}, behavior="pass")
"""NOTE: Max attempts will be patched later in fixture"""


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            retry=RETRY,
            allowed_status_code={HTTPStatus.NOT_FOUND},
            parser={HTTPStatus.NOT_FOUND: None},
        )

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(strict_status_code={HTTPStatus.OK})
    async def get_pokemon_with_strict_status(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(allowed_status_code=None, parser={"default": lambda r: r.json()})
    async def get_pokemon_without_allowed_status(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(validators=CustomValidator())
    async def get_pokemon_with_custom_validator(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


@pytest.fixture()
def make_pokeapi():
    def factory(max_attempts: int, break_or_pass: str = "pass"):
        Gracy.dangerously_reset_report()

        api = GracefulPokeAPI(REPLAY)
        api._base_config.retry.max_attempts = max_attempts  # type: ignore
        api._base_config.retry.behavior = break_or_pass  # type: ignore

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

    assert result is None
    assert_requests_made(pokeapi, EXPECTED_REQS)


@pytest.mark.parametrize("max_retries", [2, 4, 6])
async def test_pokemon_not_found_with_strict_status(max_retries: int, make_pokeapi: t.Callable[[int], GracefulPokeAPI]):
    EXPECTED_REQS: t.Final = 1 + max_retries  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(max_retries)
    result = await pokeapi.get_pokemon_with_strict_status(MISSING_NAME)

    assert result is None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_pokemon_with_bad_parser_break_wont_run(make_pokeapi: t.Callable[[int, str], GracefulPokeAPI]):
    MAX_RETRIES: t.Final = 2
    EXPECTED_REQS: t.Final = 1 + MAX_RETRIES  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(MAX_RETRIES, "break")

    with pytest.raises(NonOkResponse):
        await pokeapi.get_pokemon_without_allowed_status(MISSING_NAME)

    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_with_failing_custom_validation(make_pokeapi: t.Callable[[int], GracefulPokeAPI]):
    MAX_RETRIES: t.Final = 2
    EXPECTED_REQS: t.Final = 1 + MAX_RETRIES  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(MAX_RETRIES)
    result = await pokeapi.get_pokemon_with_custom_validator(PRESENT_NAME)

    assert result is not None

    assert_requests_made(pokeapi, EXPECTED_REQS)
