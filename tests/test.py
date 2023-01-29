import asyncio
from http import HTTPStatus

from gracy.core import Gracy, graceful
from gracy.models import BaseEndpoint, GracefulRetry, LogEvent, LogLevel

retry = GracefulRetry(
    1,
    3,
    1.5,
    None,
    LogEvent(LogLevel.WARNING),
    LogEvent(LogLevel.WARNING),
    LogEvent(LogLevel.CRITICAL),
    "pass",
)


class PokemonNotFound(Exception):
    pass


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"

    @graceful(
        strict_status_code={HTTPStatus.OK},
        retry=retry,
        log_request=LogEvent(LogLevel.WARNING),
        log_response=LogEvent(LogLevel.ERROR, "How can I become a master pokemon if {URL} keeps failing with {STATUS}"),
        parser={
            "default": lambda r: r.json()["name"],
            HTTPStatus.NOT_FOUND: None,
        },
    )
    async def get_pokemon(self, name: str):
        return await self._get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


pokeapi = GracefulPokeAPI()
pokeapifail = GracefulPokeAPI()


async def main():
    try:
        p1: str | None = await pokeapi.get_pokemon("pikachu")
        p2: str | None = await pokeapifail.get_pokemon("invent")

        print("P1: result of get_pokemon:", p1)
        print("P2: result of get_pokemon:", p2)

    finally:
        pokeapi.report_status()


asyncio.run(main())
