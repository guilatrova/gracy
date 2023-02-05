from __future__ import annotations

import asyncio
from asyncio import sleep
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Generic, Iterable, cast

import httpx

from ._configs import custom_config_context, custom_gracy_config
from ._loggers import (
    DefaultLogMessage,
    process_log_after_request,
    process_log_before_request,
    process_log_retry,
    process_log_throttle,
)
from ._models import (
    DEFAULT_CONFIG,
    LOG_EVENT_TYPE,
    PARSER_TYPE,
    THROTTLE_LOCKER,
    UNSET_VALUE,
    Endpoint,
    GracefulRequest,
    GracefulRetry,
    GracefulRetryState,
    GracyConfig,
    GracyRequestContext,
    LogEvent,
    ThrottleController,
    Unset,
)
from ._reports._builders import ReportBuilder
from ._reports._printers import PRINTERS, print_report
from .exceptions import NonOkResponse, UnexpectedResponse


async def _gracefully_throttle(controller: ThrottleController, request_context: GracyRequestContext):
    if isinstance(request_context.active_config.throttling, Unset):
        return

    if throttling := request_context.active_config.throttling:
        has_been_throttled = True

        while has_been_throttled:
            wait_per_rule = [
                (rule, wait_time)
                for rule in throttling.rules
                if (wait_time := rule.calculate_await_time(controller)) > 0.0
            ]

            if wait_per_rule:
                rule, await_time = max(wait_per_rule, key=lambda x: x[1])
                if THROTTLE_LOCKER.is_rule_throttled(rule):
                    await asyncio.sleep(await_time)
                    continue

                if throttling.log_limit_reached:
                    process_log_throttle(
                        throttling.log_limit_reached,
                        DefaultLogMessage.THROTTLE_HIT,
                        await_time,
                        rule,
                        request_context,
                    )

                with THROTTLE_LOCKER.lock_rule(rule):
                    await asyncio.sleep(await_time)

                if throttling.log_wait_over:
                    process_log_throttle(
                        throttling.log_wait_over,
                        DefaultLogMessage.THROTTLE_DONE,
                        await_time,
                        rule,
                        request_context,
                    )
            else:
                has_been_throttled = False


async def _gracefully_retry(
    report: ReportBuilder,
    throttle_controller: ThrottleController,
    request: GracefulRequest,
    request_context: GracyRequestContext,
    check_func: Callable[[GracyConfig, httpx.Response], bool],
) -> GracefulRetryState:
    retry = cast(GracefulRetry, request_context.active_config.retry)
    state = retry.create_state()
    config = request_context.active_config

    failing = True
    while failing:
        if retry.log_before:
            process_log_retry(retry.log_before, DefaultLogMessage.RETRY_BEFORE, request_context, state)

        state.increment()

        if state.cant_retry:
            break

        await sleep(state.delay)
        await _gracefully_throttle(throttle_controller, request_context)
        throttle_controller.init_request(request_context)
        result = await request()
        report.track(request_context, result)

        state.success = check_func(config, result)
        failing = not state.success

        if retry.log_after:
            process_log_retry(retry.log_after, DefaultLogMessage.RETRY_AFTER, request_context, state)

    if state.cant_retry and retry.log_exhausted:
        process_log_retry(retry.log_exhausted, DefaultLogMessage.RETRY_EXHAUSTED, request_context, state)

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
    successful = result.is_success
    if not successful and active_config.allowed_status_code:
        if not isinstance(active_config.allowed_status_code, Unset):
            allowed = active_config.allowed_status_code
            if not isinstance(allowed, Iterable):
                allowed = {allowed}

            if HTTPStatus(result.status_code) not in allowed:
                return False

    return successful


async def _gracify(
    report: ReportBuilder,
    throttle_controller: ThrottleController,
    request: GracefulRequest,
    request_context: GracyRequestContext,
):
    active_config = request_context.active_config

    if isinstance(active_config.log_request, LogEvent):
        process_log_before_request(active_config.log_request, request_context)

    await _gracefully_throttle(throttle_controller, request_context)
    throttle_controller.init_request(request_context)
    result = await request()
    report.track(request_context, result)

    if active_config.log_response and isinstance(active_config.log_response, LogEvent):
        process_log_after_request(active_config.log_response, DefaultLogMessage.AFTER, request_context, result)

    strict_pass = _check_strictness(active_config, result)
    if strict_pass is False:
        strict_codes: HTTPStatus | Iterable[HTTPStatus] = active_config.strict_status_code  # type: ignore
        retry_result = None
        must_break = True

        if active_config.should_retry(result.status_code):
            retry_result = await _gracefully_retry(
                report,
                throttle_controller,
                request,
                request_context,
                check_func=_check_strictness,
            )

        if not retry_result or retry_result.failed:
            if active_config.log_errors and isinstance(active_config.log_errors, LogEvent):
                process_log_after_request(active_config.log_errors, DefaultLogMessage.ERRORS, request_context, result)

            if isinstance(active_config.retry, GracefulRetry) and active_config.retry.behavior == "pass":
                must_break = False

        if must_break:
            raise UnexpectedResponse(str(result.url), result, strict_codes)

    allowed_pass = _check_allowed(active_config, result)
    if allowed_pass is False:
        retry_result = None
        must_break = True

        if active_config.should_retry(result.status_code):
            retry_result = await _gracefully_retry(
                report,
                throttle_controller,
                request,
                request_context,
                check_func=_check_allowed,
            )

        if not retry_result or retry_result.failed:
            if isinstance(active_config.log_errors, LogEvent):
                process_log_after_request(active_config.log_errors, DefaultLogMessage.ERRORS, request_context, result)

            if isinstance(active_config.retry, GracefulRetry) and active_config.retry.behavior == "pass":
                must_break = False

            if must_break:
                raise NonOkResponse(str(result.url), result)

    if active_config.parser and not isinstance(active_config.parser, Unset):
        default_fallback = active_config.parser.get("default", UNSET_VALUE)
        parse_result = active_config.parser.get(HTTPStatus(result.status_code), default_fallback)

        if not isinstance(parse_result, Unset):
            if isinstance(parse_result, type) and issubclass(parse_result, Exception):
                raise parse_result(request_context, result)
            elif callable(parse_result):
                return parse_result(result)
            else:
                return parse_result

    return result


class Gracy(Generic[Endpoint]):
    """Helper class that provides a standard way to create an Requester using
    inheritance.
    """

    _reporter: ReportBuilder = ReportBuilder()
    _throttle_controller: ThrottleController = ThrottleController()

    class Config:
        BASE_URL: str = ""
        REQUEST_TIMEOUT: float | None = None
        SETTINGS: GracyConfig = DEFAULT_CONFIG

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._base_config: GracyConfig = getattr(self.Config, "SETTINGS", DEFAULT_CONFIG)
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
        custom_config = custom_config_context.get()
        active_config = self._base_config
        if custom_config:
            active_config = GracyConfig.merge_config(self._base_config, custom_config)

        request_context = GracyRequestContext(
            method, str(self._client.base_url), endpoint, endpoint_args, active_config
        )

        graceful_request = _gracify(
            Gracy._reporter,
            Gracy._throttle_controller,
            GracefulRequest(
                self._client.request,
                request_context.method,
                request_context.endpoint,
                *args,
                **kwargs,
            ),
            request_context,
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
    def get_report(cls):
        return cls._reporter.build(cls._throttle_controller)

    @classmethod
    def report_status(cls, printer: PRINTERS):
        report = cls.get_report()
        print_report(report, printer)


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
