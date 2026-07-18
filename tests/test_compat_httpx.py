"""gracy.compat httpx adapter: idiomatic httpx snippets against the local test
server (plus an offline MockTransport path). Mirrors V2_PLAN.md §16.1."""

from __future__ import annotations

import uuid

import pytest

from gracy import GracyConfig, Retry, status
from gracy.compat._httpx import AsyncClient, Client, CompatResponse, HTTPStatusError
from gracy.exceptions import GracyRequestFailed
from gracy.testing import MockTransport


# --------------------------------------------------------------------------- the swap line


def test_the_swap_line() -> None:
    from gracy.compat import httpx  # instead of `import httpx`

    assert httpx.AsyncClient is AsyncClient
    assert httpx.Client is Client
    assert httpx.HTTPStatusError is HTTPStatusError


# --------------------------------------------------------------------------- basics


async def test_async_get_roundtrip(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.get("/echo/x")
    assert isinstance(resp, CompatResponse)
    assert resp.status_code == 200
    assert resp.json()["path"] == "/echo/x"
    assert resp.is_success
    assert not resp.is_error


async def test_response_shape(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.get("/echo/shape")
    assert resp.content == resp.text.encode("utf-8")
    assert resp.headers.get("Content-Type") == "application/json"  # case-insensitive
    assert resp.headers["content-type"] == "application/json"
    assert "CONTENT-TYPE" in resp.headers
    assert str(resp.url).endswith("/echo/shape")
    assert resp.elapsed.total_seconds() >= 0
    assert resp.http_version.startswith("HTTP/")
    assert resp.raise_for_status() is resp


# --------------------------------------------------------------------------- params / headers merging


async def test_params_merge_client_under_call(test_server: str) -> None:
    async with AsyncClient(base_url=test_server, params={"a": "1", "b": "client"}) as client:
        resp = await client.get("/echo/params", params={"b": "call", "c": "3"})
    query = resp.json()["query"]
    assert query == {"a": "1", "b": "call", "c": "3"}


async def test_headers_merge_client_under_call(test_server: str) -> None:
    async with AsyncClient(base_url=test_server, headers={"x-one": "client", "x-two": "keep"}) as client:
        resp = await client.get("/echo/headers", headers={"X-One": "call"})
    seen = resp.json()["headers"]
    assert seen["x-one"] == "call"  # per-call wins
    assert seen["x-two"] == "keep"  # client-level survives


# --------------------------------------------------------------------------- bodies


async def test_post_json(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.post("/status/201", json={"name": "mew"})
    assert resp.status_code == 201
    assert resp.json()["body"] == '{"name": "mew"}'


async def test_post_content(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.post("/status/200", content=b"raw-bytes")
    assert resp.status_code == 200
    assert resp.json()["body"] == "raw-bytes"


async def test_post_form_data(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.post("/status/200", data={"a": "1", "b": "2"})
    assert resp.json()["body"] == "a=1&b=2"


# --------------------------------------------------------------------------- url joining


async def test_relative_vs_absolute_urls(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        rel = await client.get("/echo/relative")
        abs_ = await client.get(f"{test_server}/echo/absolute")
    assert rel.json()["path"] == "/echo/relative"
    assert abs_.json()["path"] == "/echo/absolute"


async def test_no_base_url_absolute_only(test_server: str) -> None:
    async with AsyncClient() as client:
        resp = await client.get(f"{test_server}/echo/naked")
    assert resp.status_code == 200


# --------------------------------------------------------------------------- error semantics


async def test_404_is_returned_not_raised(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.get("/status/404")
    assert resp.status_code == 404
    assert resp.is_client_error
    assert resp.is_error
    assert not resp.is_success
    assert not resp.is_server_error


async def test_raise_for_status(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.get("/status/404")
        with pytest.raises(HTTPStatusError) as exc_info:
            resp.raise_for_status()
    err = exc_info.value
    assert err.response is resp
    assert err.request is None
    assert "404" in str(err)


async def test_5xx_flags(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.get("/status/503")
    assert resp.status_code == 503
    assert resp.is_server_error
    assert resp.is_error
    assert not resp.is_client_error


async def test_small_timeout_raises(test_server: str) -> None:
    async with AsyncClient(base_url=test_server) as client:
        with pytest.raises(GracyRequestFailed):
            await client.get("/slow", params={"ms": "2000"}, timeout=0.05)


# --------------------------------------------------------------------------- sync client


def test_sync_client_roundtrip(test_server: str) -> None:
    with Client(base_url=test_server, params={"a": "1"}) as client:
        resp = client.get("/echo/sync", params={"b": "2"})
        posted = client.post("/status/200", json={"sync": True})
    assert resp.status_code == 200
    assert resp.json()["path"] == "/echo/sync"
    assert resp.json()["query"] == {"a": "1", "b": "2"}
    assert posted.json()["body"] == '{"sync": true}'


def test_sync_client_raise_for_status(test_server: str) -> None:
    with Client(base_url=test_server) as client:
        resp = client.get("/status/404")
        with pytest.raises(HTTPStatusError):
            resp.raise_for_status()


# --------------------------------------------------------------------------- gracy pipeline upgrades


async def test_config_retry_upgrades_flaky_to_success(test_server: str) -> None:
    key = uuid.uuid4().hex
    config = GracyConfig(retry=Retry(on=status(503), attempts=3, wait=0.0))
    async with AsyncClient(base_url=test_server, config=config) as client:
        resp = await client.get(f"/flaky/{key}", params={"fail_times": "2"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "calls": 3}


async def test_without_retry_flaky_returns_503(test_server: str) -> None:
    key = uuid.uuid4().hex
    async with AsyncClient(base_url=test_server) as client:
        resp = await client.get(f"/flaky/{key}", params={"fail_times": "2"})
    assert resp.status_code == 503


# --------------------------------------------------------------------------- injected transport (offline)


async def test_injected_mock_transport_offline() -> None:
    transport = MockTransport(
        {
            "GET https://offline.test/hello": {"hi": True},
            "PUT https://offline.test/put": (204, ""),
            "https://offline.test/*": 418,
        }
    )
    async with AsyncClient(base_url="https://offline.test", transport=transport) as client:
        hello = await client.get("/hello")
        put = await client.put("/put")
        teapot = await client.delete("/whatever")
    assert hello.status_code == 200
    assert hello.json() == {"hi": True}
    assert put.status_code == 204
    assert teapot.status_code == 418
    assert [c.method for c in transport.calls] == ["GET", "PUT", "DELETE"]


def test_sync_injected_mock_transport_offline() -> None:
    transport = MockTransport({"GET https://offline.test/ping": {"pong": 1}})
    with Client(base_url="https://offline.test", transport=transport) as client:
        resp = client.get("/ping")
    assert resp.json() == {"pong": 1}
