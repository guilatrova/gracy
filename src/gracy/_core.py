from __future__ import annotations

import asyncio
import logging
import sys
import typing as t
from asyncio import sleep
from http import HTTPStatus

import httpx

from gracy.replays._wrappers import record_mode, replay_mode, smart_replay_mode

from ._configs import custom_config_context, custom_gracy_config, within_hook, within_hook_context
from ._general import extract_request_kwargs
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
    GracefulValidator,
    GracyConfig,
    GracyRequestContext,
    LogEvent,
    ThrottleController,
    ThrottleRule,
    Unset,
)
from ._reports._builders import ReportBuilder
from ._reports._printers import PRINTERS, print_report
from ._validators import AllowedStatusValidator, DefaultValidator, StrictStatusValidator
from .exceptions import GracyParseFailed, GracyRequestFailed
from .replays.storages._base import GracyReplay

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec


logger = logging.getLogger("gracy")


ANY_COROUTINE = t.Coroutine[t.Any, t.Any, t.Any]

P = ParamSpec("P")
GRACEFUL_T = t.TypeVar("GRACEFUL_T", bound=ANY_COROUTINE)
GRACEFUL_GEN_T = t.TypeVar("GRACEFUL_GEN_T", bound=t.AsyncGenerator[t.Any, t.Any])
BEFORE_HOOK_TYPE = t.Callable[[GracyRequestContext], t.Awaitable[None]]
AFTER_HOOK_TYPE = t.Callable[
    [GracyRequestContext, t.Union[httpx.Response, Exception], t.Optional[GracefulRetryState]], t.Awaitable[None]
]


async def _gracefully_throttle(
    report: ReportBuilder, controller: ThrottleController, request_context: GracyRequestContext
):
    if isinstance(request_context.active_config.throttling, Unset):
        return

    if throttling := request_context.active_config.throttling:
        has_been_throttled = True

        while has_been_throttled:
            wait_per_rule: list[tuple[ThrottleRule, float]] = [
                (rule, wait_time)
                for rule in throttling.rules
                if (wait_time := rule.calculate_await_time(controller)) > 0.0
            ]

            if wait_per_rule:
                rule, await_time = max(wait_per_rule, key=lambda x: x[1])
                if THROTTLE_LOCKER.is_rule_throttled(rule):
                    report.throttled(request_context)
                    await asyncio.sleep(await_time)
                    continue

                with THROTTLE_LOCKER.lock_rule(rule):
                    if throttling.log_limit_reached:
                        process_log_throttle(
                            throttling.log_limit_reached,
                            DefaultLogMessage.THROTTLE_HIT,
                            await_time,
                            rule,
                            request_context,
                        )

                    report.throttled(request_context)
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
    last_response: httpx.Response | None,
    before_hook: BEFORE_HOOK_TYPE,
    after_hook: AFTER_HOOK_TYPE,
    request: GracefulRequest,
    request_context: GracyRequestContext,
    validators: list[GracefulValidator],
) -> GracefulRetryState:
    config = request_context.active_config
    retry = t.cast(GracefulRetry, config.retry)
    state = retry.create_state(last_response)

    response = None
    resulting_exc: Exception | None = None

    failing = True
    while failing:
        state.increment(response)
        if state.cant_retry:
            break

        if retry.log_before:
            process_log_retry(retry.log_before, DefaultLogMessage.RETRY_BEFORE, request_context, state)

        await sleep(state.delay)
        await _gracefully_throttle(report, throttle_controller, request_context)
        throttle_controller.init_request(request_context)

        try:
            await before_hook(request_context)
            response = await request()

        except Exception as request_err:
            resulting_exc = GracyRequestFailed(request_context, request_err)
            report.track(request_context, request_err)
            await after_hook(request_context, request_err, state)

        else:
            report.track(request_context, response)
            await after_hook(request_context, response, state)

        finally:
            report.retried(request_context)

        if response:
            resulting_exc = None

            for validator in validators:
                try:
                    validator.check(response)
                except Exception as ex:
                    resulting_exc = ex
                    break

        # Even if all validators are passing, we check whether
        # it should retry for cases like:
        # e.g. Allow = 404 (so it's a success),
        #      but Retry it up to 3 times to see whether it becomes 200
        if config.should_retry(response, resulting_exc) is False:
            state.success = True
            failing = False

        if retry.log_after:
            process_log_retry(retry.log_after, DefaultLogMessage.RETRY_AFTER, request_context, state, response)

    if response:  # Unlikely to be None
        state.final_response = response

    state.final_validation_exc = resulting_exc

    if state.cant_retry and config.should_retry(state.final_response, resulting_exc) and retry.log_exhausted:
        process_log_retry(retry.log_exhausted, DefaultLogMessage.RETRY_EXHAUSTED, request_context, state, response)

    return state


def _maybe_parse_result(active_config: GracyConfig, request_context: GracyRequestContext, result: httpx.Response):
    if active_config.parser and not isinstance(active_config.parser, Unset):
        default_fallback = active_config.parser.get("default", UNSET_VALUE)
        parse_result = active_config.parser.get(HTTPStatus(result.status_code), default_fallback)

        if not isinstance(parse_result, Unset):
            if isinstance(parse_result, type) and issubclass(parse_result, Exception):
                raise parse_result(request_context, result)

            elif callable(parse_result):
                try:
                    return parse_result(result)
                except Exception as ex:
                    raise GracyParseFailed(result) from ex

            else:
                return parse_result

    return result


async def _gracify(
    report: ReportBuilder,
    throttle_controller: ThrottleController,
    replay: GracyReplay | None,
    before_hook: BEFORE_HOOK_TYPE,
    after_hook: AFTER_HOOK_TYPE,
    request: GracefulRequest,
    request_context: GracyRequestContext,
):
    active_config = request_context.active_config

    if isinstance(active_config.log_request, LogEvent):
        process_log_before_request(active_config.log_request, request_context)

    resulting_exc: Exception | None = None

    do_throttle = True
    if replay and replay.disable_throttling:
        replay_available = await replay.has_replay(request.request)
        if replay_available:
            do_throttle = False

    if do_throttle:
        await _gracefully_throttle(report, throttle_controller, request_context)
        throttle_controller.init_request(request_context)

    try:
        await before_hook(request_context)
        response = await request()
    except Exception as request_err:
        resulting_exc = GracyRequestFailed(request_context, request_err)
        response = None
        report.track(request_context, resulting_exc)
        await after_hook(request_context, resulting_exc, None)
    else:
        # mypy didn't detect it properly
        response = t.cast(httpx.Response, response)  # type: ignore
        report.track(request_context, response)
        await after_hook(request_context, response, None)

    if active_config.log_response and isinstance(active_config.log_response, LogEvent):
        process_log_after_request(active_config.log_response, DefaultLogMessage.AFTER, request_context, response)

    validators: list[GracefulValidator] = []
    if active_config.strict_status_code and not isinstance(active_config.strict_status_code, Unset):
        validators.append(StrictStatusValidator(active_config.strict_status_code))
    elif active_config.allowed_status_code and not isinstance(active_config.allowed_status_code, Unset):
        validators.append(AllowedStatusValidator(active_config.allowed_status_code))
    else:
        validators.append(DefaultValidator())

    if isinstance(active_config.validators, GracefulValidator):
        validators.append(active_config.validators)
    elif isinstance(active_config.validators, t.Iterable):
        validators += active_config.validators

    if response:
        for validator in validators:
            try:
                validator.check(response)
            except Exception as ex:
                resulting_exc = ex
                break

    retry_result: GracefulRetryState | None = None
    if active_config.should_retry(response, resulting_exc):
        retry_result = await _gracefully_retry(
            report,
            throttle_controller,
            response,
            before_hook,
            after_hook,
            request,
            request_context,
            validators,
        )

        response = retry_result.final_response
        resulting_exc = retry_result.final_validation_exc

    did_request_fail = bool(resulting_exc)
    if did_request_fail:
        if active_config.log_errors and isinstance(active_config.log_errors, LogEvent):
            process_log_after_request(active_config.log_errors, DefaultLogMessage.ERRORS, request_context, response)

    must_break = True
    if isinstance(active_config.retry, GracefulRetry) and active_config.retry.behavior == "pass":
        must_break = False

    if resulting_exc and must_break:
        raise resulting_exc

    final_result = _maybe_parse_result(active_config, request_context, response) if response else None

    return final_result


DISABLED_GRACY_CONFIG: t.Final = GracyConfig(
    strict_status_code=None,
    allowed_status_code=None,
    validators=None,
    retry=None,
    log_request=None,
    log_response=None,
    log_errors=None,
    parser=None,
)


class Gracy(t.Generic[Endpoint]):
    """Helper class that provides a standard way to create an Requester using
    inheritance.
    """

    _reporter: ReportBuilder = ReportBuilder()
    _throttle_controller: ThrottleController = ThrottleController()

    class Config:
        BASE_URL: str = ""
        REQUEST_TIMEOUT: float | None = None
        SETTINGS: GracyConfig = DEFAULT_CONFIG

    def __init__(self, replay: GracyReplay | None = None, DEBUG_ENABLED: bool = False, **kwargs: t.Any) -> None:
        self.DEBUG_ENABLED = DEBUG_ENABLED
        self._base_config = t.cast(GracyConfig, getattr(self.Config, "SETTINGS", DEFAULT_CONFIG))
        self._client = self._create_client(**kwargs)
        self.replays = replay

        if replay:
            replay.storage.prepare()

    def _create_client(self, **kwargs: t.Any) -> httpx.AsyncClient:
        base_url = getattr(self.Config, "BASE_URL", "")
        request_timeout = getattr(self.Config, "REQUEST_TIMEOUT", None)
        return httpx.AsyncClient(base_url=base_url, timeout=request_timeout)

    async def _request(
        self,
        method: str,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        custom_config = custom_config_context.get()
        active_config = self._base_config
        if custom_config:
            active_config = GracyConfig.merge_config(self._base_config, custom_config)

        if self.DEBUG_ENABLED:
            logger.debug(f"Active Config for {endpoint}: {active_config}")

        request_context = GracyRequestContext(
            method, str(self._client.base_url), endpoint, endpoint_args, active_config
        )

        httpx_request_func = self._client.request
        if replays := self.replays:
            if replays.mode == "record":
                httpx_request_func = record_mode(replays, httpx_request_func)
            elif replays.mode == "replay":
                httpx_request_func = replay_mode(replays, self._client, httpx_request_func)
            else:
                httpx_request_func = smart_replay_mode(replays, self._client, httpx_request_func)

        request_kwargs = extract_request_kwargs(kwargs)
        request = self._client.build_request(request_context.method, request_context.endpoint, **request_kwargs)

        graceful_request = _gracify(
            Gracy._reporter,
            Gracy._throttle_controller,
            replays,
            self._before,
            self._after,
            GracefulRequest(
                request,
                httpx_request_func,
                request_context.method,
                request_context.endpoint,
                *args,
                **kwargs,
            ),
            request_context,
        )

        return await graceful_request

    async def before(self, context: GracyRequestContext):
        ...

    async def _before(self, context: GracyRequestContext):
        if within_hook_context.get():
            return

        with custom_gracy_config(DISABLED_GRACY_CONFIG), within_hook():
            try:
                await self.before(context)
            except Exception:
                logger.exception("Gracy before hook raised an unexpected exception")

    async def after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
        retry_state: GracefulRetryState | None,
    ):
        ...

    async def _after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
        retry_state: GracefulRetryState | None,
    ):
        if within_hook_context.get():
            return

        with custom_gracy_config(DISABLED_GRACY_CONFIG), within_hook():
            try:
                await self.after(context, response_or_exc, retry_state)
            except Exception:
                logger.exception("Gracy after hook raised an unexpected exception")

    async def get(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        return await self._request("GET", endpoint, endpoint_args, *args, **kwargs)

    async def post(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        return await self._request("POST", endpoint, endpoint_args, *args, **kwargs)

    async def put(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        return await self._request("PUT", endpoint, endpoint_args, *args, **kwargs)

    async def patch(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        return await self._request("PATCH", endpoint, endpoint_args, *args, **kwargs)

    async def delete(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        return await self._request("DELETE", endpoint, endpoint_args, *args, **kwargs)

    async def head(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        return await self._request("HEAD", endpoint, endpoint_args, *args, **kwargs)

    async def options(
        self,
        endpoint: Endpoint | str,
        endpoint_args: dict[str, str] | None = None,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        return await self._request("OPTIONS", endpoint, endpoint_args, *args, **kwargs)

    def get_report(self):
        return self._reporter.build(self._throttle_controller, self.replays)

    def report_status(self, printer: PRINTERS):
        report = self.get_report()
        print_report(report, printer)

    @classmethod
    def dangerously_reset_report(cls):
        """
        Doing this will reset throttling rules and metrics.
        So be sure you know what you're doing.
        """
        cls._throttle_controller = ThrottleController()
        cls._reporter = ReportBuilder()


def graceful(
    strict_status_code: t.Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE,
    allowed_status_code: t.Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE,
    validators: t.Iterable[GracefulValidator] | GracefulValidator | None | Unset = UNSET_VALUE,
    retry: GracefulRetry | Unset | None = UNSET_VALUE,
    log_request: LOG_EVENT_TYPE = UNSET_VALUE,
    log_response: LOG_EVENT_TYPE = UNSET_VALUE,
    log_errors: LOG_EVENT_TYPE = UNSET_VALUE,
    parser: PARSER_TYPE = UNSET_VALUE,
):
    config = GracyConfig(
        strict_status_code=strict_status_code,
        allowed_status_code=allowed_status_code,
        validators=validators,
        retry=retry,
        log_request=log_request,
        log_response=log_response,
        log_errors=log_errors,
        parser=parser,
    )

    def _wrapper(wrapped_function: t.Callable[P, GRACEFUL_T]) -> t.Callable[P, GRACEFUL_T]:
        async def _inner_wrapper(*args: P.args, **kwargs: P.kwargs):
            with custom_gracy_config(config):
                res = await wrapped_function(*args, **kwargs)
                return res

        return t.cast(t.Callable[P, GRACEFUL_T], _inner_wrapper)

    return _wrapper


def graceful_generator(
    strict_status_code: t.Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE,
    allowed_status_code: t.Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE,
    validators: t.Iterable[GracefulValidator] | GracefulValidator | None | Unset = UNSET_VALUE,
    retry: GracefulRetry | Unset | None = UNSET_VALUE,
    log_request: LOG_EVENT_TYPE = UNSET_VALUE,
    log_response: LOG_EVENT_TYPE = UNSET_VALUE,
    log_errors: LOG_EVENT_TYPE = UNSET_VALUE,
    parser: PARSER_TYPE = UNSET_VALUE,
):
    config = GracyConfig(
        strict_status_code=strict_status_code,
        allowed_status_code=allowed_status_code,
        validators=validators,
        retry=retry,
        log_request=log_request,
        log_response=log_response,
        log_errors=log_errors,
        parser=parser,
    )

    def _wrapper(wrapped_function: t.Callable[P, GRACEFUL_GEN_T]) -> t.Callable[P, GRACEFUL_GEN_T]:
        async def _inner_wrapper(*args: P.args, **kwargs: P.kwargs):
            with custom_gracy_config(config):
                async for res in wrapped_function(*args, **kwargs):
                    yield res

        return t.cast(t.Callable[P, GRACEFUL_GEN_T], _inner_wrapper)

    return _wrapper
