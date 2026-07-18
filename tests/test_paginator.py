"""GracyOffsetPaginator against a paging MockTransport (3 pages, next=None at end).

Regression focus: v1 hardcoded page_size=20 in its fetch path (V2_PARITY bug #4);
v2 must honor the configured page_size everywhere (default prepare AND custom
prepare_params).
"""

from __future__ import annotations

import typing as t
from urllib.parse import parse_qs, urlparse

from gracy import Gracy, GracyOffsetPaginator, get
from gracy.testing import MockTransport

BASE = "https://poke.test"
PAGES = 3


class PokeAPI(Gracy):
    base_url = BASE

    @get("/pokemon")
    async def list_pokemon(self, offset: int = 0, limit: int = 20) -> dict: ...


def paging_transport(page_size: int) -> tuple[MockTransport, list[tuple[int, int]]]:
    """MockTransport callable that serves PAGES pages of `page_size` items.

    Returns (transport, seen) where seen collects every (offset, limit) the
    transport actually received on the wire.
    """
    total = page_size * PAGES
    seen: list[tuple[int, int]] = []

    def responder(spec: t.Any) -> dict[str, t.Any]:
        query = parse_qs(urlparse(spec.url).query)
        offset = int(query.get("offset", ["0"])[0])
        limit = int(query.get("limit", ["0"])[0])
        seen.append((offset, limit))
        end = min(offset + limit, total)
        return {
            "results": [f"poke-{i}" for i in range(offset, end)],
            "next": offset + limit if offset + limit < total else None,
        }

    return MockTransport({f"GET {BASE}/pokemon*": responder}), seen


def has_next(resp: dict[str, t.Any] | None) -> bool:
    if resp is None:
        return True  # first page not fetched yet (documented contract)
    return resp["next"] is not None


def make_paginator(
    api: PokeAPI, page_size: int = 50, **kwargs: t.Any
) -> GracyOffsetPaginator[dict[str, t.Any]]:
    return GracyOffsetPaginator(api.list_pokemon, has_next, page_size=page_size, **kwargs)


# --------------------------------------------------------------- page_size regression


async def test_transport_receives_configured_page_size_not_hardcoded_20():
    transport, seen = paging_transport(page_size=50)
    async with PokeAPI(transport=transport) as api:
        page = await make_paginator(api, page_size=50).next_page()

    assert page is not None
    assert seen == [(0, 50)]  # v1 regression: would have sent limit=20


async def test_custom_prepare_params_receives_configured_page_size():
    transport, seen = paging_transport(page_size=50)
    received: list[tuple[int, int]] = []

    def prepare(token: int, page_size: int) -> dict[str, t.Any]:
        received.append((token, page_size))
        return {"offset": token, "limit": page_size}

    async with PokeAPI(transport=transport) as api:
        paginator = make_paginator(api, page_size=50, prepare_params=prepare)
        await paginator.next_page()
        await paginator.next_page()

    assert received == [(0, 50), (50, 50)]  # spy saw the CONFIGURED size, not 20
    assert seen == [(0, 50), (50, 50)]


# --------------------------------------------------------------- iteration protocol


async def test_async_for_yields_exactly_three_pages_then_stops():
    transport, seen = paging_transport(page_size=50)
    async with PokeAPI(transport=transport) as api:
        pages = [page async for page in make_paginator(api, page_size=50)]

    assert len(pages) == PAGES
    assert [page["next"] for page in pages] == [50, 100, None]
    assert all(len(page["results"]) == 50 for page in pages)
    assert seen == [(0, 50), (50, 50), (100, 50)]  # exactly 3 fetches, no extra probe


async def test_has_next_is_called_with_none_before_first_fetch():
    transport, _seen = paging_transport(page_size=50)
    probe_args: list[dict[str, t.Any] | None] = []

    def spying_has_next(resp: dict[str, t.Any] | None) -> bool:
        probe_args.append(resp)
        return has_next(resp)

    async with PokeAPI(transport=transport) as api:
        paginator = GracyOffsetPaginator(api.list_pokemon, spying_has_next, page_size=50)
        first = await paginator.next_page()

    assert probe_args[0] is None  # documented: initial call sees None...
    assert first is not None  # ...and returning True for None allows the first fetch


async def test_has_next_returning_false_for_none_never_fetches():
    # The flip side of the contract: a has_next that answers False for None
    # short-circuits before any request is made.
    transport, seen = paging_transport(page_size=50)
    async with PokeAPI(transport=transport) as api:
        paginator = GracyOffsetPaginator(
            api.list_pokemon, lambda resp: resp is not None and resp["next"] is not None, page_size=50
        )
        assert await paginator.next_page() is None
        pages = [page async for page in paginator]

    assert pages == []
    assert seen == []  # zero requests hit the transport
    assert len(transport.calls) == 0


# --------------------------------------------------------------- navigation


async def test_prev_page_floors_at_zero():
    transport, seen = paging_transport(page_size=50)
    async with PokeAPI(transport=transport) as api:
        paginator = make_paginator(api, page_size=50)
        await paginator.next_page()  # fetches offset 0, token -> 50
        await paginator.prev_page()  # token back to 0, fetches offset 0
        await paginator.prev_page()  # already at 0: max(0, 0-50) == 0, fetches offset 0 again

    assert seen == [(0, 50), (0, 50), (0, 50)]


async def test_set_page_zero_restarts_iteration():
    transport, seen = paging_transport(page_size=50)
    async with PokeAPI(transport=transport) as api:
        paginator = make_paginator(api, page_size=50)
        first_run = [page async for page in paginator]
        assert len(first_run) == PAGES

        assert await paginator.next_page() is None  # exhausted

        paginator.set_page(0)  # resets token AND current response
        second_run = [page async for page in paginator]

    assert len(second_run) == PAGES
    assert [offset for offset, _ in seen] == [0, 50, 100, 0, 50, 100]
    assert len(transport.calls) == PAGES * 2
