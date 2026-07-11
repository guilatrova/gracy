"""Gracy 2.0 end-to-end example - live PokeAPI, Rust engine.

Run: python examples/v2_pokeapi.py
Shows: typed endpoints, 404 -> None parsing, retry, exact sliding-window
throttling enforced on real network calls, and the per-client report.
"""

from __future__ import annotations

import asyncio
import time

import gracy
from gracy import Gracy, GracyConfig, Rate, Retry, Throttle, get
from gracy.engine import current_engine


class PokeAPI(Gracy):
    base_url = "https://pokeapi.co/api/v2"
    config = GracyConfig(
        retry=Retry(on=gracy.status(429, 502, 503), attempts=2, wait=0.2),
        throttle=Throttle(rules=[Rate(3, per="1s")]),
    )

    @get("/pokemon/{name}", on={404: None})
    async def pokemon(self, name: str) -> dict | None: ...


async def main() -> None:
    print(f"engine: {current_engine()}")
    async with PokeAPI() as api:
        t0 = time.monotonic()
        names = ["pikachu", "charmander", "bulbasaur", "squirtle", "mew", "not-a-pokemon"]
        results = await asyncio.gather(*[api.pokemon(n) for n in names])
        elapsed = time.monotonic() - t0

        for name, poke in zip(names, results):
            print(f"  {name}: {'#' + str(poke['id']) if poke else 'None (404 -> None)'}")
        print(f"  {len(names)} requests @ 3/s took {elapsed:.2f}s (throttle enforced)")

        api.report().print("list")


if __name__ == "__main__":
    asyncio.run(main())
