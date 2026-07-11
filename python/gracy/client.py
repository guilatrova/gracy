"""The Gracy client: declarative class surface + explicit lifecycle + sync facade.

Lifecycle (V2_PLAN.md §8):
  * ``__init__`` stores arguments only — an unstarted client owns NO runtime
    state, so module-level instances are fork-safe by construction.
  * ``await build()`` (or ``async with``) captures the running loop + pid,
    collects endpoint specs, compiles the plan once, and starts the
    scheduler/transport. Idempotent.
  * ``_check_ready()`` guards every request: closed clients raise
    GracyClientClosedError, forked children raise GracyForkedClientError,
    cross-loop use raises GracyWrongLoopError.
  * ``aclose()`` flushes replay storage and closes scheduler + transport.

Namespaces are explicit descriptors (``berry = BerryNamespace()``) — no
annotation scanning, no class-attribute state bleed (V2_PLAN.md §11).
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import inspect
import json as jsonlib
import logging
import os
import threading
import typing as t
from contextvars import ContextVar

from gracy._types import UNSET, RequestContext, RequestSpec, Unset
from gracy.config import GracyConfig, Queue, apply_url_overrides
from gracy.endpoints import (
    EndpointMethod,
    EndpointSpec,
    append_query,
    format_url,
    join_url,
)
from gracy.exceptions import (
    GracyClientClosedError,
    GracyConfigError,
    GracyForkedClientError,
    GracyWrongLoopError,
)
from gracy.logging_events import make_emitter
from gracy.pipeline import Pipeline, decode_result
from gracy.plan import CompiledPlan, CompiledRoute, compile_plan
from gracy.engine import current_engine, default_scheduler, default_transport
from gracy.reports.collector import MetricsCollector
from gracy.testing import apply_test_overrides
from gracy.validators import normalize_validators

if t.TYPE_CHECKING:
    from gracy._protocols import Response, RetryState, Scheduler, Transport  # noqa: F401
    from gracy.replay import Replay
    from gracy.reports import GracyReport

__all__ = ["Gracy", "GracyNamespace", "SyncGracy"]

TGracy = t.TypeVar("TGracy", bound="Gracy")

_DEFAULT_TIMEOUT: t.Final = 30.0

# Overlay set by api.options(): (partial config or None, priority or None).
_options_overlay: ContextVar[tuple[GracyConfig | None, int | None] | None] = ContextVar(
    "gracy_options_overlay", default=None
)


# --------------------------------------------------------------------------- options()


class _OptionsContext:
    """Scoped override from api.options(). Works with both `with` and `async with`."""

    def __init__(self, config: GracyConfig | None, priority: int | None) -> None:
        self._config = config
        self._priority = priority
        self._token: t.Any = None

    def __enter__(self) -> _OptionsContext:
        cfg, prio = self._config, self._priority
        current = _options_overlay.get()
        if current is not None:  # nested options(): inner wins field-by-field
            cur_cfg, cur_prio = current
            if cfg is not None and cur_cfg is not None:
                cfg = cfg.merged_under(cur_cfg)
            elif cfg is None:
                cfg = cur_cfg
            if prio is None:
                prio = cur_prio
        self._token = _options_overlay.set((cfg, prio))
        return self

    def __exit__(self, *exc_info: t.Any) -> bool:
        if self._token is not None:
            _options_overlay.reset(self._token)
            self._token = None
        return False

    async def __aenter__(self) -> _OptionsContext:
        return self.__enter__()

    async def __aexit__(self, *exc_info: t.Any) -> bool:
        return self.__exit__(*exc_info)


# --------------------------------------------------------------------------- namespaces


class GracyNamespace:
    """Endpoint group assigned as a CLASS attribute instance on a Gracy subclass::

        class BerryNamespace(GracyNamespace):
            path_prefix = "/berry"
            @get("/{name}")
            async def get_one(self, name) -> dict: ...

        class PokeAPI(Gracy):
            berry = BerryNamespace()

    Instance access (``api.berry``) returns a per-client binding, so two
    clients never share namespace state.
    """

    path_prefix: str = ""
    config: GracyConfig | None = None

    _attr_name: str | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr_name = name

    def __get__(self, obj: t.Any, objtype: type | None = None) -> t.Any:
        if obj is None:
            return self
        name = self._attr_name or type(self).__name__
        cache_key = f"_ns_{name}"
        bound = obj.__dict__.get(cache_key)
        if bound is None:
            bound = _BoundNamespace(self, obj, name)
            obj.__dict__[cache_key] = bound
        return bound


class _BoundNamespace:
    """A GracyNamespace bound to one client instance."""

    def __init__(self, ns: GracyNamespace, client: Gracy, attr_name: str) -> None:
        self._ns = ns
        self._client = client
        self._attr = attr_name

    async def _call_endpoint(self, em: EndpointMethod, bound_args: dict[str, t.Any]) -> t.Any:
        # EndpointMethod.__get__ binds against US, so its bound callable lands here.
        return await self._client._call_endpoint_ns(self._attr, em, bound_args)

    def __getattr__(self, name: str) -> t.Any:
        for klass in type(self._ns).__mro__:
            value = vars(klass).get(name)
            if isinstance(value, EndpointMethod):
                return value.__get__(self, type(self._ns))
            if value is not None:
                break
        return getattr(self._ns, name)  # non-endpoint attrs forward to the namespace

    def __repr__(self) -> str:
        return f"<BoundNamespace {self._attr!r} of {type(self._client).__name__}>"


# --------------------------------------------------------------------------- the client


class Gracy:
    """Declarative async API client. Subclass, declare endpoints, use ``async with``."""

    # -- declarative class-level surface
    base_url: str = ""
    timeout: float | None  # optional class attr; absent = UNSET (library default 30s)
    config: GracyConfig | None = None
    hooks: list[t.Any] = []

    def __init__(
        self,
        *,
        replay: Replay | None = None,
        transport: Transport | None = None,
        scheduler: Scheduler | None = None,
        debug: bool = False,
        monitor: bool | None = None,
    ) -> None:
        # v1 migration guard: nested `class Config` is gone in v2.
        for klass in type(self).__mro__:
            nested = vars(klass).get("Config")
            if isinstance(nested, type):
                raise GracyConfigError(
                    f"{type(self).__name__} declares a nested `class Config` (v1 style). "
                    "In v2, declare class attributes instead: `base_url = ...`, `timeout = ...`, "
                    "`config = GracyConfig(...)` — see the v2 migration cookbook."
                )

        # Arguments only — NO runtime state (fork-safety: unstarted clients are inert).
        self._replay = replay
        self._injected_transport = transport
        self._injected_scheduler = scheduler
        self._debug = debug
        self._monitor = monitor  # None = read GRACY_MONITOR env at build()

        self._built = False
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pid: int | None = None
        self._plan: CompiledPlan | None = None
        self._scheduler: Scheduler | None = None
        self._transport: Transport | None = None
        self._pipeline: Pipeline | None = None
        self._metrics: MetricsCollector | None = None
        self._monitor_publisher: t.Any = None  # gracy.monitor.MonitorPublisher when enabled
        self._hooks: list[t.Any] = []
        self._routes: dict[tuple[str | None, str], CompiledRoute] = {}

    # ------------------------------------------------------------------ default hook slots

    async def before(self, context: RequestContext) -> None:  # noqa: B027
        """Override to run before every request (the client itself becomes a hook)."""

    async def after(
        self,
        context: RequestContext,
        result: Response | Exception,
        retry_state: RetryState | None,
    ) -> None:  # noqa: B027
        """Override to run after every attempt. `result` exceptions are always GracyRequestFailed-wrapped."""

    # ------------------------------------------------------------------ lifecycle

    async def build(self: TGracy) -> TGracy:
        """Compile the plan and start scheduler/transport. Idempotent."""
        if self._built and not self._closed:
            return self
        if self._closed:
            raise GracyClientClosedError("This client was closed; create a new instance")

        self._loop = asyncio.get_running_loop()
        self._pid = os.getpid()
        if self._debug:
            logging.getLogger("gracy").setLevel(logging.DEBUG)

        specs, ns_configs = self._collect_specs()
        client_config = self.config
        if client_config is not None:
            # Build-time test switches (gracy.testing.retries_off/throttle_off).
            client_config = apply_test_overrides(client_config)

        plan = compile_plan(
            client_config,
            ns_configs,
            specs,
            self.base_url,
            getattr(self, "timeout", UNSET),
        )

        scheduler: Scheduler = self._injected_scheduler or default_scheduler(plan.scheduler_plan)
        transport: Transport = self._injected_transport or default_transport()
        await scheduler.start()
        await transport.start()
        if self._replay is not None:
            await self._replay.prepare()

        metrics = MetricsCollector()
        log_emit = make_emitter(logging.getLogger("gracy"))

        hooks: list[t.Any] = []
        if type(self).before is not Gracy.before or type(self).after is not Gracy.after:
            hooks.append(self)  # the client's own before/after overrides are a hook
        for item in type(self).hooks:
            hook = item() if isinstance(item, type) else item  # accept classes or instances
            bind = getattr(hook, "bind", None)
            if callable(bind):
                bind(scheduler)
            hooks.append(hook)

        queue_config = plan.base_config.queue
        if queue_config is None or isinstance(queue_config, Unset):
            queue_config = Queue()

        self._pipeline = Pipeline(
            scheduler=scheduler,
            transport=transport,
            metrics=metrics,
            replay=self._replay,
            hooks=hooks,
            queue_config=queue_config,
            log_emit=log_emit,
        )

        routes: dict[tuple[str | None, str], CompiledRoute] = {}
        for name, route in plan.routes.items():
            if "." in name:
                ns_attr, attr = name.split(".", 1)
                routes[(ns_attr, attr)] = route
            else:
                routes[(None, name)] = route

        self._plan = plan
        self._scheduler = scheduler
        self._transport = transport
        self._metrics = metrics
        self._hooks = hooks
        self._routes = routes
        self._built = True

        if self._monitor_enabled():
            await self._start_monitor(scheduler, metrics)
        return self

    async def aclose(self) -> None:
        """Flush replay storage, close scheduler and transport. Idempotent."""
        if not self._built or self._closed:
            return
        self._closed = True
        try:
            if self._monitor_publisher is not None:
                publisher, self._monitor_publisher = self._monitor_publisher, None
                await publisher.aclose()  # final closed=true snapshot before teardown
            if self._replay is not None:
                await self._replay.flush()
        finally:
            assert self._scheduler is not None and self._transport is not None
            await self._scheduler.aclose()
            await self._transport.aclose()

    # ------------------------------------------------------------------ live monitor

    def _monitor_enabled(self) -> bool:
        """monitor kwarg wins; None falls back to the GRACY_MONITOR env var."""
        if self._monitor is not None:
            return self._monitor
        return os.environ.get("GRACY_MONITOR", "").strip().lower() in ("1", "true")

    async def _start_monitor(self, scheduler: Scheduler, metrics: MetricsCollector) -> None:
        """Spawn the MonitorPublisher (lazy import: zero overhead when disabled)."""
        import time as _time

        from gracy.monitor import MonitorPublisher

        publisher = MonitorPublisher(type(self).__name__, current_engine())

        def get_snapshot() -> dict[str, t.Any]:
            queue = dict(scheduler.stats())
            rows = metrics.monitor_rows(queue.get("throttled_by_uurl") or {})
            requests = sum(row["total"] for row in rows)
            elapsed = max(_time.time() - publisher.started_at, 0.001)
            return {
                "queue": queue,
                "totals": {
                    "requests": requests,
                    "aborts": sum(row["aborts"] for row in rows),
                    "retries": sum(row["retries"] for row in rows),
                    "replays": sum(row["replays"] for row in rows),
                    "req_per_sec": requests / elapsed,
                },
                "rows": rows,
            }

        await publisher.start(get_snapshot)
        self._monitor_publisher = publisher

    async def __aenter__(self: TGracy) -> TGracy:
        return await self.build()

    async def __aexit__(self, *exc_info: t.Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ guards

    def _check_ready(self) -> None:
        if not self._built or self._closed:
            raise GracyClientClosedError(
                "Client not started (or already closed) — use 'async with MyClient() as api:' "
                "or 'await client.build()' before issuing requests"
            )
        if os.getpid() != self._pid:
            raise GracyForkedClientError(
                "This client was built in another process; build clients per-worker (post-fork)"
            )
        if asyncio.get_running_loop() is not self._loop:
            raise GracyWrongLoopError(
                "Client used from a different event loop than the one it was built on"
            )

    # ------------------------------------------------------------------ spec collection

    def _collect_specs(self) -> tuple[dict[str, EndpointSpec], dict[str, GracyConfig | None]]:
        """Walk the MRO for EndpointMethod descriptors and GracyNamespace instances."""
        specs: dict[str, EndpointSpec] = {}
        ns_configs: dict[str, GracyConfig | None] = {}
        seen: set[str] = set()

        for klass in type(self).__mro__:
            for attr, value in vars(klass).items():
                if attr in seen:
                    continue
                if isinstance(value, EndpointMethod):
                    seen.add(attr)
                    specs[attr] = value.make_spec(attr)
                elif isinstance(value, GracyNamespace):
                    seen.add(attr)
                    ns_configs[attr] = value.config
                    self._collect_namespace_specs(attr, value, specs)
        return specs, ns_configs

    @staticmethod
    def _collect_namespace_specs(
        ns_attr: str, ns: GracyNamespace, specs: dict[str, EndpointSpec]
    ) -> None:
        for klass in type(ns).__mro__:
            for attr, value in vars(klass).items():
                if not isinstance(value, EndpointMethod):
                    continue
                full_name = f"{ns_attr}.{attr}"
                if full_name in specs:  # first hit in MRO order wins
                    continue
                # Rebuild the spec against the prefixed path so placeholder
                # validation covers path_prefix placeholders too.
                em = copy.copy(value)
                em.path = join_url(ns.path_prefix, value.path)
                spec = em.make_spec(full_name)
                spec.namespace = ns_attr
                specs[full_name] = spec

    # ------------------------------------------------------------------ endpoint dispatch

    async def _call_endpoint(self, em: EndpointMethod, bound_args: dict[str, t.Any]) -> t.Any:
        """Target of EndpointMethod.__get__ bindings made directly against the client."""
        return await self._dispatch(None, em, bound_args)

    async def _call_endpoint_ns(
        self, ns_attr: str, em: EndpointMethod, bound_args: dict[str, t.Any]
    ) -> t.Any:
        """Target of EndpointMethod bindings made against a _BoundNamespace."""
        return await self._dispatch(ns_attr, em, bound_args)

    async def _dispatch(
        self, ns_attr: str | None, em: EndpointMethod, bound_args: dict[str, t.Any]
    ) -> t.Any:
        self._check_ready()
        route = self._routes.get((ns_attr, em.attr_name or ""))
        if route is None:
            raise GracyConfigError(
                f"Endpoint {em!r} is not part of this client's compiled plan; "
                "declare it in the class body before build()"
            )
        return await self._call_route(route, bound_args)

    async def _call_route(self, route: CompiledRoute, bound_args: dict[str, t.Any]) -> t.Any:
        # -- split bound args by ParamSpec kind
        kinds = {p.name: p.kind for p in (route.spec.params if route.spec else ())}
        path_args: dict[str, t.Any] = {}
        query_args: dict[str, t.Any] = {}
        header_args: dict[str, str] = {}
        body: t.Any = UNSET
        for name, value in bound_args.items():
            if value is UNSET:
                continue
            kind = kinds.get(name, "query")
            if kind == "path":
                path_args[name] = value
            elif kind == "header":
                header_args[name] = str(value)
            elif kind == "body":
                if body is not UNSET:
                    raise GracyConfigError(
                        f"Endpoint {route.name!r}: multiple Body arguments provided"
                    )
                body = value
            else:
                query_args[name] = value

        url = format_url(route.url_template, path_args)
        url = append_query(url, query_args)

        active, priority = self._resolve_active_config(route.config)
        content, content_headers = _encode_body(body if body is not UNSET else None)

        spec = RequestSpec(
            method=route.method,
            url=url,
            uurl=route.url_template,
            headers=_headers_tuple(content_headers, header_args),
            content=content,
            timeout=_resolve_timeout(active),
        )
        context = RequestContext(
            method=route.method,
            url=url,
            uurl=route.url_template,
            endpoint=route.spec.path if route.spec else route.url_template,
            endpoint_args={k: str(v) for k, v in path_args.items()},
            priority=priority,
        )

        assert self._pipeline is not None
        validators = normalize_validators(active.validators)
        response, exc = await self._pipeline.execute(spec, context, active, validators)
        return decode_result(
            response, exc, active, route.spec.return_type if route.spec else None, context
        )

    # ------------------------------------------------------------------ ad-hoc requests

    async def request(
        self,
        method: str,
        endpoint: str | t.Any,  # str or BaseEndpoint (str-Enum)
        path: t.Mapping[str, t.Any] | None = None,
        *,
        params: t.Mapping[str, t.Any] | None = None,
        headers: t.Mapping[str, str] | None = None,
        content: bytes | str | None = None,
        json: t.Any = None,
        decode_as: t.Any = None,
        priority: int = 0,
        timeout: float | None | Unset = UNSET,
    ) -> t.Any:
        """Ad-hoc escape hatch — full queue/retry/replay treatment without a declared endpoint."""
        self._check_ready()
        assert self._plan is not None and self._pipeline is not None

        uurl = join_url(self.base_url, str(endpoint))
        url = format_url(uurl, path or {})
        if params:
            url = append_query(url, params)

        base = apply_url_overrides(self._plan.base_config, url)
        active, overlay_priority = self._resolve_active_config(base)
        if not isinstance(timeout, Unset):
            active = GracyConfig(timeout=timeout).merged_under(active)
        effective_priority = priority if priority else (overlay_priority or 0)

        body_headers: dict[str, str] = {}
        if json is not None:
            body = jsonlib.dumps(json).encode("utf-8")
            body_headers["content-type"] = "application/json"
        elif isinstance(content, str):
            body = content.encode("utf-8")
        else:
            body = content

        spec = RequestSpec(
            method=method.upper(),
            url=url,
            uurl=uurl,
            headers=_headers_tuple(body_headers, headers or {}),
            content=body,
            timeout=_resolve_timeout(active),
        )
        context = RequestContext(
            method=method.upper(),
            url=url,
            uurl=uurl,
            endpoint=str(endpoint),
            endpoint_args={k: str(v) for k, v in (path or {}).items()},
            priority=effective_priority,
        )

        validators = normalize_validators(active.validators)
        response, exc = await self._pipeline.execute(spec, context, active, validators)
        return decode_result(response, exc, active, decode_as, context)

    # ------------------------------------------------------------------ scoped overrides

    def options(self, *, priority: int | None = None, **cfg: t.Any) -> _OptionsContext:
        """Scoped per-call overrides: ``with api.options(retry=None): ...`` (also ``async with``)."""
        forbidden = {"throttle", "concurrency", "queue"} & set(cfg)
        if forbidden:
            raise GracyConfigError(
                f"options({sorted(forbidden)}): scheduler-side settings are compiled at "
                "build(); declare them on the client or endpoint"
            )
        valid = {f.name for f in dataclasses.fields(GracyConfig)}
        unknown = sorted(set(cfg) - valid)
        if unknown:
            raise GracyConfigError(f"Unknown options() kwargs {unknown}; allowed: {sorted(valid)}")
        config = GracyConfig(**cfg) if cfg else None
        return _OptionsContext(config, priority)

    def _resolve_active_config(self, base: GracyConfig) -> tuple[GracyConfig, int]:
        """Layer the options() overlay + test switches on top of a resolved config."""
        active = base
        priority = 0
        overlay = _options_overlay.get()
        if overlay is not None:
            overlay_config, overlay_priority = overlay
            if overlay_config is not None:
                active = overlay_config.merged_under(active)
            if overlay_priority is not None:
                priority = overlay_priority
        active = apply_test_overrides(active)
        return active, priority

    # ------------------------------------------------------------------ observability

    def report(self) -> GracyReport:
        """Frozen metrics snapshot (printing never mutates it)."""
        metrics = self._metrics if self._metrics is not None else MetricsCollector()
        scheduler_stats = self._scheduler.stats() if self._built and self._scheduler else None
        return metrics.snapshot(scheduler_stats)

    def reset_metrics(self) -> None:
        """Drop every tracked metric (replaces v1's dangerously_reset_report)."""
        if self._metrics is not None:
            self._metrics.reset()

    def queue_stats(self) -> dict[str, t.Any]:
        """Live scheduler stats: pending, in_flight, throttle hits, active pauses."""
        if not self._built or self._scheduler is None:
            return {}
        return dict(self._scheduler.stats())

    # ------------------------------------------------------------------ sync facade

    @classmethod
    def sync(cls, **init_kwargs: t.Any) -> SyncGracy:
        """Blocking facade: ``with MyClient.sync() as api: api.get_pokemon("mew")``.

        A daemon thread runs a private event loop hosting the REAL async
        client — sync and async share 100% of the pipeline, hooks included.
        """
        return SyncGracy(cls, init_kwargs)


# --------------------------------------------------------------------------- sync facade


class SyncGracy:
    """Blocking proxy over an async Gracy client living in a background loop thread."""

    def __init__(self, client_cls: type[Gracy], init_kwargs: dict[str, t.Any]) -> None:
        self._client_cls = client_cls
        self._init_kwargs = init_kwargs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Gracy | None = None

    # -- lifecycle

    def __enter__(self) -> SyncGracy:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"gracy-sync-{self._client_cls.__name__}", daemon=True
        )
        self._thread.start()
        self._client = self._submit(self._build()).result(30)
        return self

    def __exit__(self, *exc_info: t.Any) -> bool:
        try:
            if self._client is not None:
                self._submit(self._client.aclose()).result(30)
        finally:
            self._client = None
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(5)
        return False

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _build(self) -> Gracy:
        return await self._client_cls(**self._init_kwargs).build()

    def _submit(self, coro: t.Coroutine[t.Any, t.Any, t.Any]) -> t.Any:
        if self._loop is None:
            raise GracyClientClosedError("SyncGracy used outside its 'with' block")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # -- attribute proxying

    def __getattr__(self, name: str) -> t.Any:
        client = self.__dict__.get("_client")
        if client is None:
            raise AttributeError(
                f"{type(self).__name__} has no attribute {name!r} (did you enter the 'with' block?)"
            )
        return self._proxy(getattr(client, name))

    def _proxy(self, attr: t.Any) -> t.Any:
        if isinstance(attr, _BoundNamespace):
            return _SyncNamespaceProxy(self, attr)
        if callable(attr):

            def call(*args: t.Any, **kwargs: t.Any) -> t.Any:
                result = attr(*args, **kwargs)
                if inspect.iscoroutine(result):
                    return self._submit(result).result()
                return result

            return call
        return attr


class _SyncNamespaceProxy:
    """One-level-deep sync proxy for namespace endpoints (api.berry.get_one(...))."""

    def __init__(self, owner: SyncGracy, bound: _BoundNamespace) -> None:
        self._owner = owner
        self._bound = bound

    def __getattr__(self, name: str) -> t.Any:
        return self._owner._proxy(getattr(self._bound, name))


# --------------------------------------------------------------------------- helpers


def _encode_body(body: t.Any) -> tuple[bytes | None, dict[str, str]]:
    """bytes -> raw; str -> utf-8; None -> nothing; anything else -> JSON + content-type."""
    if body is None:
        return None, {}
    if isinstance(body, bytes):
        return body, {}
    if isinstance(body, str):
        return body.encode("utf-8"), {}
    return jsonlib.dumps(body).encode("utf-8"), {"content-type": "application/json"}


def _headers_tuple(*maps: t.Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    merged: dict[str, str] = {}
    for mapping in maps:
        for key, value in mapping.items():
            merged[key.lower()] = str(value)
    return tuple(sorted(merged.items()))


def _resolve_timeout(config: GracyConfig) -> float | None:
    timeout = config.timeout
    if isinstance(timeout, Unset):
        return _DEFAULT_TIMEOUT
    return timeout
