from __future__ import annotations

import pytest
import typing as t
from http import HTTPStatus

from gracy import GracefulRetry, Gracy, GracyConfig
from tests.conftest import PRESENT_POKEMON_NAME, REPLAY, PokeApiEndpoint

RETRY: t.Final = GracefulRetry(
    delay=0.001,
    max_attempts=2,
    retry_on={HTTPStatus.NOT_FOUND},
    behavior="break",
)


@pytest.fixture()
def make_pokeapi():
    def factory():
        Gracy.dangerously_reset_report()
        return GracefulPokeAPI(REPLAY)

    return factory


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            retry=RETRY,
            allowed_status_code={HTTPStatus.NOT_FOUND},
            parser={HTTPStatus.NOT_FOUND: None},
        )


MAKE_POKEAPI_TYPE = t.Callable[[], GracefulPokeAPI]


async def test_pass_kwargs(make_pokeapi: MAKE_POKEAPI_TYPE):
    pokeapi = make_pokeapi()

    await pokeapi.get(
        PokeApiEndpoint.GET_POKEMON,
        dict(NAME=PRESENT_POKEMON_NAME),
        follow_redirects=True,
    )
