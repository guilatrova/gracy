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
            parser={
                "default": lambda r: r.json(),
                HTTPStatus.NOT_FOUND: None,
            },
            concurrent_requests=ConcurrentRequestLimit(
                2,
                limit_per_uurl=False,
                log_limit_reached=LogEvent(
                    LogLevel.ERROR,
                    custom_message="{URL} hit {CONCURRENT_REQUESTS} ongoing concurrent request",
                ),
                log_limit_freed=LogEvent(LogLevel.INFO, "{URL} is free to request"),
            ),
        )

    @graceful(
        parser={"default": lambda r: r.json()["order"], HTTPStatus.NOT_FOUND: None}
    )
    async def get_pokemon(self, name: str):
        if name == "slow":
            await self.slow_req()
            print("\n\n")
            return

        await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})
        print("\n\n")

    async def get_generation(self, gen: int):
        return await self.get(PokeApiEndpoint.GET_GENERATION, {"ID": str(gen)})

    @graceful(parser={"default": lambda r: r})
    async def slow_req(self):
        await self.get("https://httpbin.org/delay/3")


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
        "slow",
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
        "slow",
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
