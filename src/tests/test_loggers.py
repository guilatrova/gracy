from __future__ import annotations

import httpx
import logging
import pytest
import typing as t

from gracy import GracefulValidator, Gracy, GracyConfig, LogEvent, LogLevel
from gracy.exceptions import NonOkResponse
from tests.conftest import MISSING_NAME, PRESENT_POKEMON_NAME, REPLAY, PokeApiEndpoint


class CustomValidator(GracefulValidator):
    def check(self, response: httpx.Response) -> None:
        if response.json()["order"] != 47:
            raise ValueError("Pokemon #order should be 47")  # noqa: TRY003


def assert_log(record: logging.LogRecord, expected_event: LogEvent):
    assert record.levelno == expected_event.level
    assert record.message == expected_event.custom_message  # No formatting set


# NOTE: captest only captures >=warning
ON_REQUEST: t.Final = LogEvent(LogLevel.WARNING, "LOG_REQUEST")
ON_RESPONSE: t.Final = LogEvent(LogLevel.ERROR, "LOG_RESPONSE")
ON_ERROR: t.Final = LogEvent(LogLevel.CRITICAL, "LOG_ERROR")


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            log_request=ON_REQUEST,
            log_response=ON_RESPONSE,
            log_errors=ON_ERROR,
        )

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


@pytest.fixture()
def pokeapi():
    Gracy.dangerously_reset_report()
    return GracefulPokeAPI(REPLAY)


async def test_pokemon_log_request_response(
    pokeapi: GracefulPokeAPI, caplog: pytest.LogCaptureFixture
):
    await pokeapi.get_pokemon(PRESENT_POKEMON_NAME)

    assert len(caplog.records) == 2
    assert_log(caplog.records[0], ON_REQUEST)
    assert_log(caplog.records[1], ON_RESPONSE)


async def test_pokemon_log_request_response_error(
    pokeapi: GracefulPokeAPI, caplog: pytest.LogCaptureFixture
):
    with pytest.raises(NonOkResponse):
        await pokeapi.get_pokemon(MISSING_NAME)

    assert len(caplog.records) == 3
    assert_log(caplog.records[0], ON_REQUEST)
    assert_log(caplog.records[1], ON_RESPONSE)
    assert_log(caplog.records[2], ON_ERROR)
