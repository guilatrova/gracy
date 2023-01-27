from http import HTTPStatus

from gracy.models import DEFAULT_CONFIG, BaseEndpoint
from gracy.sync.core import Gracy, graceful


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"

    @graceful(allowed_status_code={HTTPStatus.NOT_FOUND})
    def get_pokemon(self, name: str):
        return self._get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


pokeapi = GracefulPokeAPI()

print(pokeapi.get_pokemon("pikachu"))
print(pokeapi.get_pokemon("invent"))
