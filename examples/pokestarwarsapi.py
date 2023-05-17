from __future__ import annotations

import asyncio
from http import HTTPStatus

from gracy import BaseEndpoint, Gracy, LogEvent, LogLevel, graceful


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"

    @graceful(
        strict_status_code={HTTPStatus.OK},
        log_request=LogEvent(LogLevel.INFO),
        parser={
            "default": lambda r: r.json()["name"],
            HTTPStatus.NOT_FOUND: None,
        },
    )
    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


class StarWarsAPI(Gracy[str]):
    class Config:  # type: ignore
        BASE_URL = "https://swapi.dev/api/"

    @graceful(
        strict_status_code=HTTPStatus.OK,
        log_request=LogEvent(LogLevel.INFO),
        parser={"default": lambda r: r.json()["name"]},
    )
    async def get_person(self, person_id: int):
        return await self.get("people/{PERSON_ID}", {"PERSON_ID": str(person_id)})


pokeapi = GracefulPokeAPI()
swapi = StarWarsAPI()


async def main():
    try:
        pk: str | None = await pokeapi.get_pokemon("pikachu")
        sw: str = await swapi.get_person(1)

        print("PK: result of get_pokemon:", pk)
        print("SW: result of get_person:", sw)

    finally:
        pokeapi.report_status("rich")


asyncio.run(main())
