from http import HTTPStatus

from gracy.models import BaseEndpoint, GracefulRetry, LogEvent, LogLevel
from gracy.sync.core import Gracy, graceful

retry = GracefulRetry(
    None,
    1,
    1.5,
    3,
    LogEvent(LogLevel.WARNING),
    LogEvent(LogLevel.WARNING),
    LogEvent(LogLevel.CRITICAL),
)


class PokeApiEndpoint(BaseEndpoint):
    GET_POKEMON = "/pokemon/{NAME}"


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:  # type: ignore
        BASE_URL = "https://pokeapi.co/api/v2/"

    @graceful(strict_status_code={HTTPStatus.ACCEPTED}, retry=retry)
    def get_pokemon(self, name: str):
        return self._get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


pokeapi = GracefulPokeAPI()

print(pokeapi.get_pokemon("pikachu"))
print(pokeapi.get_pokemon("invent"))
