from __future__ import annotations

import pytest
import typing as t

from gracy import Gracy, graceful_generator
from tests.conftest import REPLAY, PokeApiEndpoint


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"

    @graceful_generator(parser={"default": lambda r: r.json()})
    async def get_2_yield_graceful(self):
        names = ["charmander", "pikachu"]

        for name in names:
            r = await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})
            yield r


@pytest.fixture()
def make_pokeapi():
    def factory():
        Gracy.dangerously_reset_report()
        return GracefulPokeAPI(REPLAY)

    return factory


async def test_pokemon_ok_json(make_pokeapi: t.Callable[[], GracefulPokeAPI]):
    pokeapi = make_pokeapi()
    count = 0

    async for _ in pokeapi.get_2_yield_graceful():
        count += 1

    assert count == 2
