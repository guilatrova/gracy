from __future__ import annotations

import logging
import typing as t
from enum import Enum

import httpx

from ._models import GracefulRetryState, GracyRequestContext, LogEvent, ThrottleRule

logger = logging.getLogger("gracy")


def is_replay(resp: httpx.Response) -> bool:
    return getattr(resp, "_gracy_replayed", False)


class SafeDict(t.Dict[str, str]):
    def __missing__(self, key: str):
        return "{" + key + "}"


class DefaultLogMessage(str, Enum):
    BEFORE = "Request on {URL} is ongoing"
    AFTER = "{REPLAY}[{METHOD}] {URL} returned {STATUS}"
    ERRORS = "[{METHOD}] {URL} returned a bad status ({STATUS})"

    THROTTLE_HIT = "{URL} hit {THROTTLE_LIMIT} reqs/{THROTTLE_TIME_RANGE}"
    THROTTLE_DONE = "Done waiting {THROTTLE_TIME}s to hit {URL}"

    RETRY_BEFORE = (
        "GracefulRetry: {URL} will wait {RETRY_DELAY}s before next attempt due to "
        "{RETRY_CAUSE} ({CUR_ATTEMPT} out of {MAX_ATTEMPT})"
    )
    RETRY_AFTER = "GracefulRetry: {URL} replied {STATUS} ({CUR_ATTEMPT} out of {MAX_ATTEMPT})"
    RETRY_EXHAUSTED = "GracefulRetry: {URL} exhausted the maximum attempts of {MAX_ATTEMPT} due to {RETRY_CAUSE}"

    REPLAY_RECORDED = "Gracy Replay: Recorded {RECORDED_COUNT} requests"
    REPLAY_REPLAYED = "Gracy Replay: Replayed {REPLAYED_COUNT} requests"

    CONCURRENT_REQUEST_LIMIT_HIT = "{UURL} hit {CONCURRENT_REQUESTS} ongoing concurrent requests"
    CONCURRENT_REQUEST_LIMIT_FREED = "{UURL} concurrency has been freed at {CONCURRENCY_CAP}"


def do_log(logevent: LogEvent, defaultmsg: str, format_args: dict[str, t.Any], response: httpx.Response | None = None):
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


def extract_base_format_args(request_context: GracyRequestContext) -> dict[str, str]:
    return dict(
        URL=request_context.url,
        ENDPOINT=request_context.endpoint,
        UURL=request_context.unformatted_url,
        UENDPOINT=request_context.unformatted_endpoint,
        METHOD=request_context.method,
    )


def extract_response_format_args(response: httpx.Response | None) -> dict[str, str]:
    status_code = response.status_code if response else "ABORTED"
    elapsed = response.elapsed if response else "UNKNOWN"

    if response and is_replay(response):
        replayed = "TRUE"
        replayed_str = "REPLAYED"
    else:
        replayed = "FALSE"
        replayed_str = ""

    return dict(
        STATUS=str(status_code),
        ELAPSED=str(elapsed),
        IS_REPLAY=replayed,
        REPLAY=replayed_str,
    )


def process_log_before_request(logevent: LogEvent, request_context: GracyRequestContext) -> None:
    format_args = extract_base_format_args(request_context)
    do_log(logevent, DefaultLogMessage.BEFORE, format_args)


def process_log_throttle(
    logevent: LogEvent,
    default_message: str,
    await_time: float,
    rule: ThrottleRule,
    request_context: GracyRequestContext,
):
    format_args = dict(
        **extract_base_format_args(request_context),
        THROTTLE_TIME=await_time,
        THROTTLE_LIMIT=rule.max_requests,
        THROTTLE_TIME_RANGE=rule.readable_time_range,
    )

    do_log(logevent, default_message, format_args)


def process_log_retry(
    logevent: LogEvent,
    defaultmsg: str,
    request_context: GracyRequestContext,
    state: GracefulRetryState,
    response: httpx.Response | None = None,
):
    maybe_response_args: dict[str, str] = {}
    if response:
        maybe_response_args = extract_response_format_args(response)

    format_args = dict(
        **extract_base_format_args(request_context),
        **maybe_response_args,
        RETRY_DELAY=state.delay,
        RETRY_CAUSE=state.cause,
        CUR_ATTEMPT=state.cur_attempt,
        MAX_ATTEMPT=state.max_attempts,
    )

    do_log(logevent, defaultmsg, format_args, response)


def process_log_after_request(
    logevent: LogEvent,
    defaultmsg: str,
    request_context: GracyRequestContext,
    response: httpx.Response | None,
) -> None:
    format_args: dict[str, str] = dict(
        **extract_base_format_args(request_context),
        **extract_response_format_args(response),
    )

    do_log(logevent, defaultmsg, format_args, response)


def process_log_concurrency_limit(logevent: LogEvent, count: int, request_context: GracyRequestContext):
    format_args: t.Dict[str, str] = dict(
        CONCURRENT_REQUESTS=f"{count:,}",
        **extract_base_format_args(request_context),
    )

    do_log(logevent, DefaultLogMessage.CONCURRENT_REQUEST_LIMIT_HIT, format_args)


def process_log_concurrency_freed(logevent: LogEvent, request_context: GracyRequestContext, cur_cap: float):
    format_args: t.Dict[str, str] = dict(
        CONCURRENCY_CAP=f"{cur_cap:.2f}%",
        **extract_base_format_args(request_context),
    )
    do_log(logevent, DefaultLogMessage.CONCURRENT_REQUEST_LIMIT_FREED, format_args)
