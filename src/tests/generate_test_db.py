from __future__ import annotations

import asyncio
import typing as t

from gracy import BaseEndpoint, Gracy, GracyReplay
from gracy.replays.storages.sqlite import SQLiteReplayStorage


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"
    GET_BERRY = "/berry/{NAME}"
    GET_GENERATION = "/generation/{ID}"


class GracefulPokeAPIRecorder(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"

    def __init__(self) -> None:
        record_mode: t.Final = GracyReplay(
            "record",
            SQLiteReplayStorage("pokeapi.sqlite3"),
        )

        super().__init__(record_mode)

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    async def get_berry(self, name: str):
        return await self.get(PokeApiEndpoint.GET_BERRY, {"NAME": name})

    async def get_generation(self, gen: int):
        return await self.get(PokeApiEndpoint.GET_GENERATION, {"ID": str(gen)})


async def main():
    pokeapi = GracefulPokeAPIRecorder()
    poke_names = {"pikachu", "elekid", "charmander", "blaziken", "hitmonchan"}

    try:
        get_pokemons = [
            asyncio.create_task(pokeapi.get_pokemon(name)) for name in poke_names
        ]
        get_gens = [
            asyncio.create_task(pokeapi.get_generation(gen_id))
            for gen_id in range(1, 3)
        ]
        get_berries = [asyncio.create_task(pokeapi.get_berry("cheri"))]

        await asyncio.gather(*(get_pokemons + get_gens + get_berries))

    finally:
        pokeapi.report_status("rich")


if __name__ == "__main__":
    asyncio.run(main())
