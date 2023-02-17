import asyncio
from datetime import timedelta
from http import HTTPStatus
from typing import cast

from rich import print

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

RETRY = GracefulRetry(
    delay=1,
    max_attempts=3,
    delay_modifier=1.5,
    retry_on=None,
    log_before=LogEvent(LogLevel.WARNING),
    log_after=LogEvent(LogLevel.WARNING),
    log_exhausted=LogEvent(LogLevel.CRITICAL),
    behavior="pass",
)

THROTTLE_RULE = ThrottleRule(r".*", 3, timedelta(seconds=2))


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"
    GET_GENERATION = "/generation/{ID}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            strict_status_code={HTTPStatus.OK},
            log_errors=LogEvent(
                LogLevel.ERROR, "How can I become a pokemon master if {URL} keeps failing with {STATUS}"
            ),
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

    @graceful(parser={"default": lambda r: r.json()["order"], HTTPStatus.NOT_FOUND: None})
    async def get_pokemon(self, name: str):
        val = cast(str | None, await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name}))

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
        "giratina",
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
        "zygarde",
        "decidueye",
        "incineroar",
    ]
    # pokemon_names = pokemon_names[:10]
    print(f"Will query {len(pokemon_names)} pokemons concurrently - {str(THROTTLE_RULE)}")

    try:
        pokemon_reqs = [asyncio.create_task(pokeapi.get_pokemon(name)) for name in pokemon_names]
        gen_reqs = [asyncio.create_task(pokeapi.get_generation(gen)) for gen in range(1, 4)]

        await asyncio.gather(*pokemon_reqs, *gen_reqs)

    finally:
        pokeapi.report_status("rich")
        pokeapi._throttle_controller.debug_print()  # type: ignore


asyncio.run(main())
