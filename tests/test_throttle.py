import asyncio
from http import HTTPStatus
from typing import cast

from rich import print

from gracy.core import Gracy
from gracy.models import BaseEndpoint, GracefulRetry, GracefulThrottle, GracyConfig, LogEvent, LogLevel, ThrottleRule

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


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            strict_status_code={HTTPStatus.OK},
            log_request=LogEvent(LogLevel.WARNING),
            log_errors=LogEvent(
                LogLevel.ERROR, "How can I become a master pokemon if {URL} keeps failing with {STATUS}"
            ),
            retry=retry,
            parser={
                "default": lambda r: r.json()["order"],
                HTTPStatus.NOT_FOUND: None,
            },
            throttling=GracefulThrottle(
                rules=ThrottleRule(r".*", 2),
                log_limit_reached=LogEvent(LogLevel.ERROR),
                log_wait_over=LogEvent(LogLevel.WARNING),
            ),
        )

    async def get_pokemon(self, name: str):
        val = cast(str | None, await self._get(PokeApiEndpoint.GET_POKEMON, {"NAME": name}))

        if val:
            print(f"{name} is #{val} in the pokedex")
        else:
            print(f"{name} was not found")


pokeapi = GracefulPokeAPI()
pokeapifail = GracefulPokeAPI()


async def main():
    names = ["blaziken", "pikachu", "lugia", "bulbasaur", "charmander", "venusaur", "charizard", "blastoise"]
    try:
        t = [asyncio.create_task(pokeapi.get_pokemon(name)) for name in names]

        await asyncio.gather(*t)

    finally:
        pokeapi.report_status()
        print(pokeapi._throttle_controller._control)


asyncio.run(main())
