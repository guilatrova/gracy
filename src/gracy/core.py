from __future__ import annotations

import asyncio
import logging
from asyncio import sleep
from datetime import timedelta
from enum import Enum
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Generic, Iterable, TypeVar

import httpx

from gracy.context import custom_gracy_config, gracy_context
from gracy.exceptions import NonOkResponse, UnexpectedResponse
from gracy.models import (
    DEFAULT_CONFIG,
    LOG_EVENT_TYPE,
    PARSER_TYPE,
    UNSET_VALUE,
    BaseEndpoint,
    GracefulRequest,
    GracefulRetry,
    GracefulRetryState,
    GracyConfig,
    LogEvent,
    ThrottleRule,
    Unset,
)
from gracy.reports import GracyReport, GracyRequestResult
from gracy.throttling import ThrottleController

logger = logging.getLogger(__name__)


Endpoint = TypeVar("Endpoint", bound=BaseEndpoint | str)  # , default=str)
RequestResult = TypeVar("RequestResult", bound=httpx.Response | None | dict[str, Any])


class DefaultLogMessage(str, Enum):
    BEFORE = "Request on {URL} is ongoing"
    AFTER = "[{METHOD}] {URL} returned {STATUS}"
    ERRORS = "[{METHOD}] {URL} returned a bad status ({STATUS})"

    THROTTLE_HIT = "{URL} hit {THROTTLE_LIMIT} reqs/s"
    THROTTLE_DONE = "Done waiting {THROTTLE_TIME}s to hit {URL}"


def _process_log_request(logevent: LogEvent, url: str):
    format_args = dict(URL=url)

    if logevent.custom_message:
        message = logevent.custom_message.format(**format_args)
    else:
        message = DefaultLogMessage.BEFORE.format(**format_args)

    logger.log(logevent.level, message, extra=format_args)


def _process_log_throttle(
    logevent: LogEvent,
    await_time: float,
    url: str,
    rule: ThrottleRule,
    default_message: str,
):
    format_args = dict(
        URL=url,
        THROTTLE_TIME=await_time,
        THROTTLE_LIMIT=rule.requests_per_second_limit,
    )

    if logevent.custom_message:
        message = logevent.custom_message.format(**format_args)
    else:
        message = default_message.format(**format_args)

    logger.log(logevent.level, message, extra=format_args)


def _process_log(logevent: LogEvent, defaultmsg: str, response: httpx.Response, elapsed: timedelta):
    format_args = dict(
        URL=response.request.url,
        METHOD=response.request.method,
        STATUS=response.status_code,
        ELAPSED=elapsed,
    )

    if logevent.custom_message:
        message = logevent.custom_message.format(**format_args)
    else:
        message = defaultmsg.format(**format_args)

    logger.log(logevent.level, message, extra=format_args)


async def _gracefully_throttle(active_config: GracyConfig, controller: ThrottleController, url: str):
    if throttling := active_config.throttling:
        has_been_throttled = True

        while has_been_throttled:
            wait_per_rule = [
                (rule, wait_time)
                for rule in throttling.rules
                if (wait_time := rule.calculate_await_time(controller)) > 0.0
            ]

            if wait_per_rule:
                rule, await_time = max(wait_per_rule, key=lambda x: x[1])

                if throttling.log_limit_reached:
                    _process_log_throttle(
                        throttling.log_limit_reached,
                        await_time,
                        url,
                        rule,
                        DefaultLogMessage.THROTTLE_HIT,
                    )

                await asyncio.sleep(await_time)

                if throttling.log_wait_over:
                    _process_log_throttle(
                        throttling.log_wait_over,
                        await_time,
                        url,
                        rule,
                        DefaultLogMessage.THROTTLE_DONE,
                    )
            else:
                has_been_throttled = False


async def _gracefully_retry(
    retry: GracefulRetry,
    config: GracyConfig,
    url: str,
    final_url: str,
    report: GracyReport,
    throttle_controller: ThrottleController,
    request: GracefulRequest,
    check_func: Callable[[GracyConfig, httpx.Response], bool],
) -> GracefulRetryState:
    failing = True
    state = retry.create_state()

    while failing:
        if retry.log_before:
            logger.log(
                retry.log_before.level.value,
                f"GracefulRetry: {url} will wait {state.delay}s before next attempt "
                f"({state.cur_attempt} out of {state.max_attempts})",
            )

        state.increment()

        if state.cant_retry:
            break

        await sleep(state.delay)
        await _gracefully_throttle(config, throttle_controller, final_url)
        throttle_controller.init_request(final_url)
        result = await request()
        report.track(GracyRequestResult(url, result))

        state.success = check_func(config, result)
        failing = not state.success

        if retry.log_after:
            logger.log(
                retry.log_after.level.value,
                f"GracefulRetry: {url} {'SUCCESS' if state.success else 'FAIL'} "
                f"({state.cur_attempt} out of {state.max_attempts})",
            )

    if state.cant_retry and retry.log_exhausted:
        logger.log(
            retry.log_exhausted.level.value,
            f"GracefulRetry: {url} exhausted the maximum attempts of {state.max_attempts})",
        )

    return state


def _check_strictness(active_config: GracyConfig, result: httpx.Response) -> bool:
    if active_config.strict_status_code:
        if not isinstance(active_config.strict_status_code, Unset):
            strict_statuses = active_config.strict_status_code
            if not isinstance(strict_statuses, Iterable):
                strict_statuses = {strict_statuses}

            if HTTPStatus(result.status_code) not in strict_statuses:
                return False

    return True


def _check_allowed(active_config: GracyConfig, result: httpx.Response) -> bool:
    if not result.is_success:
        if active_config.allowed_status_code:
            if not isinstance(active_config.allowed_status_code, Unset):
                allowed = active_config.allowed_status_code
                if not isinstance(allowed, Iterable):
                    allowed = {allowed}

                if HTTPStatus(result.status_code) not in allowed:
                    return False

    return True


async def _gracify(
    active_config: GracyConfig,
    endpoint: str,
    endpoint_args: dict[str, str] | None,
    report: GracyReport,
    throttle_controller: ThrottleController,
    request: GracefulRequest,
):
    final_url = endpoint if endpoint_args is None else endpoint.format(**endpoint_args)
    if active_config.log_request and isinstance(active_config.log_request, LogEvent):
        _process_log_request(active_config.log_request, final_url)

    await _gracefully_throttle(active_config, throttle_controller, final_url)
    throttle_controller.init_request(final_url)
    result = await request()
    report.track(GracyRequestResult(endpoint, result))

    if active_config.log_response and isinstance(active_config.log_response, LogEvent):
        _process_log(active_config.log_response, DefaultLogMessage.AFTER, result, result.elapsed)

    strict_pass = _check_strictness(active_config, result)
    if strict_pass is False:
        retry_result = None
        if active_config.should_retry(result.status_code):
            retry_result = await _gracefully_retry(
                active_config.retry,  # type: ignore
                active_config,
                endpoint,
                final_url,
                report,
                throttle_controller,
                request=request,
                check_func=_check_strictness,
            )

        if not retry_result or retry_result.failed:
            strict_codes: HTTPStatus | Iterable[HTTPStatus] = active_config.strict_status_code  # type: ignore
            if active_config.log_errors and isinstance(active_config.log_errors, LogEvent):
                _process_log(active_config.log_errors, DefaultLogMessage.ERRORS, result, result.elapsed)

            if active_config.retry.behavior == "break":  # type: ignore
                raise UnexpectedResponse(str(result.url), result, strict_codes)

    allowed_pass = _check_allowed(active_config, result)
    if allowed_pass is False:
        if active_config.should_retry(result.status_code):
            retry_result = None
            if active_config.has_retry:
                retry_result = await _gracefully_retry(
                    active_config.retry,  # type: ignore
                    active_config,
                    endpoint,
                    final_url,
                    report,
                    throttle_controller,
                    request=request,
                    check_func=_check_allowed,
                )

            if not retry_result or retry_result.failed:
                if active_config.log_errors and isinstance(active_config.log_errors, LogEvent):
                    _process_log(active_config.log_errors, DefaultLogMessage.ERRORS, result, result.elapsed)

                if active_config.retry.behavior == "break":  # type: ignore
                    raise NonOkResponse(str(result.url), result)

    if active_config.parser and not isinstance(active_config.parser, Unset):
        default_fallback = active_config.parser.get("default", UNSET_VALUE)
        parse_result = active_config.parser.get(HTTPStatus(result.status_code), default_fallback)

        if not isinstance(parse_result, Unset):
            if isinstance(parse_result, type) and issubclass(parse_result, Exception):
                raise parse_result(endpoint, endpoint_args, result)
            elif callable(parse_result):
                return parse_result(result)
            else:
                return parse_result

    return result


class Gracy(Generic[Endpoint]):
    """Helper class that provides a standard way to create an Requester using
    inheritance.
    """

    _report: GracyReport = GracyReport()
    _throttle_controller: ThrottleController = ThrottleController()

    class Config:
        BASE_URL: str = ""
        REQUEST_TIMEOUT: float | None = None
        SETTINGS: GracyConfig = DEFAULT_CONFIG

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._base_config = getattr(self.Config, "SETTINGS", DEFAULT_CONFIG)
        self._client = self._create_client(*args, **kwargs)

    def _create_client(self, *args: Any, **kwargs: Any) -> httpx.AsyncClient:
        base_url = getattr(self.Config, "BASE_URL", "")
        request_timeout = getattr(self.Config, "REQUEST_TIMEOUT", None)
        return httpx.AsyncClient(base_url=base_url, timeout=request_timeout)

    async def _request(
        self,
        method: str,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        custom_config = gracy_context.get()
        active_config = self._base_config
        if custom_config:
            active_config = GracyConfig.merge_config(self._base_config, custom_config)

        if endpoint_args:
            final_endpoint = endpoint.format(**endpoint_args)
        else:
            final_endpoint = endpoint

        graceful_request = _gracify(
            active_config,
            endpoint,
            endpoint_args,
            Gracy._report,
            Gracy._throttle_controller,
            GracefulRequest(
                self._client.request,
                method,
                final_endpoint,
                *args,
                **kwargs,
            ),
        )

        return await graceful_request

    async def get(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        return await self._request("GET", endpoint, endpoint_args, *args, **kwargs)

    async def post(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        return await self._request("POST", endpoint, endpoint_args, *args, **kwargs)

    async def put(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        return await self._request("PUT", endpoint, endpoint_args, *args, **kwargs)

    async def patch(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        return await self._request("PATCH", endpoint, endpoint_args, *args, **kwargs)

    async def delete(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        return await self._request("DELETE", endpoint, endpoint_args, *args, **kwargs)

    async def head(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        return await self._request("HEAD", endpoint, endpoint_args, *args, **kwargs)

    async def options(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        return await self._request("OPTIONS", endpoint, endpoint_args, *args, **kwargs)

    @classmethod
    def report_status(cls):
        cls._report.print()


def graceful(
    strict_status_code: Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE,
    allowed_status_code: Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE,
    retry: GracefulRetry | Unset | None = UNSET_VALUE,
    log_request: LOG_EVENT_TYPE = UNSET_VALUE,
    log_response: LOG_EVENT_TYPE = UNSET_VALUE,
    log_errors: LOG_EVENT_TYPE = UNSET_VALUE,
    parser: PARSER_TYPE = UNSET_VALUE,
):
    config = GracyConfig(
        strict_status_code=strict_status_code,
        allowed_status_code=allowed_status_code,
        retry=retry,
        log_request=log_request,
        log_response=log_response,
        log_errors=log_errors,
        parser=parser,
    )

    def _wrapper(wrapped_function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def _inner_wrapper(*args: Any, **kwargs: Any):
            with custom_gracy_config(config):
                res = await wrapped_function(*args, **kwargs)
                return res

        return _inner_wrapper

    return _wrapper
