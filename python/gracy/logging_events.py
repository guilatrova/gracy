"""Log templating: SafeDict placeholders + the event emitter used by pipeline.py.

Emission is synchronous and lossless - it happens inline in the Python
pipeline (no broadcast channel, nothing dropped). An UNSET/None LogEvent
means "don't log this event" and costs one attribute lookup.

Placeholder matrix (see GracyConfig / LogEvent docs):
    context     -> {URL} {UURL} {METHOD} {ENDPOINT} + UPPERCASE endpoint args
    response    -> {STATUS} {ELAPSED} {IS_REPLAY} {REPLAY}
    retry_state -> {RETRY_ATTEMPT} {MAX_ATTEMPTS} {RETRY_DELAY} {RETRY_CAUSE}
    extra       -> merged last, wins on collision
Missing sources contribute nothing; unknown placeholders render literally.
"""

from __future__ import annotations

import logging
import typing as t

from gracy._types import RequestContext, Response, RetryState, Unset
from gracy.config import GracyConfig, LogEvent

__all__ = ["SafeDict", "safe_format", "build_placeholders", "make_emitter"]


class SafeDict(dict):  # noqa: FURB189 - str.format_map requires a real dict subclass
    """dict whose missing keys render as the literal placeholder: '{KEY}'."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def safe_format(template: str, values: t.Mapping[str, t.Any]) -> str:
    """format_map that never raises: unknown keys stay literal, malformed
    templates fall back to the template unchanged."""
    try:
        return template.format_map(SafeDict(values))
    except (ValueError, KeyError):
        return template


def build_placeholders(
    context: RequestContext | None = None,
    response: Response | None = None,
    retry_state: RetryState | None = None,
    extra: t.Mapping[str, t.Any] | None = None,
) -> dict[str, t.Any]:
    """Assemble the typed placeholder matrix. Missing sources contribute nothing."""
    values: dict[str, t.Any] = {}

    if context is not None:
        values["URL"] = context.url
        values["UURL"] = context.uurl
        values["METHOD"] = context.method
        values["ENDPOINT"] = context.endpoint
        for key, arg in context.endpoint_args.items():
            values[key.upper()] = arg

    if response is not None:
        values["STATUS"] = response.status
        elapsed = context.elapsed if context is not None and context.elapsed is not None else response.elapsed
        values["ELAPSED"] = f"{elapsed:.3f}s"
        values["IS_REPLAY"] = response.is_replay
        values["REPLAY"] = "REPLAYED" if response.is_replay else ""

    if retry_state is not None:
        values["RETRY_ATTEMPT"] = retry_state.attempt
        values["MAX_ATTEMPTS"] = retry_state.max_attempts
        values["RETRY_DELAY"] = f"{retry_state.delay:.2f}"
        values["RETRY_CAUSE"] = retry_state.cause

    if extra:
        values.update(extra)

    return values


_DEFAULT_MESSAGES: t.Final[dict[str, str]] = {
    "request": "Request on {METHOD} {URL}",
    "response": "[{METHOD}] {URL} returned {STATUS} in {ELAPSED}",
    "error": "[{METHOD}] {URL} FAILED ({STATUS})",
    "retry_before": "Retry {RETRY_ATTEMPT}/{MAX_ATTEMPTS} for {URL} in {RETRY_DELAY}s ({RETRY_CAUSE})",
    "retry_after": "Retry {RETRY_ATTEMPT}/{MAX_ATTEMPTS} for {URL} finished ({STATUS})",
    "retry_exhausted": "GAVE UP retrying {URL} after {MAX_ATTEMPTS} attempts",
    "throttle": "THROTTLE hit for {URL}",
    "throttle_over": "Throttle wait over for {URL}",
}


def _resolve_log_event(event: str, config: GracyConfig) -> LogEvent | None:
    """Map an event name to its configured LogEvent; None means 'do not log'."""
    candidate: t.Any = None

    if event == "request":
        candidate = config.log_request
    elif event == "response":
        candidate = config.log_response
    elif event == "error":
        candidate = config.log_errors
    elif event in ("retry_before", "retry_after", "retry_exhausted"):
        retry = config.retry
        if retry is None or isinstance(retry, Unset):
            return None
        if event == "retry_before":
            candidate = retry.log_before
        elif event == "retry_after":
            candidate = retry.log_after
        else:
            candidate = retry.log_exhausted
    elif event in ("throttle", "throttle_over"):
        throttle = config.throttle
        if throttle is None or isinstance(throttle, Unset):
            return None
        candidate = throttle.log_limit_reached if event == "throttle" else throttle.log_wait_over

    if candidate is None or isinstance(candidate, Unset):
        return None
    return t.cast(LogEvent, candidate)


def make_emitter(logger: logging.Logger) -> t.Callable[..., None]:
    """Build the pipeline's log_emit callable, bound to `logger`.

    Returned signature (matches pipeline.py call sites):
        log_emit(event, config, context, response=None, retry_state=None, extra=None)
    """

    def log_emit(
        event: str,
        config: GracyConfig,
        context: RequestContext | None,
        response: Response | None = None,
        retry_state: RetryState | None = None,
        extra: t.Mapping[str, t.Any] | None = None,
    ) -> None:
        log_event = _resolve_log_event(event, config)
        if log_event is None:
            return

        placeholders = build_placeholders(
            context=context,
            response=response,
            retry_state=retry_state,
            extra=extra,
        )
        template = log_event.custom_message or _DEFAULT_MESSAGES.get(event, event)
        message = safe_format(template, placeholders)
        logger.log(int(log_event.level), message, extra={"gracy": placeholders})

    return log_emit
