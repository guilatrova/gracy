"""THE duck-typing proof: the SAME script body runs against the real library
and against gracy.compat - only the import line changes.

Each scenario is a plain function written as if for requests/httpx. It is then
executed twice: once with the genuine library, once with the gracy drop-in,
and the results must match.
"""

from __future__ import annotations

import typing as t

import pytest

# --------------------------------------------------------------------------- requests


def _requests_script(requests: t.Any, base: str) -> dict[str, t.Any]:
    """A script somebody wrote for the real `requests` library."""
    out: dict[str, t.Any] = {}

    r = requests.get(f"{base}/echo/duck", params={"q": "1"}, timeout=5)
    out["status"] = r.status_code
    out["ok"] = r.ok
    out["query"] = r.json()["query"]
    out["ct"] = r.headers.get("Content-Type", r.headers.get("content-type"))

    r2 = requests.post(f"{base}/status/201", json={"who": "duck"}, timeout=5)
    out["created"] = r2.status_code
    r2.raise_for_status()  # 201 -> no raise

    try:
        requests.get(f"{base}/status/404", timeout=5).raise_for_status()
    except Exception as exc:  # requests.HTTPError | gracy HTTPError - duck-typed
        out["raised"] = type(exc).__name__.endswith("HTTPError")
        out["raised_status"] = exc.response.status_code  # type: ignore[attr-defined]

    with requests.Session() as s:
        s.headers.update({"x-duck": "quack"})
        out["session_header"] = s.get(f"{base}/echo/s", timeout=5).json()["headers"].get("x-duck")

    return out


def test_requests_script_runs_identically_on_real_and_gracy(test_server: str) -> None:
    real = pytest.importorskip("requests")
    from gracy.compat import requests as gracy_requests

    result_real = _requests_script(real, test_server)
    result_gracy = _requests_script(gracy_requests, test_server)

    assert result_real == result_gracy
    assert result_gracy["ok"] is True and result_gracy["raised"] is True and result_gracy["raised_status"] == 404
    gracy_requests.shutdown()


# --------------------------------------------------------------------------- httpx (async)


async def _httpx_script(httpx: t.Any, base: str) -> dict[str, t.Any]:
    """A script somebody wrote for the real `httpx` library."""
    out: dict[str, t.Any] = {}
    async with httpx.AsyncClient(base_url=base, headers={"x-duck": "quack"}) as client:
        r = await client.get("/echo/duck", params={"q": "1"})
        out["status"] = r.status_code
        out["is_success"] = r.is_success
        out["query"] = r.json()["query"]
        out["header_sent"] = r.json()["headers"].get("x-duck")

        r2 = await client.post("/status/201", json={"who": "duck"})
        out["created"] = r2.status_code

        r3 = await client.get("/status/404")
        out["not_found_returned"] = r3.status_code  # httpx returns, never raises implicitly
        try:
            r3.raise_for_status()
        except Exception as exc:
            out["raised"] = type(exc).__name__ == "HTTPStatusError"
            out["raised_status"] = exc.response.status_code  # type: ignore[attr-defined]
    return out


async def test_httpx_script_runs_identically_on_real_and_gracy(test_server: str) -> None:
    real = pytest.importorskip("httpx")
    from gracy.compat import httpx as gracy_httpx

    result_real = await _httpx_script(real, test_server)
    result_gracy = await _httpx_script(gracy_httpx, test_server)

    assert result_real == result_gracy
    assert result_gracy["is_success"] is True
    assert result_gracy["not_found_returned"] == 404 and result_gracy["raised"] is True
