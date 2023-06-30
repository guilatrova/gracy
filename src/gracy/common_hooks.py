from __future__ import annotations

import asyncio
import logging
import typing as t
from asyncio import Lock
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus

import httpx

from ._loggers import do_log, extract_base_format_args, extract_response_format_args
from ._models import GracyRequestContext, LogEvent
from ._reports._builders import ReportBuilder
from .replays.storages._base import is_replay

logger = logging.getLogger("gracy")


@dataclass
class HookResult:
    executed: bool
    awaited: float = 0
    dry_run: bool = False


class HttpHeaderRetryAfterBackOffHook:
    """
    Provides two methods `before()` and `after()` to be used as hooks by Gracy.

    This hook checks for 429 (TOO MANY REQUESTS), and then reads the
    `retry-after` header (https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Retry-After).

    If the value is set, then Gracy pauses **ALL** client requests until the time is over.
    This behavior can be modified to happen on a per-endpoint basis if `lock_per_endpoint` is True.

    ### ⚠️ Retry
    This doesn't replace `GracefulRetry`.
    Make sure you implement a proper retry logic, otherwise the 429 will break the client.

    ### Throttling
    If you pass the reporter, it will count every await as "throttled" for that UURL.

    ### Processor
    You can optionally pass in a lambda, that can be used to modify/increase the wait time from the header.

    ### Log Event
    You can optionally define a log event.
    It provides the response and the context, but also `RETRY_AFTER` that contains the header value.
    `RETRY_AFTER_ACTUAL_WAIT` is also available in case you modify the original value.
    """

    DEFAULT_LOG_MESSAGE: t.Final = "[{METHOD}] {URL} requested to wait for {RETRY_AFTER}s"
    ALL_CLIENT_LOCK: t.Final = "CLIENT"

    def __init__(
        self,
        reporter: ReportBuilder | None = None,
        lock_per_endpoint: bool = False,
        log_event: LogEvent | None = None,
        seconds_processor: None | t.Callable[[float], float] = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self._reporter = reporter
        self._lock_per_endpoint = lock_per_endpoint
        self._lock_manager = t.DefaultDict[str, Lock](Lock)
        self._log_event = log_event
        self._processor = seconds_processor or (lambda x: x)
        self._dry_run = dry_run

    def _process_log(
        self, request_context: GracyRequestContext, response: httpx.Response, retry_after: float, actual_wait: float
    ) -> None:
        if event := self._log_event:
            format_args: t.Dict[str, str] = dict(
                **extract_base_format_args(request_context),
                **extract_response_format_args(response),
                RETRY_AFTER=str(retry_after),
                RETRY_AFTER_ACTUAL_WAIT=str(actual_wait),
            )

            do_log(event, self.DEFAULT_LOG_MESSAGE, format_args, response)

    def _parse_retry_after_as_seconds(self, response: httpx.Response) -> float:
        retry_after_value = response.headers.get("retry-after")

        if retry_after_value is None:
            return 0

        if retry_after_value.isdigit():
            return int(retry_after_value)

        try:
            # It might be a date as: Wed, 21 Oct 2015 07:28:00 GMT
            date_time = datetime.strptime(retry_after_value, "%a, %d %b %Y %H:%M:%S %Z")
            date_as_seconds = (date_time - datetime.now()).total_seconds()

        except Exception:
            logger.exception(f"Unable to parse {retry_after_value} as date within {type(self).__name__}")
            return 0

        else:
            return date_as_seconds

    async def before(self, context: GracyRequestContext) -> HookResult:
        return HookResult(False)

    async def after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
    ) -> HookResult:
        if isinstance(response_or_exc, httpx.Response) and response_or_exc.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            if is_replay(response_or_exc):
                return HookResult(executed=False, dry_run=self._dry_run)

            retry_after_seconds = self._parse_retry_after_as_seconds(response_or_exc)
            actual_wait = self._processor(retry_after_seconds)

            if retry_after_seconds > 0:
                lock_name = context.unformatted_url if self._lock_per_endpoint else self.ALL_CLIENT_LOCK

                async with self._lock_manager[lock_name]:
                    self._process_log(context, response_or_exc, retry_after_seconds, actual_wait)

                    if self._reporter:
                        self._reporter.throttled(context)

                    if self._dry_run is False:
                        await asyncio.sleep(actual_wait)

                    return HookResult(True, actual_wait, self._dry_run)

        return HookResult(False, dry_run=self._dry_run)


class RateLimitBackOffHook:
    """
    Provides two methods `before()` and `after()` to be used as hooks by Gracy.

    This hook checks for 429 (TOO MANY REQUESTS) and locks requests for an arbitrary amount of time defined by you.

    If the value is set, then Gracy pauses **ALL** client requests until the time is over.
    This behavior can be modified to happen on a per-endpoint basis if `lock_per_endpoint` is True.

    ### ⚠️ Retry
    This doesn't replace `GracefulRetry`.
    Make sure you implement a proper retry logic, otherwise the 429 will break the client.

    ### Throttling
    If you pass the reporter, it will count every await as "throttled" for that UURL.

    ### Log Event
    You can optionally define a log event.
    It provides the response and the context, but also `WAIT_TIME` that contains the wait value.
    """

    DEFAULT_LOG_MESSAGE: t.Final = "[{METHOD}] {UENDPOINT} got rate limited, waiting for {WAIT_TIME}s"
    ALL_CLIENT_LOCK: t.Final = "CLIENT"

    def __init__(
        self,
        delay: float,
        reporter: ReportBuilder | None = None,
        lock_per_endpoint: bool = False,
        log_event: LogEvent | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self._reporter = reporter
        self._lock_per_endpoint = lock_per_endpoint
        self._lock_manager = t.DefaultDict[str, Lock](Lock)
        self._log_event = log_event
        self._delay = delay
        self._dry_run = dry_run

    def _process_log(self, request_context: GracyRequestContext, response: httpx.Response) -> None:
        if event := self._log_event:
            format_args: t.Dict[str, str] = dict(
                **extract_base_format_args(request_context),
                **extract_response_format_args(response),
                WAIT_TIME=str(self._delay),
            )

            do_log(event, self.DEFAULT_LOG_MESSAGE, format_args, response)

    async def before(self, context: GracyRequestContext) -> HookResult:
        return HookResult(False)

    async def after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
    ) -> HookResult:
        if isinstance(response_or_exc, httpx.Response) and response_or_exc.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            if is_replay(response_or_exc):
                return HookResult(executed=False, dry_run=self._dry_run)

            lock_name = context.unformatted_url if self._lock_per_endpoint else self.ALL_CLIENT_LOCK

            async with self._lock_manager[lock_name]:
                self._process_log(context, response_or_exc)

                if self._reporter:
                    self._reporter.throttled(context)

                if self._dry_run is False:
                    await asyncio.sleep(self._delay)

                return HookResult(True, self._delay, self._dry_run)

        return HookResult(False, dry_run=self._dry_run)
