import pytest
import typing as t
from collections import defaultdict
from http import HTTPStatus
from unittest.mock import patch

import httpx

from gracy import GracefulRetry, Gracy, GracyConfig
from gracy._models import GracyRequestContext
from tests.conftest import MISSING_NAME, PRESENT_NAME, REPLAY, PokeApiEndpoint

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
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            retry=RETRY,
            allowed_status_code={HTTPStatus.NOT_FOUND},
            parser={HTTPStatus.NOT_FOUND: None},
        )

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        self.before_count = 0
        self.after_status_counter = defaultdict[HTTPStatus, int](int)
        self.after_aborts = 0
        super().__init__(*args, **kwargs)

    async def before(self, context: GracyRequestContext):
        self.before_count += 1

    async def after(self, context: GracyRequestContext, response_or_exc: httpx.Response | Exception):
        if isinstance(response_or_exc, httpx.Response):
            self.after_status_counter[HTTPStatus(response_or_exc.status_code)] += 1
        else:
            self.after_aborts += 1

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


MAKE_POKEAPI_TYPE = t.Callable[[], GracefulPokeAPI]


async def test_before_hook_counts(make_pokeapi: MAKE_POKEAPI_TYPE):
    pokeapi = make_pokeapi()

    assert pokeapi.before_count == 0
    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_NAME))
    assert pokeapi.before_count == 1
    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_NAME))
    assert pokeapi.before_count == 2


async def test_after_hook_counts_statuses(make_pokeapi: MAKE_POKEAPI_TYPE):
    pokeapi = make_pokeapi()

    assert pokeapi.after_status_counter[HTTPStatus.OK] == 0
    assert pokeapi.after_status_counter[HTTPStatus.NOT_FOUND] == 0

    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_NAME))  # 200
    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_NAME))  # 200
    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=MISSING_NAME))  # 404 + retry 2x
    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=MISSING_NAME))  # 404 + retry 2x

    assert pokeapi.after_status_counter[HTTPStatus.OK] == 2
    assert pokeapi.after_status_counter[HTTPStatus.NOT_FOUND] == 6


async def test_after_hook_counts_aborts():
    Gracy.dangerously_reset_report()
    pokeapi = GracefulPokeAPI()

    class SomeRequestException(Exception):
        pass

    mock: t.Any
    with patch.object(pokeapi, "_client", autospec=True) as mock:
        mock.request.side_effect = SomeRequestException("Request failed")

        with pytest.raises(SomeRequestException):
            await pokeapi.get_pokemon(PRESENT_NAME)

    assert pokeapi.after_status_counter[HTTPStatus.OK] == 0
    assert pokeapi.after_status_counter[HTTPStatus.NOT_FOUND] == 0
    assert pokeapi.after_aborts == 1
