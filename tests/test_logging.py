"""Log events: per-event levels, placeholder templating, retry logs, error defaults.

All requests go through MockTransport; assertions are made on caplog records
from the "gracy" logger (which ships a NullHandler but still propagates).
"""

from __future__ import annotations

import logging
import re
import typing as t

import pytest

from gracy import (
    Gracy,
    GracyConfig,
    LogEvent,
    LogLevel,
    NonOkResponse,
    Retry,
    get,
    status,
)
from gracy.testing import MockTransport

BASE = "https://logs.test"


def script(*values: t.Any) -> t.Callable[[t.Any], t.Any]:
    """MockTransport callable: pops scripted values in order (last one repeats)."""
    remaining = list(values)

    def responder(spec: t.Any) -> t.Any:
        value = remaining.pop(0) if remaining else values[-1]
        if isinstance(value, BaseException):
            raise value
        return value

    return responder


def gracy_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "gracy"]


def messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in gracy_records(caplog)]


# --------------------------------------------------------------------------- log_request


class RequestLogAPI(Gracy):
    base_url = BASE
    config = GracyConfig(log_request=LogEvent(LogLevel.DEBUG))

    @get("/pokemon/{name}")
    async def get_pokemon(self, name: str) -> dict: ...


async def test_log_request_emits_at_configured_level_with_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock = MockTransport({"*": {"ok": True}})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with RequestLogAPI(transport=mock) as api:
            await api.get_pokemon("mew")

    records = [r for r in gracy_records(caplog) if "Request on" in r.getMessage()]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.DEBUG
    # {URL} is FORMATTED (placeholder filled), default request template used
    assert record.getMessage() == f"Request on GET {BASE}/pokemon/mew"
    # structured placeholders travel on the record as extra["gracy"]
    gracy_extra = record.gracy  # type: ignore[attr-defined]
    assert gracy_extra["URL"] == f"{BASE}/pokemon/mew"
    assert gracy_extra["METHOD"] == "GET"
    assert gracy_extra["NAME"] == "mew"


# --------------------------------------------------------------------------- log_response


class ResponseLogAPI(Gracy):
    base_url = BASE
    config = GracyConfig(log_response=LogEvent(LogLevel.INFO))

    @get("/pokemon/{name}")
    async def get_pokemon(self, name: str) -> dict: ...


async def test_log_response_includes_status_and_elapsed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock = MockTransport({"*": {"ok": True}})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with ResponseLogAPI(transport=mock) as api:
            await api.get_pokemon("ditto")

    matching = [r for r in gracy_records(caplog) if "returned" in r.getMessage()]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelno == logging.INFO
    url = f"{BASE}/pokemon/ditto"
    # {STATUS} resolved to 200, {ELAPSED} formatted as seconds with 3 decimals
    assert re.fullmatch(
        rf"\[GET\] {re.escape(url)} returned 200 in \d+\.\d{{3}}s", record.getMessage()
    )


# --------------------------------------------------------------------------- custom templates


async def test_custom_message_template_honored(caplog: pytest.LogCaptureFixture) -> None:
    class CustomAPI(Gracy):
        base_url = BASE
        config = GracyConfig(
            log_response=LogEvent(
                LogLevel.WARNING, custom_message="Got {STATUS} for {NAME} via {METHOD}"
            )
        )

        @get("/pokemon/{name}")
        async def get_pokemon(self, name: str) -> dict: ...

    mock = MockTransport({"*": {"ok": True}})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with CustomAPI(transport=mock) as api:
            await api.get_pokemon("pikachu")

    matching = [r for r in gracy_records(caplog) if r.getMessage().startswith("Got ")]
    assert len(matching) == 1
    assert matching[0].levelno == logging.WARNING
    assert matching[0].getMessage() == "Got 200 for pikachu via GET"


async def test_unknown_placeholder_stays_literal(caplog: pytest.LogCaptureFixture) -> None:
    class NopeAPI(Gracy):
        base_url = BASE
        config = GracyConfig(
            log_response=LogEvent(LogLevel.INFO, custom_message="status={STATUS} nope={NOPE}")
        )

        @get("/thing")
        async def get_thing(self) -> dict: ...

    mock = MockTransport({"*": {"ok": True}})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with NopeAPI(transport=mock) as api:
            await api.get_thing()

    assert "status=200 nope={NOPE}" in messages(caplog)


async def test_uppercase_endpoint_args_resolve(caplog: pytest.LogCaptureFixture) -> None:
    class ArgsAPI(Gracy):
        base_url = BASE
        config = GracyConfig(
            log_request=LogEvent(LogLevel.INFO, custom_message="fetching {NAME} from {ENDPOINT}")
        )

        @get("/pokemon/{name}")
        async def get_pokemon(self, name: str) -> dict: ...

    mock = MockTransport({"*": {"ok": True}})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with ArgsAPI(transport=mock) as api:
            await api.get_pokemon("charmander")

    assert "fetching charmander from /pokemon/{name}" in messages(caplog)


# --------------------------------------------------------------------------- retry events


async def test_retry_before_fires_during_flaky_run(caplog: pytest.LogCaptureFixture) -> None:
    retry = Retry(on=status(503), attempts=3, wait=0.0, log_before=LogEvent(LogLevel.INFO))

    class FlakyAPI(Gracy):
        base_url = BASE

        @get("/flaky", retry=retry)
        async def get_flaky(self) -> dict: ...

    mock = MockTransport({f"GET {BASE}/flaky": script((503, {}), (503, {}), {"ok": True})})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with FlakyAPI(transport=mock) as api:
            assert await api.get_flaky() == {"ok": True}

    msgs = messages(caplog)
    # {RETRY_ATTEMPT}/{MAX_ATTEMPTS}, {URL}, {RETRY_DELAY} and {RETRY_CAUSE} all resolve
    assert f"Retry 1/3 for {BASE}/flaky in 0.00s (status 503)" in msgs
    assert f"Retry 2/3 for {BASE}/flaky in 0.00s (status 503)" in msgs
    assert not any("GAVE UP" in m for m in msgs)  # it eventually succeeded


async def test_retry_exhausted_fires_when_giving_up(caplog: pytest.LogCaptureFixture) -> None:
    retry = Retry(
        on=status(503),
        attempts=2,
        wait=0.0,
        log_before=LogEvent(LogLevel.DEBUG),
        log_exhausted=LogEvent(LogLevel.WARNING),
    )

    class DownAPI(Gracy):
        base_url = BASE

        @get("/down", retry=retry)
        async def get_down(self) -> dict: ...

    mock = MockTransport({f"GET {BASE}/down": (503, {"err": "still down"})})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with DownAPI(transport=mock) as api:
            with pytest.raises(NonOkResponse):
                await api.get_down()

    exhausted = [r for r in gracy_records(caplog) if "GAVE UP" in r.getMessage()]
    assert len(exhausted) == 1
    assert exhausted[0].levelno == logging.WARNING
    assert exhausted[0].getMessage() == f"GAVE UP retrying {BASE}/down after 2 attempts"
    # both retry attempts logged before giving up
    before = [m for m in messages(caplog) if m.startswith("Retry ")]
    assert len(before) == 2


# --------------------------------------------------------------------------- log_errors


class MinimalAPI(Gracy):
    base_url = BASE  # NO config at all: library defaults apply

    @get("/broken")
    async def get_broken(self) -> dict: ...


async def test_default_log_errors_fires_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    """LIBRARY_DEFAULTS ships log_errors=LogEvent(ERROR): failures log even
    with zero logging configuration."""
    mock = MockTransport({f"GET {BASE}/broken": (500, {"err": "boom"})})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with MinimalAPI(transport=mock) as api:
            with pytest.raises(NonOkResponse):
                await api.get_broken()

    errors = [r for r in gracy_records(caplog) if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage() == f"[GET] {BASE}/broken FAILED (500)"


async def test_log_errors_none_silences(caplog: pytest.LogCaptureFixture) -> None:
    class SilentAPI(Gracy):
        base_url = BASE
        config = GracyConfig(log_errors=None)  # explicit None = disabled

        @get("/broken")
        async def get_broken(self) -> dict: ...

    mock = MockTransport({f"GET {BASE}/broken": (500, {"err": "boom"})})
    with caplog.at_level(logging.DEBUG, logger="gracy"):
        async with SilentAPI(transport=mock) as api:
            with pytest.raises(NonOkResponse):
                await api.get_broken()

    assert gracy_records(caplog) == []  # request/response default off; error silenced
