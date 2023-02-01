import asyncio
from http import HTTPStatus

import httpx

from gracy import BaseEndpoint, GracefulRetry, Gracy, GracyRequestContext, LogEvent, LogLevel, graceful
from gracy.exceptions import GracyUserDefinedException

retry = GracefulRetry(
    delay=1,
    max_attempts=3,
    delay_modifier=1.5,
    retry_on=None,
    log_before=LogEvent(LogLevel.WARNING),
    log_after=LogEvent(LogLevel.WARNING),
    log_exhausted=LogEvent(LogLevel.CRITICAL),
    behavior="pass",
)


class PokemonNotFound(GracyUserDefinedException):
    BASE_MESSAGE = "Unable to find a pokemon with the name [{NAME}] at {URL} due to {STATUS} status"

    def _format_message(self, request_context: GracyRequestContext, response: httpx.Response) -> str:
        format_args = self._build_default_args()
        name = request_context.endpoint_args.get("NAME", "Unknown")
        return self.BASE_MESSAGE.format(NAME=name, **format_args)


class ServerIsOutError(Exception):
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
            HTTPStatus.NOT_FOUND: PokemonNotFound,
            HTTPStatus.INTERNAL_SERVER_ERROR: ServerIsOutError,
        },
    )
    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


pokeapi = GracefulPokeAPI()
pokeapi_two = GracefulPokeAPI()


async def main():
    try:
        p1: str | None = await pokeapi.get_pokemon("pikachu")
        p2: str | None = await pokeapi_two.get_pokemon("doesnt-exist")

        print("P1: result of get_pokemon:", p1)
        print("P2: result of get_pokemon:", p2)

    finally:
        pokeapi.report_status()


asyncio.run(main())
