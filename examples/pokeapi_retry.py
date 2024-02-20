from __future__ import annotations

import asyncio
from http import HTTPStatus

from gracy import (
    BaseEndpoint,
    GracefulRetry,
    Gracy,
    GracyReplay,
    LogEvent,
    LogLevel,
    graceful,
)
from gracy.replays.storages.sqlite import SQLiteReplayStorage

retry = GracefulRetry(
    delay=1,
    max_attempts=3,
    delay_modifier=1.2,
    retry_on=None,
    log_before=LogEvent(LogLevel.WARNING),
    log_after=LogEvent(LogLevel.WARNING),
    log_exhausted=LogEvent(LogLevel.CRITICAL),
    behavior="pass",
)


class ServerIsOutError(Exception):
    pass


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"

    @graceful(
        strict_status_code={HTTPStatus.OK},
        retry=retry,
        log_errors=LogEvent(LogLevel.ERROR),
        parser={
            "default": lambda r: r.json()["name"],
            HTTPStatus.NOT_FOUND: None,
            HTTPStatus.INTERNAL_SERVER_ERROR: ServerIsOutError,
        },
    )
    async def get_pokemon(self, name: str):
        return await self.get[str](PokeApiEndpoint.GET_POKEMON, {"NAME": name})


record = GracyReplay("record", SQLiteReplayStorage("pokeapi.sqlite3"))
pokeapi = GracefulPokeAPI(record)


async def main():
    try:
        p1: str | None = await pokeapi.get_pokemon("pikachu")  # 1 req = 200
        print("P1: result of get_pokemon:", p1)

        p2: str | None = await pokeapi.get_pokemon("doesnt-exist")  # 1+3 req = 404
        print("P2: result of get_pokemon:", p2)

    finally:
        pokeapi.report_status("rich")


asyncio.run(main())
