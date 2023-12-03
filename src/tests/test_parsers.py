from __future__ import annotations

import pytest
import typing as t
from http import HTTPStatus

from gracy import Gracy, GracyConfig, graceful, parsed_response
from gracy.exceptions import GracyParseFailed
from tests.conftest import (
    MISSING_NAME,
    PRESENT_POKEMON_NAME,
    REPLAY,
    PokeApiEndpoint,
    assert_one_request_made,
)


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(allowed_status_code=HTTPStatus.NOT_FOUND)

    @parsed_response(t.Dict[str, t.Any])
    @graceful(parser={"default": lambda r: r.json()})
    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(parser={HTTPStatus.NOT_FOUND: lambda r: None})
    async def get_pokemon_not_found_as_none(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


@pytest.fixture()
def make_pokeapi():
    def factory():
        Gracy.dangerously_reset_report()
        return GracefulPokeAPI(REPLAY)

    return factory


async def test_pokemon_ok_json(make_pokeapi: t.Callable[[], GracefulPokeAPI]):
    pokeapi = make_pokeapi()

    result: dict[str, t.Any] = await pokeapi.get_pokemon(PRESENT_POKEMON_NAME)

    assert isinstance(result, dict)
    assert "name" in result
    assert result["name"] == PRESENT_POKEMON_NAME
    assert_one_request_made(pokeapi)


async def test_pokemon_bad_json(make_pokeapi: t.Callable[[], GracefulPokeAPI]):
    pokeapi = make_pokeapi()

    with pytest.raises(GracyParseFailed):
        await pokeapi.get_pokemon(MISSING_NAME)

    assert_one_request_made(pokeapi)


async def test_pokemon_not_found_as_none(make_pokeapi: t.Callable[[], GracefulPokeAPI]):
    pokeapi = make_pokeapi()

    result = await pokeapi.get_pokemon_not_found_as_none(MISSING_NAME)

    assert result is None
    assert_one_request_made(pokeapi)
