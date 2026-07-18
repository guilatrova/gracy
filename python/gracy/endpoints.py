"""Typed endpoint declarations: @get/@post/... decorators, Path/Query/Header/Body
markers, and the URL helpers shared by client.py and plan.py.

Endpoint stubs use `...` bodies; the decorator returns an EndpointMethod
descriptor. Type hints are evaluated at build() time (make_spec), so
`from __future__ import annotations` and forward refs work.
"""

from __future__ import annotations

import functools
import inspect
import re
import typing as t
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode

from gracy._types import UNSET
from gracy.config import GracyConfig
from gracy.exceptions import GracyConfigError

__all__ = [
    "Path",
    "Query",
    "Header",
    "Body",
    "ParamSpec",
    "EndpointSpec",
    "EndpointMethod",
    "BaseEndpoint",
    "join_url",
    "format_url",
    "append_query",
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
]

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

ParamKind = t.Literal["path", "query", "header", "body"]

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


# --------------------------------------------------------------------------- markers


class Path:
    """Annotated[str, Path] - value fills a {placeholder} in the endpoint path."""


class Query:
    """Annotated[str, Query] - value becomes a query-string parameter."""


class Header:
    """Annotated[str, Header] - value becomes a request header."""


class Body:
    """Annotated[dict, Body] - value becomes the request body (always explicit)."""


_MARKER_KINDS: dict[type, ParamKind] = {Path: "path", Query: "query", Header: "header", Body: "body"}


def _marker_kind(annotation: t.Any) -> ParamKind | None:
    """Extract the Path/Query/Header/Body marker (class OR instance) from Annotated metadata."""
    if t.get_origin(annotation) is not t.Annotated:
        return None
    for meta in t.get_args(annotation)[1:]:
        if isinstance(meta, type) and meta in _MARKER_KINDS:
            return _MARKER_KINDS[meta]
        if type(meta) in _MARKER_KINDS:
            return _MARKER_KINDS[type(meta)]
    return None


# --------------------------------------------------------------------------- specs


@dataclass
class ParamSpec:
    name: str
    kind: ParamKind
    default: t.Any = UNSET  # UNSET = required


@dataclass
class EndpointSpec:
    name: str
    method: str
    path: str
    config: GracyConfig | None
    params: list[ParamSpec]
    return_type: t.Any
    func: t.Callable[..., t.Any] | None
    namespace: str | None = None


# --------------------------------------------------------------------------- URL helpers


def join_url(base: str, path: str) -> str:
    """Join base_url + endpoint path, normalizing slashes. Absolute http(s) paths pass through."""
    if path.lower().startswith(("http://", "https://")):
        return path
    if not path:
        return base
    if not base:
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


def format_url(template: str, args: t.Mapping[str, t.Any]) -> str:
    """Replace {NAME} placeholders with str(value); args match case-insensitively."""
    lowered = {str(k).lower(): v for k, v in args.items()}
    used: set[str] = set()
    missing: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1).lower()
        if key in lowered:
            used.add(key)
            return str(lowered[key])
        missing.append(m.group(1))
        return m.group(0)

    result = _PLACEHOLDER_RE.sub(_sub, template)
    unknown = sorted(k for k in lowered if k not in used)
    if missing or unknown:
        problems: list[str] = []
        if missing:
            problems.append(f"missing args for placeholders {sorted(set(missing))}")
        if unknown:
            problems.append(f"unknown args {unknown}")
        raise GracyConfigError(f"Cannot format URL {template!r}: " + "; ".join(problems))
    return result


def append_query(url: str, params: t.Mapping[str, t.Any]) -> str:
    """Append urlencoded params (skipping None values) with ? or & as appropriate."""
    filtered = {k: v for k, v in params.items() if v is not None}
    if not filtered:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(filtered, doseq=True)}"


# --------------------------------------------------------------------------- descriptor


class EndpointMethod:
    """Descriptor produced by @get/@post/... - turns a `...` stub into a real call.

    Class access returns the descriptor itself (client.build() scans for these);
    instance access returns a bound async callable that routes through
    `obj._call_endpoint(self, arguments_dict)` (obj is a Gracy client OR a
    namespace binding).
    """

    def __init__(
        self,
        method: str,
        path: str,
        config: GracyConfig | None,
        func: t.Callable[..., t.Any],
    ) -> None:
        self.method = method
        self.path = path
        self.config = config
        self.func = func
        self.attr_name: str | None = None
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        self._bind_signature = sig.replace(parameters=params[1:])  # drop `self`

    def __set_name__(self, owner: type, name: str) -> None:
        self.attr_name = name

    def make_spec(self, name: str) -> EndpointSpec:
        """Resolve type hints NOW (build-time - forward refs work) and parse params."""
        hints = t.get_type_hints(self.func, include_extras=True)
        placeholders = {p.lower() for p in _PLACEHOLDER_RE.findall(self.path)}
        params: list[ParamSpec] = []

        for param in self._bind_signature.parameters.values():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                raise GracyConfigError(
                    f"Endpoint {name!r}: *args/**kwargs are not supported on endpoint stubs"
                )
            is_path_name = param.name.lower() in placeholders
            kind = _marker_kind(hints.get(param.name, param.annotation))
            if kind is None:
                kind = "path" if is_path_name else "query"
            elif kind == "path" and not is_path_name:
                raise GracyConfigError(
                    f"Endpoint {name!r}: param {param.name!r} is marked Path but "
                    f"{self.path!r} has no matching {{{param.name}}} placeholder"
                )
            default = UNSET if param.default is inspect.Parameter.empty else param.default
            params.append(ParamSpec(name=param.name, kind=kind, default=default))

        return EndpointSpec(
            name=name,
            method=self.method,
            path=self.path,
            config=self.config,
            params=params,
            return_type=hints.get("return"),
            func=self.func,
        )

    def __get__(self, obj: t.Any, objtype: type | None = None) -> t.Any:
        if obj is None:
            return self

        descriptor = self
        sig = self._bind_signature

        @functools.wraps(self.func)
        async def bound(*args: t.Any, **kwargs: t.Any) -> t.Any:
            ba = sig.bind_partial(*args, **kwargs)
            ba.apply_defaults()
            return await obj._call_endpoint(descriptor, dict(ba.arguments))

        return bound

    def __repr__(self) -> str:
        return f"<EndpointMethod {self.method} {self.path!r} ({self.attr_name or '?'})>"


# --------------------------------------------------------------------------- decorators

_CFG_KEYS = frozenset(
    {
        "on",
        "retry",
        "status_policy",
        "validators",
        "throttle",
        "concurrency",
        "queue",
        "log_request",
        "log_response",
        "log_errors",
        "decoder",
        "timeout",
    }
)


def _endpoint_decorator(method: str, path: str, cfg: dict[str, t.Any]) -> t.Callable[[F], F]:
    unknown = sorted(set(cfg) - _CFG_KEYS)
    if unknown:
        raise GracyConfigError(
            f"Unknown @{method.lower()}() config kwargs {unknown}; allowed: {sorted(_CFG_KEYS)}"
        )
    # Only the provided kwargs are set (missing = UNSET = inherit); an explicit
    # None disables the knob. No kwargs at all -> no endpoint-level config layer.
    config = GracyConfig(**cfg) if cfg else None

    def decorate(func: F) -> F:
        return t.cast(F, EndpointMethod(method=method, path=path, config=config, func=func))

    return decorate


def get(path: str, **cfg: t.Any) -> t.Callable[[F], F]:
    return _endpoint_decorator("GET", path, cfg)


def post(path: str, **cfg: t.Any) -> t.Callable[[F], F]:
    return _endpoint_decorator("POST", path, cfg)


def put(path: str, **cfg: t.Any) -> t.Callable[[F], F]:
    return _endpoint_decorator("PUT", path, cfg)


def patch(path: str, **cfg: t.Any) -> t.Callable[[F], F]:
    return _endpoint_decorator("PATCH", path, cfg)


def delete(path: str, **cfg: t.Any) -> t.Callable[[F], F]:
    return _endpoint_decorator("DELETE", path, cfg)


def head(path: str, **cfg: t.Any) -> t.Callable[[F], F]:
    return _endpoint_decorator("HEAD", path, cfg)


def options(path: str, **cfg: t.Any) -> t.Callable[[F], F]:
    return _endpoint_decorator("OPTIONS", path, cfg)


# --------------------------------------------------------------------------- v1 compat


class BaseEndpoint(str, Enum):
    """v1 compat: enum of endpoint templates, used with api.request()."""

    def __str__(self) -> str:
        return self.value
