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
)


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
    )
    async def get_pokemon(self, name: str):
        return await self._get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


pokeapi = GracefulPokeAPI()
pokeapifail = GracefulPokeAPI()


async def main():
    try:
        await pokeapi.get_pokemon("pikachu")
        # await pokeapifail.get_pokemon("invent")
    finally:
        pokeapi.report_status()


asyncio.run(main())
