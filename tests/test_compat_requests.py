"""gracy.compat.requests - the sync drop-in duck-type of the requests library.

Exercised against the conftest test_server:
  GET  /echo/{x}       -> {"path", "query", "headers"}
  GET|POST /status/{c} -> that status, {"status", "body": <posted body>}
  GET  /flaky/{k}?fail_times=N -> N x 503 then 200 (per key)
  GET  /slow?ms=N      -> sleeps then 200
"""

from __future__ import annotations

import datetime
import uuid

import pytest

import gracy
import gracy.compat
from gracy import GracyConfig, Retry
from gracy.compat import requests as rq


@pytest.fixture(autouse=True, scope="module")
def _compat_lifecycle():
    """Every test in this module runs against a fresh default engine and
    leaves no global compat state behind for the rest of the suite."""
    rq.configure()
    yield
    rq.shutdown()


def _key() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- the swap line


def test_the_swap_line(test_server):
    body = rq.get(f"{test_server}/echo/swap").json()
    assert body["path"] == "/echo/swap"


def test_module_attribute_access_form(test_server):
    # `import gracy.compat; gracy.compat.requests.get(...)` must work too.
    response = gracy.compat.requests.get(f"{test_server}/echo/attr")
    assert response.ok
    assert gracy.compat.requests is rq


# --------------------------------------------------------------------------- request building


def test_params_encoding(test_server):
    body = rq.get(f"{test_server}/echo/params", params={"a": "1", "b": ["x", "y"]}).json()
    assert body["query"] == {"a": "1", "b": ["x", "y"]}


def test_json_body_posts(test_server):
    payload = {"name": "gracy", "version": 2}
    response = rq.post(f"{test_server}/status/200", json=payload)
    assert response.status_code == 200
    import json as jsonlib

    assert jsonlib.loads(response.json()["body"]) == payload


def test_data_dict_form_encodes(test_server):
    response = rq.post(f"{test_server}/status/200", data={"a": "1", "b": "two words"})
    assert response.json()["body"] == "a=1&b=two+words"


def test_data_str_is_raw_body(test_server):
    response = rq.post(f"{test_server}/status/200", data="raw body here")
    assert response.json()["body"] == "raw body here"


def test_headers_per_call(test_server):
    body = rq.get(f"{test_server}/echo/h", headers={"X-Custom": "abc"}).json()
    assert body["headers"]["x-custom"] == "abc"


def test_auth_tuple_sends_basic_auth(test_server):
    body = rq.get(f"{test_server}/echo/auth", auth=("user", "pass")).json()
    assert body["headers"]["authorization"] == "Basic dXNlcjpwYXNz"


def test_relative_url_raises():
    with pytest.raises(ValueError, match="absolute URLs"):
        rq.get("/echo/nope")


def test_unsupported_kwargs_warn(test_server):
    with pytest.warns(UserWarning, match="allow_redirects"):
        response = rq.get(f"{test_server}/echo/warn", allow_redirects=False)
    assert response.ok


def test_request_verb_dispatch(test_server):
    assert rq.request("get", f"{test_server}/echo/verb").ok


# --------------------------------------------------------------------------- timeout


def test_timeout_raises_catchable(test_server):
    with pytest.raises(Exception) as exc_info:
        rq.get(f"{test_server}/slow", params={"ms": 2000}, timeout=0.1)
    # catchable via the requests idiom AND as a gracy exception
    assert isinstance(exc_info.value, rq.RequestException)
    assert isinstance(exc_info.value, gracy.GracyException)


def test_timeout_tuple_uses_max(test_server):
    response = rq.get(f"{test_server}/slow", params={"ms": 50}, timeout=(0.01, 5.0))
    assert response.ok


def test_requests_style_error_handling_still_works(test_server):
    # canonical post-swap snippet: catch rq.HTTPError around raise_for_status
    with pytest.raises(rq.HTTPError):
        rq.get(f"{test_server}/status/500").raise_for_status()


# --------------------------------------------------------------------------- response shape


def test_response_shape(test_server):
    response = rq.get(f"{test_server}/echo/shape")
    assert response.ok is True
    assert response.status_code == 200
    assert response.reason == "OK"
    assert isinstance(response.content, bytes)
    assert response.text == response.content.decode("utf-8")
    assert response.json()["path"] == "/echo/shape"
    assert isinstance(response.elapsed, datetime.timedelta)
    assert response.elapsed.total_seconds() >= 0
    assert response.url.endswith("/echo/shape")
    assert bool(response) is True
    assert repr(response) == "<Response [200]>"


def test_headers_case_insensitive(test_server):
    response = rq.get(f"{test_server}/echo/ci")
    assert response.headers["content-type"] == "application/json"
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers["CONTENT-TYPE"] == "application/json"
    assert "content-type" in {k.lower() for k in response.headers}


def test_non_2xx_returns_response_not_raise(test_server):
    response = rq.get(f"{test_server}/status/404")
    assert response.status_code == 404
    assert response.ok is False
    assert bool(response) is False
    assert response.reason == "Not Found"
    assert repr(response) == "<Response [404]>"


def test_ok_is_requests_semantics_below_400(test_server):
    # 3xx is ok in requests (gracy's is_success is 2xx-only)
    assert rq.get(f"{test_server}/status/302").ok is True


def test_raise_for_status_attaches_response(test_server):
    response = rq.get(f"{test_server}/status/404")
    with pytest.raises(rq.HTTPError) as exc_info:
        response.raise_for_status()
    assert exc_info.value.response is response
    assert "404 Client Error: Not Found for url:" in str(exc_info.value)

    with pytest.raises(rq.HTTPError, match="503 Server Error"):
        rq.get(f"{test_server}/status/503").raise_for_status()

    # 2xx: no raise
    assert rq.get(f"{test_server}/status/200").raise_for_status() is None


def test_iter_content(test_server):
    response = rq.get(f"{test_server}/echo/chunks")
    chunks = list(response.iter_content(chunk_size=5))
    assert all(isinstance(chunk, bytes) for chunk in chunks)
    assert all(len(chunk) <= 5 for chunk in chunks)
    assert b"".join(chunks) == response.content
    assert b"".join(response.iter_content(chunk_size=None)) == response.content


# --------------------------------------------------------------------------- sessions


def test_session_persistent_headers_and_merge(test_server):
    with rq.Session() as session:
        session.headers["X-Base"] = "base"
        session.headers.update({"X-Both": "session"})

        body = session.get(f"{test_server}/echo/s1").json()
        assert body["headers"]["x-base"] == "base"

        # per-call headers merge over session headers; per-call wins on clash
        body = session.get(
            f"{test_server}/echo/s2", headers={"X-Call": "call", "X-Both": "call"}
        ).json()
        assert body["headers"]["x-base"] == "base"
        assert body["headers"]["x-call"] == "call"
        assert body["headers"]["x-both"] == "call"

    # module-level verbs never see session headers
    body = rq.get(f"{test_server}/echo/s3").json()
    assert "x-base" not in body["headers"]


def test_session_post_and_context_manager(test_server):
    with rq.Session() as session:
        session.headers["X-Sess"] = "yes"
        response = session.post(f"{test_server}/status/201", json={"k": "v"})
        assert response.status_code == 201
    assert len(session.headers) == 0  # close() drops session state


# --------------------------------------------------------------------------- configure / shutdown


def test_configure_retry_makes_flaky_succeed(test_server):
    key = _key()
    try:
        rq.configure(GracyConfig(retry=Retry(on=gracy.status(503), attempts=3, wait=0.01)))
        response = rq.get(f"{test_server}/flaky/{key}", params={"fail_times": 2})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "calls": 3}  # pipeline retried twice
    finally:
        rq.configure()

    # without retry the same route yields the raw 503 (requests semantics: no raise)
    assert rq.get(f"{test_server}/flaky/{_key()}", params={"fail_times": 2}).status_code == 503


def test_shutdown_and_reconfigure_idempotent(test_server):
    rq.shutdown()
    rq.shutdown()  # idempotent
    assert rq.get(f"{test_server}/echo/revive").ok  # verbs lazily rebuild the engine

    rq.configure()
    rq.configure()  # rebuild twice in a row is fine
    assert rq.get(f"{test_server}/echo/reconf").ok


def test_module_verbs_after_configure(test_server):
    rq.configure(GracyConfig(timeout=5.0))
    try:
        assert rq.get(f"{test_server}/echo/after-conf").ok
        assert rq.post(f"{test_server}/status/200", json={"a": 1}).ok
        with rq.Session() as session:
            assert session.get(f"{test_server}/echo/after-conf-s").ok
    finally:
        rq.configure()


# --------------------------------------------------------------------------- CaseInsensitiveDict


def test_case_insensitive_dict_unit():
    d = rq.CaseInsensitiveDict({"Accept": "application/json"})
    assert d["accept"] == "application/json"
    assert d["ACCEPT"] == "application/json"
    d["accept"] = "text/html"
    assert list(d) == ["accept"]  # last-set casing wins for iteration
    assert len(d) == 1
    assert d == {"AcCePt": "text/html"}
    copied = d.copy()
    assert copied == d and copied is not d
    del d["Accept"]
    assert "accept" not in d
