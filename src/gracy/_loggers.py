import logging
import typing as t
from enum import Enum

import httpx

from ._models import GracefulRetryState, GracyRequestContext, LogEvent, ThrottleRule

logger = logging.getLogger("gracy")


class SafeDict(dict[str, str]):
    def __missing__(self, key: str):
        return "{" + key + "}"


class DefaultLogMessage(str, Enum):
    BEFORE = "Request on {URL} is ongoing"
    AFTER = "[{METHOD}] {URL} returned {STATUS}"
    ERRORS = "[{METHOD}] {URL} returned a bad status ({STATUS})"

    THROTTLE_HIT = "{URL} hit {THROTTLE_LIMIT} reqs/{THROTTLE_TIME_RANGE}"
    THROTTLE_DONE = "Done waiting {THROTTLE_TIME}s to hit {URL}"

    RETRY_BEFORE = (
        "GracefulRetry: {URL} will wait {RETRY_DELAY}s before next attempt ({CUR_ATTEMPT} out of {MAX_ATTEMPT})"
    )
    RETRY_AFTER = "GracefulRetry: {URL} replied {STATUS} attempt ({CUR_ATTEMPT} out of {MAX_ATTEMPT})"
    RETRY_EXHAUSTED = "GracefulRetry: {URL} exhausted the maximum attempts of {MAX_ATTEMPT}"


def _do_log(logevent: LogEvent, defaultmsg: str, format_args: dict[str, t.Any], response: httpx.Response | None = None):
    # Let's protect ourselves against potential customizations with undefined {key}
    safe_format_args = SafeDict(**format_args)

    if logevent.custom_message:
        if isinstance(logevent.custom_message, str):
            message = logevent.custom_message.format_map(safe_format_args)
        else:
            message = logevent.custom_message(response).format_map(safe_format_args)
    else:
        message = defaultmsg.format_map(safe_format_args)

    logger.log(logevent.level, message, extra=format_args)


def _extract_base_format_args(request_context: GracyRequestContext) -> dict[str, str]:
    return dict(
        URL=request_context.url,
        ENDPOINT=request_context.endpoint,
        UURL=request_context.unformatted_url,
        UENDPOINT=request_context.unformatted_endpoint,
        METHOD=request_context.method,
    )


def _extract_response_format_args(response: httpx.Response | None) -> dict[str, str]:
    status_code = response.status_code if response else "ABORTED"
    elapsed = response.elapsed if response else "UNKNOWN"

    return dict(
        STATUS=str(status_code),
        ELAPSED=str(elapsed),
    )


def process_log_before_request(logevent: LogEvent, request_context: GracyRequestContext) -> None:
    format_args = _extract_base_format_args(request_context)
    _do_log(logevent, DefaultLogMessage.BEFORE, format_args)


def process_log_throttle(
    logevent: LogEvent,
    default_message: str,
    await_time: float,
    rule: ThrottleRule,
    request_context: GracyRequestContext,
):
    format_args = dict(
        **_extract_base_format_args(request_context),
        THROTTLE_TIME=await_time,
        THROTTLE_LIMIT=rule.max_requests,
        THROTTLE_TIME_RANGE=rule.readable_time_range,
    )

    _do_log(logevent, default_message, format_args)


def process_log_retry(
    logevent: LogEvent,
    defaultmsg: str,
    request_context: GracyRequestContext,
    state: GracefulRetryState,
    response: httpx.Response | None = None,
):
    maybe_response_args: dict[str, str] = {}
    if response:
        maybe_response_args = _extract_response_format_args(response)

    format_args = dict(
        **_extract_base_format_args(request_context),
        **maybe_response_args,
        RETRY_DELAY=state.delay,
        CUR_ATTEMPT=state.cur_attempt,
        MAX_ATTEMPT=state.max_attempts,
    )

    _do_log(logevent, defaultmsg, format_args, response)


def process_log_after_request(
    logevent: LogEvent,
    defaultmsg: str,
    request_context: GracyRequestContext,
    response: httpx.Response | None,
) -> None:
    format_args: dict[str, str] = dict(
        **_extract_base_format_args(request_context),
        **_extract_response_format_args(response),
    )

    _do_log(logevent, defaultmsg, format_args, response)
