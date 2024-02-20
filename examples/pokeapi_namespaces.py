from __future__ import annotations

import asyncio
import typing as t
from http import HTTPStatus

from gracy import (
    BaseEndpoint,
    Gracy,
    GracyConfig,
    GracyNamespace,
    LogEvent,
    LogLevel,
)
from rich import print

RESP_TYPE = t.Union[t.Dict[str, t.Any], None]


class PokeApiEndpoint(BaseEndpoint):
    BERRY = "/berry/{KEY}"
    BERRY_FLAVOR = "/berry-flavor/{KEY}"
    BERRY_FIRMNESS = "/berry-firmness/{KEY}"

    POKEMON = "/pokemon/{KEY}"
    POKEMON_COLOR = "/pokemon-color/{KEY}"
    POKEMON_FORM = "/pokemon-form/{KEY}"


class PokeApiBerryNamespace(GracyNamespace[PokeApiEndpoint]):
    async def get_this(self, name_or_id: t.Union[str, int]):
        return await self.get[RESP_TYPE](
            PokeApiEndpoint.BERRY, dict(KEY=str(name_or_id))
        )

    async def get_flavor(self, name_or_id: t.Union[str, int]):
        return await self.get[RESP_TYPE](
            PokeApiEndpoint.BERRY_FLAVOR, dict(KEY=str(name_or_id))
        )

    async def get_firmness(self, name_or_id: t.Union[str, int]):
        return await self.get[RESP_TYPE](
            PokeApiEndpoint.BERRY_FIRMNESS, dict(KEY=str(name_or_id))
        )


class PokeApiPokemonNamespace(GracyNamespace[PokeApiEndpoint]):
    async def get_this(self, name_or_id: t.Union[str, int]):
        return await self.get[RESP_TYPE](
            PokeApiEndpoint.POKEMON, dict(KEY=str(name_or_id))
        )

    async def get_color(self, name_or_id: t.Union[str, int]):
        return await self.get[RESP_TYPE](
            PokeApiEndpoint.POKEMON_COLOR, dict(KEY=str(name_or_id))
        )

    async def get_form(self, name_or_id: t.Union[str, int]):
        return await self.get[RESP_TYPE](
            PokeApiEndpoint.POKEMON_FORM, dict(KEY=str(name_or_id))
        )


class PokeApi(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        REQUEST_TIMEOUT = 5.0
        SETTINGS = GracyConfig(
            parser={
                HTTPStatus.OK: lambda resp: resp.json(),
                HTTPStatus.NOT_FOUND: None,
            },
            allowed_status_code=HTTPStatus.NOT_FOUND,
            log_errors=LogEvent(LogLevel.ERROR),
        )

    berry: PokeApiBerryNamespace
    pokemon: PokeApiPokemonNamespace


async def main():
    api = PokeApi()

    berry = api.berry.get_this("cheri")
    berry_flavor = api.berry.get_flavor("spicy")
    pikachu = api.pokemon.get_this("pikachu")
    black = api.pokemon.get_color("black")

    results = await asyncio.gather(berry, berry_flavor, pikachu, black)

    for content in results:
        print(content)

    api.report_status("rich")


if __name__ == "__main__":
    asyncio.run(main())
