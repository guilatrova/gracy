from __future__ import annotations

import httpx
import pytest
import typing as t
from http import HTTPStatus

from gracy import GracefulValidator, Gracy, graceful, parsed_response
from gracy.exceptions import NonOkResponse, UnexpectedResponse
from tests.conftest import (
    MISSING_NAME,
    PRESENT_POKEMON_NAME,
    REPLAY,
    PokeApiEndpoint,
    assert_one_request_made,
)


class CustomValidator(GracefulValidator):
    def check(self, response: httpx.Response) -> None:
        if response.json()["order"] != 47:
            raise ValueError("Pokemon #order should be 47")  # noqa: TRY003


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(strict_status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
    async def get_pokemon_with_wrong_strict_status(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @parsed_response(httpx.Response)
    @graceful(strict_status_code=HTTPStatus.OK)
    async def get_pokemon_with_correct_strict_status(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @parsed_response(httpx.Response)
    @graceful(allowed_status_code=HTTPStatus.NOT_FOUND)
    async def get_pokemon_allow_404(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


@pytest.fixture()
def make_pokeapi():
    def factory():
        Gracy.dangerously_reset_report()
        return GracefulPokeAPI(REPLAY)

    return factory


async def test_pokemon_ok_default(make_pokeapi: t.Callable[[], GracefulPokeAPI]):
    pokeapi = make_pokeapi()

    result = t.cast(httpx.Response, await pokeapi.get_pokemon(PRESENT_POKEMON_NAME))

    assert result.status_code == HTTPStatus.OK

    assert_one_request_made(pokeapi)


async def test_pokemon_not_found_default(make_pokeapi: t.Callable[[], GracefulPokeAPI]):
    pokeapi = make_pokeapi()

    try:
        _ = await pokeapi.get_pokemon(MISSING_NAME)

    except NonOkResponse as ex:
        assert ex.response.status_code == HTTPStatus.NOT_FOUND

    else:
        pytest.fail("NonOkResponse was expected")

    assert_one_request_made(pokeapi)


async def test_pokemon_strict_status_fail(
    make_pokeapi: t.Callable[[], GracefulPokeAPI]
):
    pokeapi = make_pokeapi()

    try:
        _ = await pokeapi.get_pokemon_with_wrong_strict_status(PRESENT_POKEMON_NAME)

    except UnexpectedResponse as ex:
        assert ex.response.status_code == HTTPStatus.OK

    else:
        pytest.fail("UnexpectedResponse was expected")

    assert_one_request_made(pokeapi)


async def test_pokemon_strict_status_success(
    make_pokeapi: t.Callable[[], GracefulPokeAPI]
):
    pokeapi = make_pokeapi()

    result = await pokeapi.get_pokemon_with_correct_strict_status(PRESENT_POKEMON_NAME)

    assert result.status_code == HTTPStatus.OK
    assert_one_request_made(pokeapi)


async def test_pokemon_allow_404(make_pokeapi: t.Callable[[], GracefulPokeAPI]):
    pokeapi = make_pokeapi()

    result = await pokeapi.get_pokemon_allow_404(MISSING_NAME)

    assert result.status_code == HTTPStatus.NOT_FOUND
    assert_one_request_made(pokeapi)
