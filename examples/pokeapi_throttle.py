import asyncio
from http import HTTPStatus
from typing import cast

from rich import print

from gracy import BaseEndpoint, GracefulRetry, GracefulThrottle, Gracy, GracyConfig, LogEvent, LogLevel, ThrottleRule

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


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            strict_status_code={HTTPStatus.OK},
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


async def main():
    names = ["blaziken", "pikachu", "lugia", "bulbasaur", "charmander", "venusaur", "charizard", "blastoise", "bad"] * 2
    print(names, len(names))
    try:
        t = [asyncio.create_task(pokeapi.get_pokemon(name)) for name in names]

        await asyncio.gather(*t)

    finally:
        pokeapi.report_status()
        pokeapi._throttle_controller.debug_print()  # type: ignore


asyncio.run(main())
