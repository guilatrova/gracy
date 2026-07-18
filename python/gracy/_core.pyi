"""Type stubs for the compiled ``gracy._core`` extension (crates/gracy-py).

The real signatures live in crates/gracy-py/src/{scheduler,transport}.rs —
keep this file in lockstep with them (and with gracy._protocols).
"""

from typing import Awaitable

def engine_version() -> str:
    """Version string of the Rust core (e.g. ``"2.0.0-alpha.0"``)."""

class CorePermit:
    """A granted admission (throttle tokens spent, concurrency slots held)."""

    def release(self) -> None:
        """Release concurrency slots + in_flight. Safe to call more than once."""

class CoreScheduler:
    """The queue/throttle engine, compiled from the ``scheduler_plan`` JSON
    emitted by ``gracy.plan.compile_plan``. Raises ``ValueError`` on an
    invalid plan (bad JSON / bad rule regex)."""

    def __init__(self, plan_json: str) -> None: ...
    def submit(
        self,
        uurl: str,
        url: str,
        priority: int = 0,
        from_hook: bool = False,
        no_throttle: bool = False,
        conc_extra: str = "",
    ) -> Awaitable[CorePermit]:
        """Awaitable admission. Failure crosses the FFI as ``RuntimeError``
        with a marker message: ``"GRACY_QUEUE_FULL"`` or ``"GRACY_CLOSED"``."""

    def pause(self, scope: str, seconds: float) -> None:
        """Pause ``"client"`` or a uurl scope. Extends, never shortens."""

    def stats_json(self) -> str:
        """Stats snapshot as JSON — same shape as ``PyScheduler.stats()``
        (JSON forces the ``throttle_hits`` rule-id keys to strings)."""

    def close(self) -> None:
        """Pending and future submits fail with GRACY_CLOSED. Idempotent."""

class CoreResponse:
    """Fully buffered HTTP response (mirrors ``gracy._types.Response``)."""

    @property
    def status(self) -> int: ...
    @property
    def url(self) -> str:
        """Final URL after redirects."""

    @property
    def elapsed(self) -> float:
        """Seconds, wall-clock around send + full body read."""

    @property
    def http_version(self) -> str:
        """``"HTTP/1.1"``, ``"HTTP/2"``, ..."""

    def headers(self) -> list[tuple[str, str]]:
        """Lowercase names, response order preserved."""

    def body(self) -> bytes: ...

class CoreTransport:
    """reqwest-backed transport; one instance wraps one connection pool.
    Raises ``ValueError`` on invalid config JSON."""

    def __init__(self, config_json: str) -> None: ...
    def send(
        self,
        method: str,
        url: str,
        headers: list[tuple[str, str]] = ...,
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> Awaitable[CoreResponse]:
        """Awaitable send. Errors map to raw Python exception types:
        Timeout -> TimeoutError, Connect -> ConnectionError,
        InvalidUrl -> ValueError, Other -> RuntimeError."""
