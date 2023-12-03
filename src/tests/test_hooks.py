from __future__ import annotations

import httpx
import pytest
import typing as t
from http import HTTPStatus
from unittest.mock import patch

from gracy import (
    GracefulRetry,
    GracefulRetryState,
    Gracy,
    GracyConfig,
    GracyRequestContext,
)
from gracy.exceptions import GracyRequestFailed
from tests.conftest import (
    MISSING_NAME,
    PRESENT_POKEMON_NAME,
    REPLAY,
    PokeApiEndpoint,
    assert_requests_made,
)

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

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        self.before_count = 0

        self.after_status_counter = t.DefaultDict[int, int](int)
        self.after_aborts = 0
        self.after_retries_counter = 0

        super().__init__(*args, **kwargs)

    async def before(self, context: GracyRequestContext):
        self.before_count += 1

    async def after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
        retry_state: GracefulRetryState | None,
    ):
        if retry_state:
            self.after_retries_counter += 1

        if isinstance(response_or_exc, httpx.Response):
            self.after_status_counter[response_or_exc.status_code] += 1
        else:
            self.after_aborts += 1

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


class GracefulPokeAPIWithRequestHooks(GracefulPokeAPI):
    async def before(self, context: GracyRequestContext):
        await super().before(context)
        # This shouldn't re-trigger any hook!
        await self.get_pokemon(PRESENT_POKEMON_NAME)

    async def after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
        retry_state: GracefulRetryState | None,
    ):
        await super().after(context, response_or_exc, retry_state)
        # This shouldn't re-trigger any hook!
        await self.get_pokemon(PRESENT_POKEMON_NAME)


MAKE_POKEAPI_TYPE = t.Callable[[], GracefulPokeAPI]


async def test_before_hook_counts(make_pokeapi: MAKE_POKEAPI_TYPE):
    pokeapi = make_pokeapi()

    assert pokeapi.before_count == 0
    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_POKEMON_NAME))
    assert pokeapi.before_count == 1
    await pokeapi.get(PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_POKEMON_NAME))
    assert pokeapi.before_count == 2


async def test_after_hook_counts_statuses(make_pokeapi: MAKE_POKEAPI_TYPE):
    pokeapi = make_pokeapi()

    assert pokeapi.after_status_counter[HTTPStatus.OK] == 0
    assert pokeapi.after_status_counter[HTTPStatus.NOT_FOUND] == 0

    await pokeapi.get(
        PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_POKEMON_NAME)
    )  # 200
    await pokeapi.get(
        PokeApiEndpoint.GET_POKEMON, dict(NAME=PRESENT_POKEMON_NAME)
    )  # 200
    await pokeapi.get(
        PokeApiEndpoint.GET_POKEMON, dict(NAME=MISSING_NAME)
    )  # 404 + retry 2x
    await pokeapi.get(
        PokeApiEndpoint.GET_POKEMON, dict(NAME=MISSING_NAME)
    )  # 404 + retry 2x

    assert pokeapi.after_status_counter[HTTPStatus.OK] == 2
    assert pokeapi.after_status_counter[HTTPStatus.NOT_FOUND] == 6
    assert pokeapi.after_retries_counter == 4


async def test_after_hook_counts_aborts():
    Gracy.dangerously_reset_report()
    pokeapi = GracefulPokeAPI()

    class SomeRequestException(Exception):
        pass

    mock: t.Any
    with patch.object(pokeapi, "_client", autospec=True) as mock:
        mock.request.side_effect = SomeRequestException("Request failed")

        with pytest.raises(GracyRequestFailed):
            await pokeapi.get_pokemon(PRESENT_POKEMON_NAME)

    assert pokeapi.after_status_counter[HTTPStatus.OK] == 0
    assert pokeapi.after_status_counter[HTTPStatus.NOT_FOUND] == 0
    assert pokeapi.after_retries_counter == 0
    assert pokeapi.after_aborts == 1


async def test_hook_has_no_recursion():
    Gracy.dangerously_reset_report()
    pokeapi = GracefulPokeAPIWithRequestHooks(REPLAY)

    EXPECTED_REQS: t.Final = 1 + 2  # This + Before hook + After hook
    await pokeapi.get_pokemon(PRESENT_POKEMON_NAME)

    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_hook_with_retries_has_no_recursion():
    Gracy.dangerously_reset_report()
    pokeapi = GracefulPokeAPIWithRequestHooks(REPLAY)

    # (1 This + 2 Retries) + 2 hooks for each (3)
    EXPECTED_REQS: t.Final = (1 + 2) + (2 * 3)
    await pokeapi.get_pokemon(MISSING_NAME)

    assert pokeapi.before_count == 3
    assert pokeapi.after_status_counter[HTTPStatus.NOT_FOUND] == 3
    assert pokeapi.after_retries_counter == 2
    assert_requests_made(pokeapi, EXPECTED_REQS)
