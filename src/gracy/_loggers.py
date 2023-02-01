import logging
from datetime import timedelta
from enum import Enum
from typing import Any

import httpx

from gracy._models import GracefulRetryState, LogEvent, ThrottleRule

logger = logging.getLogger("gracy")


class DefaultLogMessage(str, Enum):
    BEFORE = "Request on {URL} is ongoing"
    AFTER = "[{METHOD}] {URL} returned {STATUS}"
    ERRORS = "[{METHOD}] {URL} returned a bad status ({STATUS})"

    THROTTLE_HIT = "{URL} hit {THROTTLE_LIMIT} reqs/s"
    THROTTLE_DONE = "Done waiting {THROTTLE_TIME}s to hit {URL}"

    RETRY_BEFORE = (
        "GracefulRetry: {URL} will wait {RETRY_DELAY}s before next attempt ({CUR_ATTEMPT} out of {MAX_ATTEMPT})"
    )
    RETRY_AFTER = "GracefulRetry: {URL} exhausted the maximum attempts of {MAX_ATTEMPT})"
    RETRY_EXHAUSTED = "GracefulRetry: {URL} exhausted the maximum attempts of {MAX_ATTEMPT})"


def _do_log(logevent: LogEvent, defaultmsg: str, format_args: dict[str, Any]):
    if logevent.custom_message:
        message = logevent.custom_message.format(**format_args)
    else:
        message = defaultmsg.format(**format_args)

    logger.log(logevent.level, message, extra=format_args)


def process_log_before_request(logevent: LogEvent, url: str):
    format_args = dict(URL=url)
    _do_log(logevent, DefaultLogMessage.BEFORE, format_args)


def process_log_throttle(
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

    _do_log(logevent, default_message, format_args)


def process_log_retry(logevent: LogEvent, defaultmsg: str, url: str, state: GracefulRetryState):
    format_args = dict(
        URL=url,
        RETRY_DELAY=state.delay,
        CUR_ATTEMPT=state.cur_attempt,
        MAX_ATTEMPT=state.max_attempts,
    )

    _do_log(logevent, defaultmsg, format_args)


def process_log(logevent: LogEvent, defaultmsg: str, response: httpx.Response, elapsed: timedelta):
    format_args = dict(
        URL=response.request.url,
        METHOD=response.request.method,
        STATUS=response.status_code,
        ELAPSED=elapsed,
    )

    _do_log(logevent, defaultmsg, format_args)
