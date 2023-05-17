from __future__ import annotations

import asyncio
from http import HTTPStatus

import httpx

from gracy import BaseEndpoint, GracefulRetry, Gracy, GracyReplay, GracyRequestContext, LogEvent, LogLevel, graceful
from gracy.exceptions import GracyUserDefinedException
from gracy.replays.storages.pymongo import MongoCredentials, MongoReplayStorage

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

mongo_container = MongoCredentials(host="localhost", username="root", password="example")
record_mode = GracyReplay("record", MongoReplayStorage(mongo_container))
replay_mode = GracyReplay("replay", MongoReplayStorage(mongo_container))


class PokemonNotFound(GracyUserDefinedException):
    BASE_MESSAGE = "Unable to find a pokemon with the name [{NAME}] at {URL} due to {STATUS} status"

    def _format_message(self, request_context: GracyRequestContext, response: httpx.Response) -> str:
        format_args = self._build_default_args()
        name = request_context.endpoint_args.get("NAME", "Unknown")
        return self.BASE_MESSAGE.format(NAME=name, **format_args)


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"
    GET_GENERATION = "/generation/{ID}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"

    @graceful(
        strict_status_code={HTTPStatus.OK},
        retry=retry,
        log_errors=LogEvent(
            LogLevel.ERROR,
            lambda r: "Request failed with {STATUS}" f" and it was {'' if r.is_redirect else 'NOT'} redirected"
            if r
            else "",
        ),
        parser={
            "default": lambda r: r.json()["name"],
            HTTPStatus.NOT_FOUND: PokemonNotFound,
            HTTPStatus.INTERNAL_SERVER_ERROR: PokemonNotFound,
        },
    )
    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    async def get_generation(self, gen: int):
        return await self.get(PokeApiEndpoint.GET_GENERATION, {"ID": str(gen)})


async def main(replay_mode: GracyReplay):
    pokeapi = GracefulPokeAPI(replay_mode)
    poke_names = {"pikachu", "elekid", "charmander", "blaziken", "hitmonchan"}

    try:
        get_pokemons = [asyncio.create_task(pokeapi.get_pokemon(name)) for name in poke_names]
        get_gens = [asyncio.create_task(pokeapi.get_generation(gen_id)) for gen_id in range(1, 3)]

        await asyncio.gather(*(get_pokemons + get_gens))

    finally:
        pokeapi.report_status("rich")
        print("-" * 100)
        pokeapi.report_status("list")
        print("-" * 100)
        pokeapi.report_status("logger")


asyncio.run(main(replay_mode))
