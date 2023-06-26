from __future__ import annotations

import asyncio
import logging
import typing as t
from asyncio import Lock
from collections import defaultdict
from datetime import datetime
from http import HTTPStatus

import httpx

from ._loggers import do_log, extract_base_format_args, extract_response_format_args
from ._models import GracyRequestContext, LogEvent
from ._reports._builders import ReportBuilder

logger = logging.getLogger("gracy")


class HttpHeaderRetryAfterBackOffHook:
    """
    Provides two method `before()` and `after()` to be used as hooks by Gracy.

    This hook checks for 429 (TOO MANY REQUESTS), and then reads the
    `retry-after` header (https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Retry-After).

    If the value is set, then Gracy pauses **ALL** client requests until the time is over.

    ### Retry
    Make sure you implement a proper retry logic, otherwise the 429 will break.

    ### Throttling
    It takes the reporter, because it counts every await as "throttled" for that UURL.

    ### Log Event
    You can optionally define a log event.
    It provides the response and the context, but also `RETRY_AFTER` that contains the header value.
    """

    DEFAULT_LOG_MESSAGE: t.Final = "[{METHOD}] {URL} requested to wait for {RETRY_AFTER}s"
    ALL_CLIENT_LOCK: t.Final = "CLIENT"

    def __init__(
        self, reporter: ReportBuilder, lock_per_endpoint: bool = False, log_event: LogEvent | None = None
    ) -> None:
        self._reporter = reporter
        self._lock_per_endpoint = lock_per_endpoint
        self._lock_manager = defaultdict[str, Lock](Lock)
        self._log_event = log_event

    def _process_log(self, request_context: GracyRequestContext, response: httpx.Response, retry_after: float) -> None:
        if event := self._log_event:
            format_args: t.Dict[str, str] = dict(
                **extract_base_format_args(request_context),
                **extract_response_format_args(response),
                RETRY_AFTER=str(retry_after),
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

    async def before(self, context: GracyRequestContext):
        lock_name = context.unformatted_url if self._lock_per_endpoint else self.ALL_CLIENT_LOCK

        while self._lock_manager[lock_name].locked():
            await asyncio.sleep(1)

    async def after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
    ):
        if isinstance(response_or_exc, httpx.Response) and response_or_exc.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            retry_after_seconds = self._parse_retry_after_as_seconds(response_or_exc)

            if retry_after_seconds > 0:
                lock_name = context.unformatted_url if self._lock_per_endpoint else self.ALL_CLIENT_LOCK

                async with self._lock_manager[lock_name]:
                    self._reporter.throttled(context)
                    self._process_log(context, response_or_exc, retry_after_seconds)
                    await asyncio.sleep(retry_after_seconds)
