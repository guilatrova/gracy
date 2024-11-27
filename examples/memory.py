from __future__ import annotations

import httpx
from dataclasses import dataclass
from time import sleep

from gracy import (
    BaseEndpoint,
    Gracy,
    GracyRequestContext,
)
from gracy.exceptions import GracyUserDefinedException


class PokemonNotFound(GracyUserDefinedException):
    BASE_MESSAGE = "Unable to find a pokemon with the name [{NAME}] at {URL} due to {STATUS} status"

    def _format_message(
        self, request_context: GracyRequestContext, response: httpx.Response
    ) -> str:
        format_args = self._build_default_args()
        name = request_context.endpoint_args.get("NAME", "Unknown")
        return self.BASE_MESSAGE.format(NAME=name, **format_args)


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"
    GET_GENERATION = "/generation/{ID}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"

    pass


@dataclass
class Test:
    pass


def main():
    while True:
        GracefulPokeAPI()
        sleep(1)


if __name__ == "__main__":
    main()
