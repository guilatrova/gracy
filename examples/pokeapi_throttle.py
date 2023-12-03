from __future__ import annotations

import asyncio
import time
import typing as t
from datetime import timedelta
from http import HTTPStatus

from gracy import (
    BaseEndpoint,
    GracefulRetry,
    GracefulThrottle,
    Gracy,
    GracyConfig,
    LogEvent,
    LogLevel,
    ThrottleRule,
    graceful,
)
from rich import print

RETRY = GracefulRetry(
    delay=0,  # Force throttling to work
    max_attempts=3,
    retry_on=None,
    log_after=LogEvent(LogLevel.WARNING),
    log_exhausted=LogEvent(LogLevel.CRITICAL),
    behavior="pass",
)

THROTTLE_RULE = ThrottleRule(r".*", 4, timedelta(seconds=2))


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"
    GET_GENERATION = "/generation/{ID}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            strict_status_code={HTTPStatus.OK},
            retry=RETRY,
            parser={
                "default": lambda r: r.json(),
                HTTPStatus.NOT_FOUND: None,
            },
            throttling=GracefulThrottle(
                rules=THROTTLE_RULE,
                log_limit_reached=LogEvent(LogLevel.ERROR),
                log_wait_over=LogEvent(LogLevel.WARNING),
            ),
        )

    @graceful(
        parser={"default": lambda r: r.json()["order"], HTTPStatus.NOT_FOUND: None}
    )
    async def get_pokemon(self, name: str):
        val = t.cast(
            t.Optional[str], await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})
        )

        if val:
            print(f"{name} is #{val} in the pokedex")
        else:
            print(f"{name} was not found")

    async def get_generation(self, gen: int):
        return await self.get(PokeApiEndpoint.GET_GENERATION, {"ID": str(gen)})


pokeapi = GracefulPokeAPI()


async def main():
    pokemon_names = [
        "bulbasaur",
        "charmander",
        "squirtle",
        "pikachu",
        "jigglypuff",
        "mewtwo",
        "gyarados",
        "dragonite",
        "mew",
        "chikorita",
        "cyndaquil",
        "totodile",
        "pichu",
        "togepi",
        "ampharos",
        "typhlosion",
        "feraligatr",
        "espeon",
        "umbreon",
        "lugia",
        "ho-oh",
        "treecko",
        "torchic",
        "mudkip",
        "gardevoir",
        "sceptile",
        "blaziken",
        "swampert",
        "rayquaza",
        "latias",
        "latios",
        "lucario",
        "garchomp",
        "darkrai",
        "giratina",  # (1) this fails, so good to test retry
        "arceus",
        "snivy",
        "tepig",
        "oshawott",
        "zekrom",
        "reshiram",
        "victini",
        "chespin",
        "fennekin",
        "froakie",
        "xerneas",
        "yveltal",
        "zygarde",  # (2) this fails, so good to test retry
        "decidueye",
        "incineroar",
    ]
    # pokemon_names = pokemon_names[:10]
    print(
        f"Will query {len(pokemon_names)} pokemons concurrently - {str(THROTTLE_RULE)}"
    )

    try:
        start = time.time()

        pokemon_reqs = [
            asyncio.create_task(pokeapi.get_pokemon(name)) for name in pokemon_names
        ]
        gen_reqs = [
            asyncio.create_task(pokeapi.get_generation(gen)) for gen in range(1, 4)
        ]

        await asyncio.gather(*pokemon_reqs, *gen_reqs)
        elapsed = time.time() - start
        print(f"All requests took {timedelta(seconds=elapsed)}s to finish")

    finally:
        pokeapi.report_status("rich")
        pokeapi.report_status("list")
        pokeapi._throttle_controller.debug_print()  # type: ignore


asyncio.run(main())
