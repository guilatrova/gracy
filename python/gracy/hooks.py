"""Built-in hooks. v2 backoff hooks drive scheduler-level pause gates (V2_PLAN §6.3).

Because pauses are dispatcher gates on the queue - not sleeps inside the hook -
these hooks genuinely pause admission for ALL matching requests, including
retries (which re-enter admission on every attempt).
"""

from __future__ import annotations

import logging
import time
import typing as t
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from gracy._protocols import Hook
from gracy._types import RequestContext, Response, RetryState
from gracy.config import LogEvent

if t.TYPE_CHECKING:
    from gracy._protocols import Scheduler

__all__ = [
    "Hook",
    "HookResult",
    "parse_retry_after",
    "RetryAfterBackoff",
    "RateLimitBackoff",
]

logger = logging.getLogger("gracy")


@dataclass
class HookResult:
    """v1-compat shim. The v2 pipeline ignores hook return values - pausing is
    done via the scheduler's pause gates, not by returning HookResult."""

    should_pause: bool = False
    seconds: float | None = None


def parse_retry_after(value: str) -> float | None:
    """Parse a Retry-After header value into seconds.

    Accepts delta-seconds ("120") or an HTTP-date ("Wed, 21 Oct 2026 07:28:00 GMT").
    Returns None when unparseable.
    """
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, dt.timestamp() - time.time())


class RetryAfterBackoff(Hook):
    """Pauses admission when a response status carries a Retry-After header.

    Default statuses: 429, 503. The pause is a scheduler dispatcher gate:
    scope is the whole client, or the endpoint's uurl when lock_per_endpoint.
    Bound to the scheduler by the client at build() via bind().
    """

    DEFAULT_LOG_MESSAGE: t.Final = "[{METHOD}] {UURL} asked to back off; pausing for {PAUSE}s"

    def __init__(
        self,
        lock_per_endpoint: bool = False,
        statuses: tuple[int, ...] = (429, 503),
        processor: t.Callable[[float], float] | None = None,
        log_event: LogEvent | None = None,
    ) -> None:
        self._lock_per_endpoint = lock_per_endpoint
        self._statuses = statuses
        self._processor = processor
        self._log_event = log_event
        self._scheduler: Scheduler | None = None
        self._fallback_seconds: float | None = None  # RateLimitBackoff sets this
        self._warned_unbound = False

    def bind(self, scheduler: Scheduler) -> None:
        """Called by the client at build() so the hook can drive pause gates."""
        self._scheduler = scheduler

    async def after(
        self,
        context: RequestContext,
        result: Response | Exception,
        retry_state: RetryState | None,
    ) -> None:
        if not (isinstance(result, Response) and result.status in self._statuses):
            return
        if result.is_replay:  # replayed responses no-op backoff hooks (v1 parity)
            return

        if self._scheduler is None:
            if not self._warned_unbound:
                self._warned_unbound = True
                logger.warning(
                    "%s.after() called before bind(scheduler); pause skipped. "
                    "Register the hook via `hooks = [...]` so the client binds it at build().",
                    type(self).__name__,
                )
            return

        raw = result.header("retry-after")
        seconds = parse_retry_after(raw) if raw else None
        if seconds is None:
            seconds = self._fallback_seconds
            if seconds is None:
                return
        if self._processor is not None:
            seconds = self._processor(seconds)

        scope = context.uurl if self._lock_per_endpoint else "client"
        self._scheduler.pause(scope, seconds)
        self._log_pause(context, result, seconds)

    def _log_pause(self, context: RequestContext, response: Response, seconds: float) -> None:
        event = self._log_event
        if event is None:
            return
        from gracy.logging_events import build_placeholders, safe_format

        values = build_placeholders(context=context, response=response, extra={"PAUSE": seconds})
        message = safe_format(event.custom_message or self.DEFAULT_LOG_MESSAGE, values)
        logger.log(int(event.level), message)


class RateLimitBackoff(RetryAfterBackoff):
    """Like RetryAfterBackoff, but pauses for `delay` even when the Retry-After
    header is missing or unparseable. A parseable header value wins over delay."""

    def __init__(
        self,
        delay: float = 30.0,
        lock_per_endpoint: bool = False,
        statuses: tuple[int, ...] = (429, 503),
        processor: t.Callable[[float], float] | None = None,
        log_event: LogEvent | None = None,
    ) -> None:
        super().__init__(
            lock_per_endpoint=lock_per_endpoint,
            statuses=statuses,
            processor=processor,
            log_event=log_event,
        )
        self.delay = delay
        self._fallback_seconds = delay
