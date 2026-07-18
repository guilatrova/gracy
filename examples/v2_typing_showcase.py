"""Typing showcase - what your IDE sees when you use Gracy 2.0.

Run the types:   pyright examples/v2_typing_showcase.py
Run for real:    python examples/v2_typing_showcase.py
"""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Annotated

from typing import TYPE_CHECKING

if not TYPE_CHECKING:  # pyright uses its builtin reveal_type; runtime needs a real one
    try:
        from typing import reveal_type  # 3.11+: prints "Runtime type is ..."
    except ImportError:  # 3.10: no-op so the example still runs

        def reveal_type(obj, /):
            return obj

from pydantic import BaseModel

import gracy
from gracy import (
    Backoff,
    Gracy,
    GracyConfig,
    GracyNamespace,
    GracyOffsetPaginator,
    Path,
    PydanticDecoder,
    Query,
    Rate,
    Retry,
    Throttle,
    get,
    raises,
    status,
)


# ── 1. Your models are the contract ─────────────────────────────────────────
class Pokemon(BaseModel):
    id: int
    name: str
    height: int
    weight: int


class ResourcePage(BaseModel):
    count: int
    next: str | None
    results: list[dict]


class PokemonNotFound(gracy.GracyUserDefinedException):
    BASE_MESSAGE = "No pokemon named [{NAME}] ({STATUS} from {URL})"


# ── 2. Namespaces group endpoints; config layers under the client's ─────────
class BerryNamespace(GracyNamespace):
    path_prefix = "/berry"

    @get("/{name}")
    async def get_one(self, name: Annotated[str, Path]) -> dict: ...


# ── 3. The client: annotations drive decoding, config drives behavior ───────
class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"

    config = GracyConfig(
        decoder=PydanticDecoder(),
        retry=Retry(on=(status(429, 502, 503), TimeoutError), attempts=3, wait=Backoff(0.5, multiplier=2)),
        throttle=Throttle(rules=[Rate(5, per="1s")]),
    )

    berry = BerryNamespace()

    # 404 becomes None - and the return type SAYS so:
    @get("/pokemon/{name}", on={HTTPStatus.NOT_FOUND: None})
    async def get_pokemon(self, name: Annotated[str, Path]) -> Pokemon | None: ...

    # Or make 404 raise a rich domain exception - type narrows to Pokemon:
    @get("/pokemon/{name}", on={HTTPStatus.NOT_FOUND: raises(PokemonNotFound)})
    async def get_pokemon_strict(self, name: Annotated[str, Path]) -> Pokemon: ...

    # Query params with defaults; page decoded straight into your model:
    @get("/pokemon")
    async def list_pokemon(
        self,
        offset: Annotated[int, Query] = 0,
        limit: Annotated[int, Query] = 20,
    ) -> ResourcePage: ...


async def main() -> None:
    async with PokeAPI() as api:
        mew = await api.get_pokemon("mew")
        reveal_type(mew)  # ── pyright: Pokemon | None

        if mew:  # narrowing works like any other Optional
            reveal_type(mew.height)  # ── pyright: int
            print(f"{mew.name} weighs {mew.weight}")

        strict = await api.get_pokemon_strict("pikachu")
        reveal_type(strict)  # ── pyright: Pokemon - no None to handle
        print(strict.id, strict.name)

        page = await api.list_pokemon(limit=5)
        reveal_type(page)  # ── pyright: ResourcePage
        reveal_type(page.next)  # ── pyright: str | None

        cheri = await api.berry.get_one("cheri")
        reveal_type(cheri)  # ── pyright: dict[Unknown, Unknown]

        # Pagination is generic over your model too:
        paginator = GracyOffsetPaginator[ResourcePage](
            gracy_func=api.list_pokemon,
            has_next=lambda p: bool(p.next) if p else True,
            page_size=40,
        )
        async for page in paginator:
            reveal_type(page)  # ── pyright: ResourcePage
            break

        api.report().print("list")


if __name__ == "__main__":
    asyncio.run(main())
