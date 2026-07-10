"""The per-request orchestrator — pure Python, the executable spec.

Per-attempt order (V2_PLAN.md §6.4 — hooks run BEFORE admission):

    before hooks -> replay check -> admission (queue: throttle+concurrency)
    -> transport send -> record (if recording) -> after hooks -> metrics/logs
    -> validators -> retry decision (sleep, re-enter) -> decode/parse

Key invariants:
  * Throttle tokens are spent at the last instant before the wire; replay
    hits never spend tokens; every retry attempt re-enters admission.
  * The permit is ALWAYS released in a finally block.
  * Transport exceptions are wrapped in GracyRequestFailed exactly once,
    here, so hooks/retry matching see a consistent type on every attempt.
  * Requests issued inside hooks run in hook-mode (contextvar): hooks are
    skipped, concurrency is bypassed, throttle bypassed unless configured.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import typing as t

from gracy._types import UNSET, RequestContext, RequestSpec, Response, RetryState, Unset
from gracy.config import GracyConfig, Queue, Raises, Retry, StatusPolicy
from gracy.exceptions import GracyParseFailed, GracyRequestFailed

if t.TYPE_CHECKING:
    from gracy._protocols import Hook, ReplayStorage, Scheduler, Transport, Validator
    from gracy.replay import Replay
    from gracy.reports.collector import MetricsCollector

logger = logging.getLogger("gracy")

_in_hook: contextvars.ContextVar[bool] = contextvars.ContextVar("gracy_in_hook", default=False)


def in_hook_context() -> bool:
    """True while executing inside a before/after hook (gracy.testing helper)."""
    return _in_hook.get()


class Pipeline:
    """One per client. Stateless across requests except for injected stages."""

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        transport: Transport,
        metrics: MetricsCollector,
        replay: Replay | None,
        hooks: t.Sequence[Hook],
        queue_config: Queue,
        log_emit: t.Callable[..., None],
    ) -> None:
        self.scheduler = scheduler
        self.transport = transport
        self.metrics = metrics
        self.replay = replay
        self.hooks = list(hooks)
        self.queue_config = queue_config
        self.log_emit = log_emit  # log_emit(event, context, response=..., retry_state=..., extra=...)

    # ------------------------------------------------------------------ hooks

    async def _run_before_hooks(self, context: RequestContext) -> None:
        token = _in_hook.set(True)
        try:
            for hook in self.hooks:
                try:
                    await hook.before(context)
                except Exception:
                    logger.exception("gracy before-hook %r failed; continuing", type(hook).__name__)
        finally:
            _in_hook.reset(token)

    async def _run_after_hooks(
        self, context: RequestContext, result: Response | Exception, retry_state: RetryState | None
    ) -> None:
        token = _in_hook.set(True)
        try:
            for hook in self.hooks:
                try:
                    await hook.after(context, result, retry_state)
                except Exception:
                    logger.exception("gracy after-hook %r failed; continuing", type(hook).__name__)
        finally:
            _in_hook.reset(token)

    # ------------------------------------------------------------------ one attempt

    async def _attempt(
        self,
        spec: RequestSpec,
        context: RequestContext,
        config: GracyConfig,
        retry_state: RetryState | None,
    ) -> tuple[Response | None, Exception | None]:
        from_hook = _in_hook.get()
        run_hooks = not from_hook and self.hooks

        if run_hooks:
            await self._run_before_hooks(context)

        response: Response | None = None
        exc: Exception | None = None
        replayed = False

        # -- replay check happens BEFORE admission: replays never spend tokens
        if self.replay is not None and self.replay.mode in ("replay", "smart-replay"):
            response = await self.replay.find(spec)
            replayed = response is not None
            if self.replay.mode == "replay" and response is None:
                from gracy.exceptions import GracyReplayRequestNotFound

                exc = GracyReplayRequestNotFound(f"No recording for {spec.method} {spec.url}")

        if response is None and exc is None:
            # Lazy import: gracy.testing imports this module (in_hook_context).
            # throttle_off() must also work for clients built OUTSIDE the with
            # block, so the runtime switch ORs into the replay-driven bypass.
            from gracy.testing import throttle_is_disabled

            no_throttle = throttle_is_disabled() or bool(
                self.replay is not None and self.replay.disable_throttling and replayed
            )
            start = time.monotonic()
            permit = await self.scheduler.submit(
                spec.uurl,
                spec.url,
                priority=context.priority,
                from_hook=from_hook,
                no_throttle=no_throttle,
            )
            try:
                try:
                    response = await self.transport.send(spec)
                except asyncio.CancelledError:
                    raise
                except Exception as raw:
                    exc = GracyRequestFailed(spec.url, raw)
            finally:
                permit.release()
            context.elapsed = time.monotonic() - start

            if response is not None and self.replay is not None and self.replay.mode in ("record", "smart-replay"):
                if not replayed:
                    await self.replay.record(spec, response)
        elif response is not None:
            context.elapsed = response.elapsed

        # -- pause gates (Queue.pause_on_status)
        if response is not None and self.queue_config.pause_on_status:
            scope_kind = self.queue_config.pause_on_status.get(response.status)
            if scope_kind is not None:
                seconds = _retry_after_seconds(response) or 1.0
                scope = "client" if scope_kind == "client" else spec.uurl
                self.scheduler.pause(scope, seconds)

        result: Response | Exception = response if response is not None else t.cast(Exception, exc)
        if run_hooks:
            await self._run_after_hooks(context, result, retry_state)

        self.metrics.track(context, response, exc, replayed=replayed, retry=retry_state is not None)
        return response, exc

    # ------------------------------------------------------------------ full request

    async def execute(
        self,
        spec: RequestSpec,
        context: RequestContext,
        config: GracyConfig,
        validators: t.Sequence[Validator],
    ) -> tuple[Response | None, Exception | None]:
        """Runs attempts + validation + retry loop. Returns (response, unresolved_exc).

        Decoding/parsing is the caller's job (parsing.py) — it needs the
        endpoint's return annotation, which the pipeline doesn't know about.
        """
        self.log_emit("request", config, context)

        retry: Retry | None = None if isinstance(config.retry, Unset) else config.retry
        policy = config.status_policy if isinstance(config.status_policy, StatusPolicy) else None

        attempt = 0
        retry_state: RetryState | None = None
        response, exc = await self._attempt(spec, context, config, None)
        exc = self._validate(response, exc, policy, validators)

        while exc is not None and retry is not None:
            status_code = response.status if response is not None else None
            if attempt >= retry.attempts or not retry.matches(status_code, exc):
                if attempt > 0:
                    self.log_emit("retry_exhausted", config, context, response=response, retry_state=retry_state)
                break

            attempt += 1
            delay = retry.delay_for(attempt, status_code)
            if retry.respect_retry_after and response is not None:
                ra = _retry_after_seconds(response)
                if ra is not None:
                    delay = max(delay, ra)

            retry_state = RetryState(
                attempt=attempt,
                max_attempts=retry.attempts,
                delay=delay,
                cause=_cause(response, exc),
                last_status=status_code,
            )
            self.log_emit("retry_before", config, context, response=response, retry_state=retry_state)
            if delay > 0:
                await asyncio.sleep(delay)

            context.attempt = attempt
            response, exc = await self._attempt(spec, context, config, retry_state)  # noqa: PLW2901
            exc = self._validate(response, exc, policy, validators)
            self.log_emit("retry_after", config, context, response=response, retry_state=retry_state)

        if response is not None:
            if exc is None:
                self.log_emit("response", config, context, response=response)
            else:
                self.log_emit("error", config, context, response=response)
        elif exc is not None:
            self.log_emit("error", config, context)

        return response, exc

    # ------------------------------------------------------------------ validation

    def _validate(
        self,
        response: Response | None,
        exc: Exception | None,
        policy: StatusPolicy | None,
        validators: t.Sequence[Validator],
    ) -> Exception | None:
        """Validators only run when a response exists (v1 parity). A passing
        chain CLEARS a previous transport error only if this attempt has a
        response (stale-response bug fix: response is per-attempt here)."""
        if response is None:
            return exc

        from gracy.validators import check_status_policy

        try:
            if policy is not None:
                check_status_policy(policy, response)
            for validator in validators:
                validator.check(response)
        except Exception as v_exc:  # noqa: BLE001 - validator contract is "raise anything"
            return v_exc
        return None


def _retry_after_seconds(response: Response) -> float | None:
    raw = response.header("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        return max(0.0, dt.timestamp() - time.time())


def _cause(response: Response | None, exc: Exception | None) -> str:
    if response is not None:
        return f"status {response.status}"
    if exc is not None:
        inner = exc.original_exc if isinstance(exc, GracyRequestFailed) else exc
        return type(inner).__name__
    return "unknown"


def decode_result(
    response: Response | None,
    exc: Exception | None,
    config: GracyConfig,
    return_type: t.Any,
    context: RequestContext,
) -> t.Any:
    """Final stage: on= map -> decoder -> return. Runs EVEN on failed responses
    when the failure is suppressed (v1 parity: parse runs last)."""
    retry = None if isinstance(config.retry, Unset) else config.retry
    suppressed = bool(retry and (retry.suppress or retry.on_exhausted == "return"))

    on = config.on if not isinstance(config.on, Unset) else None
    action: t.Any = UNSET
    if response is not None and on:
        if response.status in on:
            action = on[response.status]
        elif "default" in on:
            action = on["default"]

    if not isinstance(action, Unset):
        if isinstance(action, Raises):
            raise action.exc(context, response)  # type: ignore[call-arg]
        if callable(action):
            try:
                return action(response)
            except Exception as parse_exc:
                raise GracyParseFailed(
                    f"Parser for status {response.status if response else '?'} failed: {parse_exc}", response
                ) from parse_exc
        return action  # literal (e.g. None)

    if exc is not None and not suppressed:
        raise exc
    if response is None:
        return None  # suppressed failure with no response

    if exc is not None and suppressed and on is None and return_type is not None:
        # Failed response, suppressed, nothing to decode it into safely.
        return response if return_type is None else _decode(response, config, return_type)
    return _decode(response, config, return_type)


def _decode(response: Response, config: GracyConfig, return_type: t.Any) -> t.Any:
    from gracy.parsing import decode_response

    decoder = None if isinstance(config.decoder, Unset) else config.decoder
    return decode_response(response, return_type, decoder)
