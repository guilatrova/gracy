"""Compile-once plan: config chain resolved at build() into plain data.

The scheduler (Python or Rust) receives SCHEDULER-side data as a JSON-able
dict — this shape is the FFI contract with crates/gracy-core/src/plan.rs:

{
  "throttle": {
    "mode": "exact" | "smooth",
    "rules": [{"id": int, "match": str-regex-vs-formatted-URL, "limit": int, "per": float-seconds}]
  },
  "concurrency": [
    {"id": int, "match": str-regex-vs-uurl | null, "limit": int, "per_uurl": bool}
  ],
  "queue": {"max_at_once": int|null, "max_pending": int, "on_full": "wait"|"raise",
            "throttle_in_hooks": bool}
}

POLICY-side data (retry, validators, parsing, hooks, logging) never crosses
the FFI — it stays on CompiledRoute / CompiledPlan for pipeline.py.
"""

from __future__ import annotations

import re
import typing as t
from dataclasses import dataclass, field

from gracy.config import (
    Concurrency,
    GracyConfig,
    Queue,
    Rate,
    Throttle,
    apply_url_overrides,
    resolve_chain,
)
from gracy._types import Unset

if t.TYPE_CHECKING:
    from gracy.endpoints import EndpointSpec


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def template_to_regex(url_template: str) -> str:
    """'https://api/pokemon/{NAME}' -> escaped regex with placeholders as [^/]+."""
    parts = _PLACEHOLDER_RE.split(url_template)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(re.escape(part))
        else:
            out.append(r"[^/]+")
    return "".join(out)


@dataclass
class CompiledRoute:
    """One endpoint (or the ad-hoc request route) with its fully-resolved policy config."""

    name: str
    method: str
    url_template: str  # absolute uurl
    config: GracyConfig  # fully resolved (chain + url-glob overrides at template level)
    spec: EndpointSpec | None = None


@dataclass
class CompiledPlan:
    routes: dict[str, CompiledRoute] = field(default_factory=dict)
    scheduler_plan: dict[str, t.Any] = field(default_factory=dict)
    base_config: GracyConfig = None  # type: ignore[assignment]  # set by compile_plan
    has_before_hooks: bool = False
    has_after_hooks: bool = False

    def explain(self) -> str:
        import json as _json
        import dataclasses as _dc

        lines = ["== scheduler plan =="]
        lines.append(_json.dumps(self.scheduler_plan, indent=2))
        lines.append("== routes ==")
        for name, route in self.routes.items():
            cfg = {
                f.name: repr(getattr(route.config, f.name))
                for f in _dc.fields(route.config)
                if not isinstance(getattr(route.config, f.name), Unset)
            }
            lines.append(f"{name}: {route.method} {route.url_template}\n  {cfg}")
        return "\n".join(lines)


def _collect_scheduler_plan(
    base: GracyConfig,
    routes: t.Mapping[str, CompiledRoute],
) -> dict[str, t.Any]:
    throttle_rules: list[dict[str, t.Any]] = []
    conc_rules: list[dict[str, t.Any]] = []
    mode = "exact"

    def add_throttle(th: Throttle | None | Unset, scope_regex: str | None) -> None:
        nonlocal mode
        if not th or isinstance(th, Unset):
            return
        mode = th.mode
        for rate in th.rules:
            match = rate.match
            if scope_regex is not None and match == r".*":
                match = scope_regex  # endpoint-scoped rule: bind to the endpoint's URL shape
            throttle_rules.append(
                {"id": len(throttle_rules), "match": match, "limit": rate.limit, "per": rate.per_seconds}
            )

    def add_conc(c: Concurrency | int | None | Unset, scope_uurl_regex: str | None) -> None:
        if not c or isinstance(c, Unset):
            return
        conc = Concurrency(limit=c) if isinstance(c, int) else c
        match = conc.match
        if match is None and scope_uurl_regex is not None:
            match = scope_uurl_regex
        conc_rules.append(
            {"id": len(conc_rules), "match": match, "limit": conc.limit, "per_uurl": conc.per_uurl}
        )

    add_throttle(t.cast("Throttle | None | Unset", base.throttle), None)
    add_conc(base.concurrency, None)

    for route in routes.values():
        scope = template_to_regex(route.url_template)
        if route.config.throttle is not base.throttle:
            add_throttle(t.cast("Throttle | None | Unset", route.config.throttle), scope)
        if route.config.concurrency is not base.concurrency:
            add_conc(route.config.concurrency, re.escape(route.url_template))

    queue = base.queue if not isinstance(base.queue, Unset) else Queue()
    assert isinstance(queue, Queue)
    return {
        "throttle": {"mode": mode, "rules": throttle_rules},
        "concurrency": conc_rules,
        "queue": {
            "max_at_once": queue.max_at_once,
            "max_pending": queue.max_pending,
            "on_full": queue.on_full,
            "throttle_in_hooks": queue.throttle_in_hooks,
        },
    }


def compile_plan(
    client_config: GracyConfig | None,
    namespace_configs: t.Mapping[str, GracyConfig | None],
    endpoint_specs: t.Mapping[str, EndpointSpec],
    base_url: str,
    timeout: float | None | Unset,
) -> CompiledPlan:
    """Resolve the full precedence chain once. Called by Gracy.build()."""
    from gracy.endpoints import join_url

    base = resolve_chain(client_config)
    if not isinstance(timeout, Unset):
        base = GracyConfig(timeout=timeout).merged_under(base)

    plan = CompiledPlan(base_config=base)

    for name, spec in endpoint_specs.items():
        ns_config = namespace_configs.get(spec.namespace or "", None)
        layers = [client_config]
        if ns_config is not None:
            layers.append(ns_config)
        if spec.config is not None:
            layers.append(spec.config)
        resolved = resolve_chain(*layers)
        if not isinstance(timeout, Unset):
            resolved = GracyConfig(timeout=timeout).merged_under(resolved) if spec.config is None else resolved
        url_template = join_url(base_url, spec.path)
        resolved = apply_url_overrides(resolved, url_template)
        plan.routes[name] = CompiledRoute(
            name=name,
            method=spec.method,
            url_template=url_template,
            config=resolved,
            spec=spec,
        )

    plan.scheduler_plan = _collect_scheduler_plan(base, plan.routes)
    return plan
