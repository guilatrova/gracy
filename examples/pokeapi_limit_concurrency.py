from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from http import HTTPStatus

from rich import print
from rich.logging import RichHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)

from gracy import (  # noqa: E402
    BaseEndpoint,
    ConcurrentRequestLimit,
    GracefulRetry,
    Gracy,
    GracyConfig,
    LogEvent,
    LogLevel,
    graceful,
)

CONCURRENCY = (
    ConcurrentRequestLimit(
        2,
        limit_per_uurl=False,
        log_limit_reached=LogEvent(
            LogLevel.ERROR,
            custom_message="{URL} hit {CONCURRENT_REQUESTS} ongoing concurrent request",
        ),
        log_limit_freed=LogEvent(LogLevel.INFO, "{URL} is free to request"),
    ),
)

RETRY = GracefulRetry(
    delay=0,  # Force throttling to work
    max_attempts=3,
    retry_on=None,
    log_after=LogEvent(LogLevel.INFO),
    log_exhausted=LogEvent(LogLevel.ERROR),
    behavior="pass",
)


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"
    GET_GENERATION = "/generation/{ID}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            strict_status_code={HTTPStatus.OK},
            retry=RETRY,
            concurrent_requests=CONCURRENCY,
            parser={
                "default": lambda r: r.json(),
                HTTPStatus.NOT_FOUND: None,
            },
        )

    @graceful(
        parser={"default": lambda r: r.json()["order"], HTTPStatus.NOT_FOUND: None}
    )
    async def get_pokemon(self, name: str):
        await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    async def get_generation(self, gen: int):
        return await self.get(PokeApiEndpoint.GET_GENERATION, {"ID": str(gen)})

    @graceful(parser={"default": lambda r: r})
    async def slow_req(self, s: int):
        await self.get("https://httpbin.org/delay/{DELAY}", dict(DELAY=str(s)))


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

    try:
        start = time.time()

        pokemon_reqs = [
            asyncio.create_task(pokeapi.get_pokemon(name))
            for name in pokemon_names[:10]
        ]

        slow_reqs = [asyncio.create_task(pokeapi.slow_req(s)) for s in range(3)]

        pokemon_reqs += [
            asyncio.create_task(pokeapi.get_pokemon(name))
            for name in pokemon_names[10:20]
        ]

        slow_reqs += [asyncio.create_task(pokeapi.slow_req(s)) for s in range(3)]

        pokemon_reqs += [
            asyncio.create_task(pokeapi.get_pokemon(name))
            for name in pokemon_names[20:]
        ]

        gen_reqs = [
            asyncio.create_task(pokeapi.get_generation(gen)) for gen in range(1, 4)
        ]

        await asyncio.gather(*pokemon_reqs, *gen_reqs, *slow_reqs)

        await pokeapi.get_pokemon("hitmonchan")

        elapsed = time.time() - start
        print(f"All requests took {timedelta(seconds=elapsed)}s to finish")

    finally:
        plotly = pokeapi.report_status("plotly")
        plotly.show()


asyncio.run(main())
