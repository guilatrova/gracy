from __future__ import annotations

import pytest
import typing as t
from http import HTTPStatus

from gracy import GracefulRetry, Gracy, GracyConfig, GracyNamespace
from tests.conftest import (
    PRESENT_BERRY_NAME,
    PRESENT_POKEMON_NAME,
    REPLAY,
    PokeApiEndpoint,
    assert_muiti_endpoints_requests_made,
)

RETRY: t.Final = GracefulRetry(
    delay=0.001,
    max_attempts=2,
    retry_on={HTTPStatus.NOT_FOUND},
    behavior="break",
)


class PokemonNamespace(GracyNamespace[PokeApiEndpoint]):
    async def get_one(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


class BerryNamespace(GracyNamespace[PokeApiEndpoint]):
    async def get_one(self, name: str):
        return await self.get(PokeApiEndpoint.GET_BERRY, {"NAME": name})


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            retry=RETRY,
            allowed_status_code={HTTPStatus.NOT_FOUND},
            parser={HTTPStatus.NOT_FOUND: None},
        )

    berry: BerryNamespace
    pokemon: PokemonNamespace


@pytest.fixture()
def make_pokeapi():
    def factory():
        Gracy.dangerously_reset_report()
        return GracefulPokeAPI(REPLAY)

    return factory


MAKE_POKEAPI_TYPE = t.Callable[[], GracefulPokeAPI]


async def test_get_from_namespaces(make_pokeapi: MAKE_POKEAPI_TYPE):
    pokeapi = make_pokeapi()

    await pokeapi.pokemon.get_one(PRESENT_POKEMON_NAME)
    await pokeapi.berry.get_one(PRESENT_BERRY_NAME)

    EXPECTED_ENDPOINTS = 2
    EXPECTED_REQUESTS = (1, 1)

    assert_muiti_endpoints_requests_made(
        pokeapi, EXPECTED_ENDPOINTS, *EXPECTED_REQUESTS
    )
