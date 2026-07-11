"""Engine selection + the Rust-backed Scheduler/Transport wrappers.

The Rust core (``gracy._core``: CoreScheduler / CorePermit / CoreTransport /
CoreResponse) is the DEFAULT engine when the extension is available. Set
``GRACY_ENGINE=python`` to force the pure-Python reference implementations
(PyScheduler / HttpxTransport), or ``GRACY_ENGINE=rust`` to require the core
(raising loudly if the extension is missing).

The wrappers here are deliberately THIN: all queue/throttle semantics live in
crates/gracy-core (mirroring scheduler_py.PyScheduler, the executable spec),
and all transport semantics live in crates/gracy-core/src/transport.rs
(mirroring transports.HttpxTransport). This module only translates types and
error markers across the FFI.

NOTE: this module must not import gracy.transports at module level -
transports.py re-exports RustTransport from here (import cycle).
"""

from __future__ import annotations

import asyncio
import json
import os
import typing as t

from gracy._types import RequestSpec, Response
from gracy.exceptions import GracyClientClosedError, GracyConfigError, GracyQueueFull
from gracy.scheduler_py import PyScheduler

if t.TYPE_CHECKING:
    from gracy._core import CorePermit, CoreTransport
    from gracy._protocols import Scheduler, Transport
    from gracy.transports import TransportConfig

try:  # the extension may be absent (pure-Python install / build failure)
    from gracy import _core
except ImportError:  # pragma: no cover - exercised only without the extension
    _core = None  # type: ignore[assignment]

_HAS_CORE: t.Final[bool] = _core is not None and hasattr(_core, "CoreScheduler")

__all__ = [
    "RustPermit",
    "RustScheduler",
    "RustTransport",
    "current_engine",
    "default_scheduler",
    "default_transport",
]


# --------------------------------------------------------------------------- engine resolution


def current_engine() -> str:
    """Resolve the active engine name: ``"rust"`` or ``"python"``.

    ``GRACY_ENGINE`` env var wins; unset/empty falls back to ``"rust"`` when
    the compiled core is importable, else ``"python"``.
    """
    env = os.environ.get("GRACY_ENGINE", "").strip().lower()
    if env in ("rust", "python"):
        return env
    if env:
        raise GracyConfigError(f"GRACY_ENGINE must be 'rust' or 'python', got {env!r}")
    return "rust" if _HAS_CORE else "python"


def _require_core() -> t.Any:
    if not _HAS_CORE:
        raise GracyConfigError(
            "GRACY_ENGINE=rust but the gracy._core extension is not available; "
            "reinstall gracy with the compiled core or set GRACY_ENGINE=python"
        )
    return _core


def default_scheduler(plan: dict[str, t.Any]) -> Scheduler:
    """Scheduler for the active engine (used when none is injected)."""
    if current_engine() == "rust":
        return RustScheduler(plan)
    return PyScheduler(plan)


def default_transport() -> Transport:
    """Transport for the active engine (used when none is injected)."""
    if current_engine() == "rust":
        return RustTransport()
    from gracy.transports import HttpxTransport  # lazy: avoids the import cycle

    return HttpxTransport()


# --------------------------------------------------------------------------- scheduler


class RustPermit:
    """Held admission from the Rust core. ``release()`` is idempotent."""

    __slots__ = ("_permit",)

    def __init__(self, permit: CorePermit) -> None:
        self._permit = permit

    def release(self) -> None:
        self._permit.release()


def _release_raced_grant(fut: asyncio.Future) -> None:
    """A cancelled submit whose grant raced in anyway must give it back."""
    if fut.cancelled():
        return
    if fut.exception() is None:
        fut.result().release()


class RustScheduler:
    """Scheduler protocol over ``gracy._core.CoreScheduler``.

    Constructed from the same ``scheduler_plan`` dict PyScheduler takes
    (serialized to JSON for the FFI). Submit errors cross the FFI as
    RuntimeError marker messages and are translated back into gracy types.
    """

    __slots__ = ("_core",)

    def __init__(self, plan: dict[str, t.Any]) -> None:
        core = _require_core()
        try:
            self._core = core.CoreScheduler(json.dumps(plan))
        except ValueError as e:
            raise GracyConfigError(f"invalid scheduler plan: {e}") from e

    async def start(self) -> None:
        """No-op: the core has no dispatcher task (submit() does the work inline)."""

    async def submit(
        self,
        uurl: str,
        url: str,
        *,
        priority: int = 0,
        from_hook: bool = False,
        no_throttle: bool = False,
        conc_extra: str = "",
    ) -> RustPermit:
        # shield() mediates cancellation: the pyo3-bridged future's own cancel
        # machinery must never touch the caller task's cancellation bookkeeping,
        # or asyncio.wait_for() can re-raise CancelledError instead of
        # TimeoutError. On cancel we abort the core future ourselves and, if
        # the grant raced in anyway, release it so no capacity leaks.
        core_fut = asyncio.ensure_future(
            self._core.submit(uurl, url, priority, from_hook, no_throttle, conc_extra)
        )
        try:
            permit = await asyncio.shield(core_fut)
        except asyncio.CancelledError:
            core_fut.cancel()
            core_fut.add_done_callback(_release_raced_grant)
            raise
        except RuntimeError as e:
            message = str(e)
            if "GRACY_QUEUE_FULL" in message:
                raise GracyQueueFull(
                    "queue is full and on_full='raise'"
                ) from None
            if "GRACY_CLOSED" in message:
                raise GracyClientClosedError("submit() on a closed scheduler") from None
            raise
        return RustPermit(permit)

    def pause(self, scope: str, seconds: float) -> None:
        self._core.pause(scope, seconds)

    def stats(self) -> dict[str, t.Any]:
        stats = json.loads(self._core.stats_json())
        # JSON object keys are always strings; PyScheduler.stats() uses the
        # int rule ids - normalize so both engines report the same shape.
        stats["throttle_hits"] = {int(rule_id): count for rule_id, count in stats["throttle_hits"].items()}
        return stats

    async def aclose(self) -> None:
        """Idempotent: closing twice is safe (the core's close() is too)."""
        self._core.close()


# --------------------------------------------------------------------------- transport


class RustTransport:
    """The 2.0 default transport: reqwest via ``gracy._core.CoreTransport``.

    Construction stores config only; the core client (and the shared tokio
    runtime) is built lazily in ``start()`` so unstarted clients stay
    fork-safe. Transport errors propagate RAW (TimeoutError / ConnectionError /
    ValueError / RuntimeError from the bindings) - the pipeline is the single
    point that wraps them into GracyRequestFailed.
    """

    __slots__ = ("_config", "_core")

    def __init__(self, config: TransportConfig | None = None) -> None:
        if config is None:
            from gracy.transports import TransportConfig  # lazy: import cycle

            config = TransportConfig()
        self._config = config
        self._core: CoreTransport | None = None

    def _config_json(self) -> str:
        """Serialize TransportConfig to the JSON shape transport.rs expects."""
        config = self._config
        return json.dumps(
            {
                "base_headers": {str(k): str(v) for k, v in config.base_headers.items()},
                "proxy": config.proxy,
                "verify_tls": config.verify_tls,
                "follow_redirects": config.follow_redirects,
                "http2": config.http2,
            }
        )

    async def start(self) -> None:
        if self._core is not None:
            return
        core = _require_core()
        try:
            self._core = core.CoreTransport(self._config_json())
        except ValueError as e:
            raise GracyConfigError(f"invalid transport config: {e}") from e

    async def send(self, spec: RequestSpec) -> Response:
        """Bindings exceptions (timeouts, connect errors, ...) propagate raw."""
        if self._core is None:
            await self.start()
        core = self._core
        assert core is not None

        resp = await core.send(spec.method, spec.url, list(spec.headers), spec.content, spec.timeout)

        return Response(
            status=resp.status,
            headers=tuple(sorted((k.lower(), v) for k, v in resp.headers())),
            body=resp.body(),
            url=resp.url,
            elapsed=resp.elapsed,
            http_version=resp.http_version,
        )

    async def aclose(self) -> None:
        """Drop the core client; its reqwest pool closes when the last ref dies."""
        self._core = None
