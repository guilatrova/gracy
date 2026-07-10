"""Protocols every engine stage implements. Rust and Python implementations are interchangeable.

Contract stability note: these signatures are the seam between the Python
policy layer and the Rust core. Changing them is a breaking internal change —
update _core.pyi, the Rust bindings, and the pure-Python reference together.
"""

from __future__ import annotations

import typing as t

from gracy._types import RequestSpec, Response

if t.TYPE_CHECKING:
    from gracy._types import RequestContext, RetryState


@t.runtime_checkable
class Permit(t.Protocol):
    """Held admission: throttle tokens spent + concurrency slot acquired.

    MUST be released exactly once, in a finally block, after the send
    completes/fails/cancels.
    """

    def release(self) -> None: ...


@t.runtime_checkable
class Scheduler(t.Protocol):
    """The queue. Owns throttling, concurrency, priorities, backpressure, pause gates.

    Construction receives the compiled plan (see gracy.plan.compile_plan).
    """

    async def start(self) -> None: ...

    async def submit(
        self,
        uurl: str,
        url: str,
        *,
        priority: int = 0,
        from_hook: bool = False,
        no_throttle: bool = False,
        conc_extra: str = "",
    ) -> Permit:
        """Admission control. Resolves when the request may go on the wire NOW.

        - Throttle window tokens are reserved atomically at grant.
        - from_hook=True bypasses concurrency semaphores (hook-deadlock fix)
          and, unless the plan sets throttle_in_hooks=True, bypasses throttle.
        - no_throttle=True bypasses throttle only (replay-hit path).
        - Raises GracyQueueFull when the queue is saturated and on_full="raise".
        """
        ...

    def pause(self, scope: str, seconds: float) -> None:
        """Dispatcher-level gate. scope: "client" or a uurl. Extends the current pause if longer."""
        ...

    def stats(self) -> dict[str, t.Any]:
        """{"pending": int, "in_flight": int, "throttle_hits": {rule: int}, "paused": {scope: seconds_left}}"""
        ...

    async def aclose(self) -> None: ...


@t.runtime_checkable
class Transport(t.Protocol):
    """Sends one RequestSpec, returns a buffered Response. No policy inside."""

    async def start(self) -> None: ...

    async def send(self, spec: RequestSpec) -> Response:
        """Transport failures raise the ORIGINAL exception; the pipeline wraps
        it into GracyRequestFailed (single wrapping point)."""
        ...

    async def aclose(self) -> None: ...


@t.runtime_checkable
class ReplayStorage(t.Protocol):
    """Cassette storage, schema v2, no pickle. All methods async (called off the hot path)."""

    async def prepare(self) -> None: ...

    async def record(self, spec: RequestSpec, response: Response) -> None: ...

    async def find(self, spec: RequestSpec, discard_before: float | None) -> Response | None:
        """Return the recorded response or None. discard_before: unix seconds.
        Returned Response MUST have is_replay=True."""
        ...

    async def flush(self) -> None:
        """Guaranteed to be awaited by Gracy.aclose()."""
        ...


class Validator:
    """Response validator. Sync check(); raise to signal failure (feeds retry_on matching)."""

    def check(self, response: Response) -> None:
        raise NotImplementedError


class Decoder:
    """Decodes response bytes into the endpoint's return annotation type."""

    def decode(self, tp: t.Any, response: Response) -> t.Any:
        raise NotImplementedError

    def handles(self, tp: t.Any) -> bool:
        raise NotImplementedError


class Hook:
    """Before/after hook. Subclass or duck-type; register via `hooks = [...]` or client methods.

    Requests issued INSIDE a hook run in hook-mode: hooks skipped, concurrency
    bypassed, throttle bypassed unless Queue(throttle_in_hooks=True).
    """

    async def before(self, context: RequestContext) -> None:  # noqa: B027
        pass

    async def after(
        self,
        context: RequestContext,
        result: Response | Exception,
        retry_state: RetryState | None,
    ) -> None:  # noqa: B027
        pass
