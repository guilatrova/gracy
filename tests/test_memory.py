"""Regression tests for v1's per-instance typed-method leak.

v1's _init_typed_http_methods created SEVEN new classes (Get/Post/...) on
EVERY Gracy instantiation and stored them on the instance — memory grew with
each client, and the classes kept instances alive until the weakref band-aid
(v1.34.0). v2 endpoints are class-level descriptors: instantiating a client
must create ZERO new classes, and clients must be garbage-collectible.
"""

from __future__ import annotations

import asyncio
import gc
import weakref

from gracy import Gracy, get
from gracy.testing import MockTransport


class DuckAPI(Gracy):
    base_url = "https://example.test"

    @get("/duck/{name}")
    async def duck(self, name: str) -> dict: ...

    @get("/ducks")
    async def ducks(self) -> list: ...


def _count_classes() -> int:
    gc.collect()
    return sum(1 for o in gc.get_objects() if isinstance(o, type))


def test_instantiation_creates_no_new_classes() -> None:
    DuckAPI()  # warm any lazy first-touch caches
    before = _count_classes()
    clients = [DuckAPI() for _ in range(100)]
    after = _count_classes()
    assert after == before, f"{after - before} classes created by 100 instantiations (v1 leaked 7 PER instance)"
    del clients


def test_endpoint_descriptors_are_shared_class_attrs() -> None:
    # The descriptor lives on the class — one object, not per-instance copies.
    assert DuckAPI.__dict__["duck"] is type(DuckAPI()).__dict__["duck"]
    a, b = DuckAPI(), DuckAPI()
    # Bound access is an ephemeral closure (like bound methods), never cached on self.
    a.duck  # noqa: B018
    b.duck  # noqa: B018
    assert "duck" not in a.__dict__ and "duck" not in b.__dict__


async def test_built_client_is_garbage_collectible() -> None:
    client = DuckAPI(transport=MockTransport({"*/duck/*": {"name": "mew"}}))
    async with client as api:
        assert (await api.duck("mew")) == {"name": "mew"}
    ref = weakref.ref(client)
    del client, api
    for _ in range(3):
        gc.collect()
    assert ref() is None, "closed client not garbage-collected — something in the pipeline pins it"


async def test_many_client_lifecycles_do_not_accumulate_objects() -> None:
    async def one_lifecycle() -> None:
        async with DuckAPI(transport=MockTransport({"*/duck/*": {"ok": 1}})) as api:
            await api.duck("x")

    await one_lifecycle()  # warm-up
    gc.collect()
    baseline = len(gc.get_objects())
    for _ in range(30):
        await one_lifecycle()
    gc.collect()
    growth = len(gc.get_objects()) - baseline
    # Generous bound: some interpreter-level caching is normal; v1's bug grew
    # by hundreds of objects (7 classes + machinery) per client.
    assert growth < 30 * 20, f"object count grew by {growth} across 30 client lifecycles"


def test_sync_facade_thread_cleanup() -> None:
    import threading

    baseline = threading.active_count()
    with DuckAPI.sync(transport=MockTransport({"*/duck/*": {"ok": 1}})) as api:
        assert api.duck("x") == {"ok": 1}
    deadline = asyncio.get_event_loop_policy()  # noqa: F841 - no loop needed; just wait for join
    for _ in range(20):
        if threading.active_count() <= baseline:
            break
        import time

        time.sleep(0.05)
    assert threading.active_count() <= baseline, "sync facade leaked its loop thread"
