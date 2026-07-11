"""STATIC introspection of a Gracy subclass into the doc model.

Mirrors Gracy._collect_specs() — walk the MRO for EndpointMethod descriptors
and GracyNamespace instances — but works from the CLASS alone: nothing is
instantiated, no scheduler/transport/monitor is ever started.
"""

from __future__ import annotations

import copy
import inspect
import types
import typing as t

from gracy._types import UNSET, Unset
from gracy.config import (
    Backoff,
    Concurrency,
    GracyConfig,
    Queue,
    Raises,
    Rate,
    Retry,
    StatusPolicy,
    StatusSet,
    Throttle,
)
from gracy.docs._model import (
    ApiDoc,
    ConfigSummary,
    EndpointDoc,
    EndpointGroup,
    ParamDoc,
    ReturnDoc,
    StatusOutcome,
)
from gracy.endpoints import EndpointMethod, EndpointSpec, join_url

if t.TYPE_CHECKING:
    from gracy.client import Gracy

__all__ = ["collect", "SpecIndex"]

# (group name, endpoint name) -> EndpointSpec. "" = root group. Side channel for
# the OpenAPI emitter, which needs the real type objects for schema generation.
SpecIndex = t.Dict[t.Tuple[str, str], EndpointSpec]


# --------------------------------------------------------------------------- helpers


def _is_set(value: t.Any) -> bool:
    """True when a config field is neither UNSET (inherit) nor None (disabled)."""
    return value is not None and not isinstance(value, Unset)


def _split_doc(doc: str | None) -> tuple[str, str]:
    """Docstring -> (first line, dedented remainder)."""
    if not doc:
        return "", ""
    cleaned = inspect.cleandoc(doc)
    first, _, rest = cleaned.partition("\n")
    return first.strip(), rest.strip()


def strip_annotated(tp: t.Any) -> t.Any:
    """Annotated[X, ...] -> X (recursively at the top level)."""
    while hasattr(tp, "__metadata__"):
        tp = tp.__origin__
    return tp


def type_repr(tp: t.Any) -> str:
    """Human type string: Pokemon | None, list[dict], str, ..."""
    tp = strip_annotated(tp)
    if tp is None or tp is types.NoneType:
        return "None"
    if tp is t.Any:
        return "Any"
    origin = t.get_origin(tp)
    if origin is t.Union or origin is types.UnionType:
        return " | ".join(type_repr(arm) for arm in t.get_args(tp))
    if origin is not None:
        name = getattr(origin, "__name__", None) or str(origin)
        args = t.get_args(tp)
        if not args:
            return str(name)
        return f"{name}[{', '.join(type_repr(a) for a in args)}]"
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else str(tp)


def split_optional(tp: t.Any) -> tuple[t.Any, bool]:
    """'Pokemon | None' -> (Pokemon, True); anything else -> (tp, False)."""
    tp = strip_annotated(tp)
    origin = t.get_origin(tp)
    if origin is t.Union or origin is types.UnionType:
        args = t.get_args(tp)
        non_none = tuple(a for a in args if a is not types.NoneType)
        if non_none and len(non_none) != len(args):
            base = non_none[0] if len(non_none) == 1 else t.Union[non_none]  # noqa: UP007
            return base, True
    return tp, False


def json_schema_for(tp: t.Any, ref_template: str | None = None) -> dict[str, t.Any] | None:
    """JSON schema via pydantic (model_json_schema / TypeAdapter) — lazy and optional.

    Returns None when pydantic is not importable or the type is not adaptable.
    """
    if tp is None or tp is types.NoneType or tp is t.Any:
        return None
    try:
        import pydantic
    except ImportError:
        return None
    kwargs: dict[str, t.Any] = {"ref_template": ref_template} if ref_template else {}
    try:
        if isinstance(tp, type) and issubclass(tp, pydantic.BaseModel):
            return tp.model_json_schema(**kwargs)
        return pydantic.TypeAdapter(tp).json_schema(**kwargs)
    except Exception:  # noqa: BLE001 - non-adaptable annotation: no schema
        return None


# --------------------------------------------------------------------------- humanizers


def humanize_retry(retry: Retry) -> str:
    on = retry.on if isinstance(retry.on, tuple) else (retry.on,)
    triggers: list[str] = []
    for item in on:
        if isinstance(item, StatusSet):
            triggers.append("/".join(str(c) for c in item.codes))
        else:
            triggers.append(getattr(item, "__name__", str(item)))
    if isinstance(retry.wait, Backoff):
        wait = f"backoff {retry.wait.initial:g}s x{retry.wait.multiplier:g}"
        if retry.wait.max is not None:
            wait += f" (max {retry.wait.max:g}s)"
        if retry.wait.jitter:
            wait += " with jitter"
    else:
        wait = f"wait {retry.wait:g}s"
    return f"{retry.attempts} attempts on {', '.join(triggers)}, {wait}"


def humanize_rate(rate: Rate) -> str:
    per = rate.per if isinstance(rate.per, str) else f"{rate.per_seconds:g}s"
    return f"{rate.limit} req / {per} on {rate.match}"


def humanize_throttle(throttle: Throttle) -> list[str]:
    return [humanize_rate(rate) for rate in throttle.rules]


def humanize_concurrency(conc: Concurrency | int) -> str:
    conc = Concurrency(limit=conc) if isinstance(conc, int) else conc
    text = f"max {conc.limit} concurrent"
    if conc.per_uurl:
        text += " per endpoint"
    if conc.match:
        text += f" on {conc.match}"
    if conc.key_by:
        text += f" keyed by {', '.join(conc.key_by)}"
    return text


def humanize_queue(queue: Queue) -> str:
    parts: list[str] = []
    if queue.max_at_once is not None:
        parts.append(f"max {queue.max_at_once} in flight")
    parts.append(f"max_pending {queue.max_pending}")
    parts.append(f"on_full={queue.on_full}")
    if queue.pause_on_status:
        parts.append("pauses on " + "/".join(str(s) for s in queue.pause_on_status))
    return ", ".join(parts)


def humanize_status_policy(policy: StatusPolicy) -> str | None:
    codes = "/".join(str(c) for c in policy.codes)
    if policy.kind == "strict":
        return f"only {codes} accepted (strict)"
    if policy.kind == "allow":
        return f"2xx or {codes} accepted"
    return None


def _config_summary(config: GracyConfig | None) -> ConfigSummary:
    if config is None:
        return ConfigSummary()
    return ConfigSummary(
        retry=humanize_retry(config.retry) if _is_set(config.retry) else None,
        throttle=humanize_throttle(t.cast(Throttle, config.throttle)) if _is_set(config.throttle) else [],
        concurrency=humanize_concurrency(config.concurrency) if _is_set(config.concurrency) else None,
        queue=humanize_queue(t.cast(Queue, config.queue)) if _is_set(config.queue) else None,
        decoder=type(config.decoder).__name__ if _is_set(config.decoder) else None,
    )


def _config_notes(config: GracyConfig | None) -> list[str]:
    """Endpoint-level overrides as one-liners (`on=` is covered by status_map)."""
    if config is None:
        return []
    notes: list[str] = []
    if not isinstance(config.retry, Unset):
        notes.append("retry disabled" if config.retry is None else "retry: " + humanize_retry(config.retry))
    if not isinstance(config.throttle, Unset):
        if config.throttle is None:
            notes.append("throttle disabled")
        else:
            notes.append("throttle: " + "; ".join(humanize_throttle(config.throttle)))
    if not isinstance(config.concurrency, Unset):
        if config.concurrency is None:
            notes.append("concurrency unlimited")
        else:
            notes.append("concurrency: " + humanize_concurrency(config.concurrency))
    if not isinstance(config.queue, Unset):
        notes.append("queue: " + humanize_queue(t.cast(Queue, config.queue)))
    if _is_set(config.status_policy):
        policy = humanize_status_policy(t.cast(StatusPolicy, config.status_policy))
        if policy:
            notes.append(policy)
    if not isinstance(config.timeout, Unset):
        notes.append("no timeout" if config.timeout is None else f"timeout {config.timeout:g}s")
    if _is_set(config.decoder):
        notes.append(f"decoder: {type(config.decoder).__name__}")
    return notes


# --------------------------------------------------------------------------- status map


def _resolve_on(*layers: GracyConfig | None) -> t.Any:
    """Innermost-first layers; first one that SETS `on` wins (whole-field semantics)."""
    for config in layers:
        if config is not None and not isinstance(config.on, Unset):
            return config.on
    return None


def _status_map(on: t.Any) -> list[StatusOutcome]:
    if not on:
        return []
    outcomes: list[StatusOutcome] = []
    for key, action in on.items():
        status: int | str = int(key) if isinstance(key, int) else str(key)
        if isinstance(action, Raises):
            exc = action.exc
            detail = getattr(exc, "BASE_MESSAGE", None)
            outcomes.append(StatusOutcome(status, f"raises {exc.__name__}", detail))
        elif action is None:
            outcomes.append(StatusOutcome(status, "returns None"))
        elif callable(action):
            outcomes.append(StatusOutcome(status, "custom parser", getattr(action, "__name__", None)))
        else:
            outcomes.append(StatusOutcome(status, f"returns {action!r}"))
    return outcomes


# --------------------------------------------------------------------------- endpoints


def _return_doc(return_type: t.Any) -> ReturnDoc:
    tp = strip_annotated(return_type)
    if tp is None:
        return ReturnDoc(type_repr="Any")
    base, optional = split_optional(tp)
    return ReturnDoc(type_repr=type_repr(tp), json_schema=json_schema_for(base), is_optional=optional)


def _endpoint_doc(
    name: str,
    em: EndpointMethod,
    client_config: GracyConfig | None,
    ns_config: GracyConfig | None,
) -> tuple[EndpointDoc, EndpointSpec]:
    spec = em.make_spec(name)
    hints = t.get_type_hints(em.func, include_extras=True)

    params: list[ParamDoc] = []
    for p in spec.params:
        annotation = hints.get(p.name)
        params.append(
            ParamDoc(
                name=p.name,
                kind=p.kind,
                type_repr=type_repr(annotation) if annotation is not None else "Any",
                required=p.default is UNSET,
                default_repr=None if p.default is UNSET else repr(p.default),
            )
        )

    summary, description = _split_doc(em.func.__doc__)
    doc = EndpointDoc(
        name=name.rpartition(".")[2],
        http_method=em.method,
        path=em.path,
        summary=summary,
        description=description,
        params=params,
        returns=_return_doc(spec.return_type),
        status_map=_status_map(_resolve_on(spec.config, ns_config, client_config)),
        config_notes=_config_notes(spec.config),
    )
    return doc, spec


# --------------------------------------------------------------------------- collect


def collect(cls: type[Gracy]) -> tuple[ApiDoc, SpecIndex]:
    """Walk the class (never an instance) into (ApiDoc, spec index).

    Exactly mirrors Gracy._collect_specs()'s MRO walk, so the docs always match
    what build() would compile — without starting anything.
    """
    from gracy import __version__
    from gracy.client import GracyNamespace  # runtime isinstance check

    root_endpoints: list[tuple[str, EndpointMethod]] = []
    namespaces: list[tuple[str, GracyNamespace]] = []
    seen: set[str] = set()
    for klass in cls.__mro__:
        for attr, value in vars(klass).items():
            if attr in seen:
                continue
            if isinstance(value, EndpointMethod):
                seen.add(attr)
                root_endpoints.append((attr, value))
            elif isinstance(value, GracyNamespace):
                seen.add(attr)
                namespaces.append((attr, value))

    client_config: GracyConfig | None = cls.config
    groups: list[EndpointGroup] = []
    spec_index: SpecIndex = {}

    if root_endpoints:
        endpoint_docs: list[EndpointDoc] = []
        for attr, em in root_endpoints:
            doc, spec = _endpoint_doc(attr, em, client_config, None)
            endpoint_docs.append(doc)
            spec_index[("", attr)] = spec
        groups.append(EndpointGroup(name="", path_prefix="", endpoints=endpoint_docs))

    for ns_attr, ns in namespaces:
        endpoint_docs = []
        seen_ns: set[str] = set()
        for klass in type(ns).__mro__:
            for attr, value in vars(klass).items():
                if attr in seen_ns or not isinstance(value, EndpointMethod):
                    continue
                seen_ns.add(attr)
                # Same trick as Gracy._collect_namespace_specs: re-spec against
                # the prefixed path so placeholders in path_prefix validate too.
                em = copy.copy(value)
                em.path = join_url(ns.path_prefix, value.path)
                doc, spec = _endpoint_doc(f"{ns_attr}.{attr}", em, client_config, ns.config)
                spec.namespace = ns_attr
                endpoint_docs.append(doc)
                spec_index[(ns_attr, attr)] = spec
        groups.append(EndpointGroup(name=ns_attr, path_prefix=ns.path_prefix, endpoints=endpoint_docs))

    title, description = _split_doc(cls.__doc__)
    api = ApiDoc(
        name=cls.__name__,
        title=title or cls.__name__,
        description=description,
        base_url=cls.base_url,
        version=__version__,
        config_summary=_config_summary(client_config),
        groups=groups,
    )
    return api, spec_index
