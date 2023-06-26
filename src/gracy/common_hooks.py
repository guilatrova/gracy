from __future__ import annotations

import asyncio
import typing as t
from asyncio import Lock
from http import HTTPStatus

import httpx

from ._loggers import do_log, extract_base_format_args, extract_response_format_args
from ._models import GracyRequestContext, LogEvent
from ._reports._builders import ReportBuilder


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

    def __init__(self, reporter: ReportBuilder, log_event: LogEvent | None = None) -> None:
        self._reporter = reporter
        self._lock = Lock()
        self._log_event = log_event

    def _process_log(self, request_context: GracyRequestContext, response: httpx.Response, retry_after: int) -> None:
        if event := self._log_event:
            format_args: t.Dict[str, str] = dict(
                **extract_base_format_args(request_context),
                **extract_response_format_args(response),
                RETRY_AFTER=str(retry_after),
            )

            do_log(event, self.DEFAULT_LOG_MESSAGE, format_args, response)

    async def before(self):
        while self._lock.locked():
            await asyncio.sleep(1)

    async def after(
        self,
        context: GracyRequestContext,
        response_or_exc: httpx.Response | Exception,
    ):
        if isinstance(response_or_exc, httpx.Response) and response_or_exc.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            retry_after = int(response_or_exc.headers.get("retry-after", 0))
            # TODO: This might be a date

            if retry_after > 0:
                async with self._lock:
                    self._reporter.throttled(context)
                    self._process_log(context, response_or_exc, retry_after)
                    await asyncio.sleep(retry_after)
