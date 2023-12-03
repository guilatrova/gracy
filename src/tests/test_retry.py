from __future__ import annotations

import httpx
import logging
import pytest
import typing as t
from http import HTTPStatus
from unittest.mock import patch

from gracy import (
    GracefulRetry,
    GracefulValidator,
    Gracy,
    GracyConfig,
    GracyReplay,
    LogEvent,
    LogLevel,
    OverrideRetryOn,
    graceful,
)
from gracy.exceptions import GracyRequestFailed, NonOkResponse
from tests.conftest import (
    MISSING_NAME,
    PRESENT_POKEMON_NAME,
    REPLAY,
    FakeReplayStorage,
    PokeApiEndpoint,
    assert_requests_made,
)

RETRY: t.Final = GracefulRetry(
    delay=0.001,
    max_attempts=0,
    retry_on={HTTPStatus.NOT_FOUND, ValueError},
    behavior="pass",
)
"""NOTE: Max attempts will be patched later in fixture"""

RETRY_ON_NONE: t.Final = GracefulRetry(
    delay=0.001, max_attempts=1, retry_on=None, behavior="pass"
)

RETRY_LOG_BEFORE = LogEvent(LogLevel.WARNING, "LOG_BEFORE")
RETRY_LOG_AFTER = LogEvent(LogLevel.ERROR, "LOG_AFTER")
RETRY_LOG_EXHAUSTED = LogEvent(LogLevel.CRITICAL, "LOG_EXHAUSTED")

RETRY_3_TIMES_LOG: t.Final = GracefulRetry(
    delay=0.001,
    max_attempts=3,
    retry_on=HTTPStatus.NOT_FOUND,
    log_before=RETRY_LOG_BEFORE,
    log_after=RETRY_LOG_AFTER,
    log_exhausted=RETRY_LOG_EXHAUSTED,
)

RETRY_3_TIMES_OVERRIDE_PLACEHOLDER_LOG: t.Final = GracefulRetry(
    delay=90,  # Will be overriden
    max_attempts=3,
    retry_on=HTTPStatus.NOT_FOUND,
    overrides={HTTPStatus.NOT_FOUND: OverrideRetryOn(delay=0.001)},
    log_before=LogEvent(LogLevel.WARNING, "BEFORE: {RETRY_DELAY} {RETRY_CAUSE}"),
    log_after=LogEvent(LogLevel.WARNING, "AFTER: {RETRY_CAUSE}"),
)


def assert_log(record: logging.LogRecord, expected_event: LogEvent):
    assert record.levelno == expected_event.level
    assert record.message == expected_event.custom_message  # No formatting set


class CustomValidator(GracefulValidator):
    def check(self, response: httpx.Response) -> None:
        if response.json()["order"] != 47:
            raise ValueError("Pokemon #order should be 47")  # noqa: TRY003


class GracefulPokeAPI(Gracy[PokeApiEndpoint]):
    class Config:
        BASE_URL = "https://pokeapi.co/api/v2/"
        SETTINGS = GracyConfig(
            retry=RETRY,
            allowed_status_code={HTTPStatus.NOT_FOUND},
            parser={HTTPStatus.NOT_FOUND: None},
        )

    async def get_pokemon(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(retry=None)
    async def get_pokemon_without_retry(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(retry=None, parser=None, allowed_status_code=None)
    async def get_pokemon_without_retry_or_parser(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(strict_status_code={HTTPStatus.OK})
    async def get_pokemon_with_strict_status(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(allowed_status_code=None, parser={"default": lambda r: r.json()})
    async def get_pokemon_without_allowed_status(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(validators=CustomValidator())
    async def get_pokemon_with_custom_validator(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(retry=RETRY_ON_NONE)
    async def get_pokemon_with_retry_on_none(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(retry=RETRY_ON_NONE, validators=CustomValidator())
    async def get_pokemon_with_retry_on_none_and_validator(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(retry=RETRY_3_TIMES_LOG, allowed_status_code=None)
    async def get_pokemon_with_log_retry_3_times(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})

    @graceful(retry=RETRY_3_TIMES_OVERRIDE_PLACEHOLDER_LOG, allowed_status_code=None)
    async def get_pokemon_with_retry_overriden_log_placeholder(self, name: str):
        return await self.get(PokeApiEndpoint.GET_POKEMON, {"NAME": name})


@pytest.fixture()
def make_pokeapi():
    def factory(
        max_attempts: int, break_or_pass: str = "pass", replay_enabled: bool = True
    ):
        Gracy.dangerously_reset_report()

        api = GracefulPokeAPI(REPLAY) if replay_enabled else GracefulPokeAPI()
        api._base_config.retry.max_attempts = max_attempts  # type: ignore
        api._base_config.retry.behavior = break_or_pass  # type: ignore

        return api

    return factory


@pytest.fixture()
def make_flaky_pokeapi():
    def factory(
        flaky_requests: int,
        max_attempts: int | None = None,
        break_or_pass: str = "break",
    ):
        Gracy.dangerously_reset_report()

        force_urls = (
            ["https://pokeapi.co/api/v2/pokemon/doesnt-exist"] * flaky_requests
        ) + (["https://pokeapi.co/api/v2/pokemon/charmander"])
        mock_storage = FakeReplayStorage(force_urls)
        fake_replay = GracyReplay("replay", mock_storage)

        api = GracefulPokeAPI(fake_replay)
        if max_attempts:
            api._base_config.retry.max_attempts = max_attempts  # type: ignore
        api._base_config.retry.behavior = break_or_pass  # type: ignore

        return api

    return factory


class PokeApiFactory(t.Protocol):
    def __call__(
        self,
        max_attempts: int,
        break_or_pass: str = "pass",
        replay_enabled: bool = True,
    ) -> GracefulPokeAPI:
        ...


class FlakyPokeApiFactory(t.Protocol):
    def __call__(
        self,
        flaky_requests: int,
        max_attempts: int | None = None,
        break_or_pass: str = "pass",
    ) -> GracefulPokeAPI:
        ...


async def test_ensure_replay_is_enabled(make_pokeapi: PokeApiFactory):
    pokeapi = make_pokeapi(0)
    result = await pokeapi.get_pokemon(MISSING_NAME)
    report = pokeapi.get_report()

    assert result is None
    assert report.replay_settings is not None
    assert report.replay_settings.mode == "replay"
    assert len(report.requests) == 1
    assert report.requests[0].total_requests == 1


@pytest.mark.parametrize("max_retries", [2, 4, 6])
async def test_pokemon_not_found(max_retries: int, make_pokeapi: PokeApiFactory):
    EXPECTED_REQS: t.Final = 1 + max_retries  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(max_retries)
    result = await pokeapi.get_pokemon(MISSING_NAME)

    assert result is None
    assert_requests_made(pokeapi, EXPECTED_REQS)


@pytest.mark.parametrize("max_retries", [2, 4, 6])
async def test_pokemon_not_found_without_allowed(
    max_retries: int, make_pokeapi: t.Callable[[int, str], GracefulPokeAPI]
):
    EXPECTED_REQS: t.Final = 1 + max_retries  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(max_retries, "break")

    with pytest.raises(NonOkResponse):
        await pokeapi.get_pokemon_without_allowed_status(MISSING_NAME)

    assert_requests_made(pokeapi, EXPECTED_REQS)


@pytest.mark.parametrize("max_retries", [2, 4, 6])
async def test_pokemon_not_found_with_strict_status(
    max_retries: int, make_pokeapi: PokeApiFactory
):
    EXPECTED_REQS: t.Final = 1 + max_retries  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(max_retries)
    result = await pokeapi.get_pokemon_with_strict_status(MISSING_NAME)

    assert result is None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_pokemon_with_bad_parser_break_wont_run(make_pokeapi: PokeApiFactory):
    MAX_RETRIES: t.Final = 2
    EXPECTED_REQS: t.Final = 1 + MAX_RETRIES  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(MAX_RETRIES, "break")

    with pytest.raises(NonOkResponse):
        await pokeapi.get_pokemon_without_allowed_status(MISSING_NAME)

    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_with_failing_custom_validation(make_pokeapi: PokeApiFactory):
    MAX_RETRIES: t.Final = 2
    EXPECTED_REQS: t.Final = 1 + MAX_RETRIES  # First request + Retries (2) = 3 requests

    pokeapi = make_pokeapi(MAX_RETRIES)
    result = await pokeapi.get_pokemon_with_custom_validator(PRESENT_POKEMON_NAME)

    assert result is not None

    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_failing_without_retry(make_pokeapi: PokeApiFactory):
    EXPECTED_REQS: t.Final = 1

    pokeapi = make_pokeapi(0)  # Won't have effect

    result = await pokeapi.get_pokemon_without_retry(MISSING_NAME)

    assert result is None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_failing_without_retry_or_parser(make_pokeapi: PokeApiFactory):
    EXPECTED_REQS: t.Final = 1

    pokeapi = make_pokeapi(0)  # Won't have effect

    with pytest.raises(NonOkResponse):
        await pokeapi.get_pokemon_without_retry_or_parser(MISSING_NAME)

    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_none_for_successful_request(make_pokeapi: PokeApiFactory):
    EXPECTED_REQS: t.Final = 1

    pokeapi = make_pokeapi(0)  # Won't have effect

    result = await pokeapi.get_pokemon_with_retry_on_none(PRESENT_POKEMON_NAME)

    assert result is not None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_none_for_failing_request(make_pokeapi: PokeApiFactory):
    EXPECTED_REQS: t.Final = 2

    pokeapi = make_pokeapi(0)  # Won't have effect

    result = await pokeapi.get_pokemon_with_retry_on_none(MISSING_NAME)

    assert result is None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_none_for_failing_validator(make_pokeapi: PokeApiFactory):
    EXPECTED_REQS: t.Final = 2

    pokeapi = make_pokeapi(0)  # Won't have effect

    response = await pokeapi.get_pokemon_with_retry_on_none_and_validator(
        PRESENT_POKEMON_NAME
    )

    assert response is not None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_eventually_recovers(make_flaky_pokeapi: FlakyPokeApiFactory):
    RETRY_ATTEMPTS: t.Final = 4
    EXPECTED_REQS: t.Final = 1 + RETRY_ATTEMPTS

    # Scenario: 1 + 3 Retry attemps fail + Last attempt works
    pokeapi = make_flaky_pokeapi(4, RETRY_ATTEMPTS)

    result = await pokeapi.get_pokemon(PRESENT_POKEMON_NAME)

    # Test
    assert result is not None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_eventually_recovers_with_strict(
    make_flaky_pokeapi: FlakyPokeApiFactory,
):
    RETRY_ATTEMPTS: t.Final = 4
    EXPECTED_REQS: t.Final = 1 + RETRY_ATTEMPTS

    # Scenario: 1 + 3 Retry attempts fail + last attempt works
    pokeapi = make_flaky_pokeapi(4, RETRY_ATTEMPTS)

    result = await pokeapi.get_pokemon_with_strict_status(PRESENT_POKEMON_NAME)

    # Test
    assert result is not None
    assert_requests_made(pokeapi, EXPECTED_REQS)


async def test_retry_logs(
    make_flaky_pokeapi: FlakyPokeApiFactory, caplog: pytest.LogCaptureFixture
):
    FLAKY_REQUESTS: t.Final = 3
    EXPECTED_REQS: t.Final = FLAKY_REQUESTS + 1

    pokeapi = make_flaky_pokeapi(FLAKY_REQUESTS)

    result = await pokeapi.get_pokemon_with_log_retry_3_times(PRESENT_POKEMON_NAME)

    # Test
    assert result is not None
    assert_requests_made(pokeapi, EXPECTED_REQS)

    assert len(caplog.records) == 6
    assert_log(caplog.records[0], RETRY_LOG_BEFORE)
    assert_log(caplog.records[1], RETRY_LOG_AFTER)
    assert_log(caplog.records[2], RETRY_LOG_BEFORE)
    assert_log(caplog.records[3], RETRY_LOG_AFTER)
    assert_log(caplog.records[4], RETRY_LOG_BEFORE)
    assert_log(caplog.records[5], RETRY_LOG_AFTER)


async def test_retry_logs_fail_reason(
    make_flaky_pokeapi: FlakyPokeApiFactory, caplog: pytest.LogCaptureFixture
):
    FLAKY_REQUESTS: t.Final = 2
    EXPECTED_REQS: t.Final = FLAKY_REQUESTS + 1

    pokeapi = make_flaky_pokeapi(FLAKY_REQUESTS)

    result = await pokeapi.get_pokemon_with_retry_overriden_log_placeholder(
        PRESENT_POKEMON_NAME
    )

    # Test
    assert result is not None
    assert_requests_made(pokeapi, EXPECTED_REQS)

    assert len(caplog.records) == 4
    assert caplog.records[0].message == "BEFORE: 0.001 [Bad Status Code: 404]"
    assert caplog.records[1].message == "AFTER: [Bad Status Code: 404]"
    assert caplog.records[2].message == "BEFORE: 0.001 [Bad Status Code: 404]"
    assert caplog.records[3].message == "AFTER: SUCCESSFUL"


async def test_retry_logs_exhausts(
    make_pokeapi: PokeApiFactory, caplog: pytest.LogCaptureFixture
):
    EXPECTED_REQS: t.Final = 3 + 1  # Retry's value from graceful + 1

    pokeapi = make_pokeapi(0)  # Won't take effect due to @graceful

    with pytest.raises(NonOkResponse):
        await pokeapi.get_pokemon_with_log_retry_3_times(MISSING_NAME)

    # Test
    assert_requests_made(pokeapi, EXPECTED_REQS)

    assert len(caplog.records) == 7
    assert_log(caplog.records[0], RETRY_LOG_BEFORE)
    assert_log(caplog.records[1], RETRY_LOG_AFTER)
    assert_log(caplog.records[2], RETRY_LOG_BEFORE)
    assert_log(caplog.records[3], RETRY_LOG_AFTER)
    assert_log(caplog.records[4], RETRY_LOG_BEFORE)
    assert_log(caplog.records[5], RETRY_LOG_AFTER)
    assert_log(caplog.records[6], RETRY_LOG_EXHAUSTED)


async def test_retry_without_replay_request_without_response_generic(
    make_pokeapi: PokeApiFactory,
):
    EXPECTED_REQS: t.Final = 3 + 1

    class SomeRequestException(Exception):
        pass

    # Regardless of replay being disabled, no request will be triggered as we're mocking httpx
    pokeapi = make_pokeapi(3, break_or_pass="break", replay_enabled=False)
    pokeapi._base_config.retry.retry_on.add(GracyRequestFailed)  # type: ignore

    mock: t.Any
    with patch.object(pokeapi, "_client", autospec=True) as mock:
        mock.request.side_effect = SomeRequestException("Request failed")

        with pytest.raises(GracyRequestFailed):
            await pokeapi.get_pokemon(PRESENT_POKEMON_NAME)

    assert_requests_made(pokeapi, EXPECTED_REQS)
