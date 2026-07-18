"""The doc model: the shared contract between introspection (inspect_api) and
the emitters (OpenAPI/YAML/JSON on one side, HTML on the other).

Plain frozen dataclasses, stdlib-only. Everything here is already humanized -
emitters never need to touch GracyConfig objects again.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamDoc:
    name: str
    kind: str  # "path" | "query" | "header" | "body"
    type_repr: str  # e.g. "str", "int"
    required: bool
    default_repr: str | None = None  # repr() of the default; None when required


@dataclass(frozen=True)
class ReturnDoc:
    type_repr: str  # e.g. "Pokemon | None"
    json_schema: dict[str, t.Any] | None = None  # pydantic schema when derivable
    is_optional: bool = False


@dataclass(frozen=True)
class StatusOutcome:
    status: int | str  # HTTP status code or "default"
    outcome: str  # "returns None" | "returns <literal>" | "raises X" | "custom parser"
    detail: str | None = None  # e.g. the exception's BASE_MESSAGE


@dataclass(frozen=True)
class EndpointDoc:
    name: str  # method name as declared on the class
    http_method: str  # "GET", "POST", ...
    path: str  # full path, namespace prefix included
    summary: str  # docstring first line ("" when absent)
    description: str  # docstring remainder ("" when absent)
    params: list[ParamDoc] = field(default_factory=list)
    returns: ReturnDoc = field(default_factory=lambda: ReturnDoc(type_repr="Any"))
    status_map: list[StatusOutcome] = field(default_factory=list)
    config_notes: list[str] = field(default_factory=list)  # per-endpoint overrides


@dataclass(frozen=True)
class EndpointGroup:
    name: str  # "" for root endpoints, else the namespace attribute name
    path_prefix: str
    endpoints: list[EndpointDoc] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigSummary:
    retry: str | None = None  # "3 attempts on 429/502/503, backoff 0.5s x2"
    throttle: list[str] = field(default_factory=list)  # ["5 req / 1s on .*"]
    concurrency: str | None = None
    queue: str | None = None
    decoder: str | None = None


@dataclass(frozen=True)
class ApiDoc:
    name: str  # class name
    title: str  # class docstring first line, or class name
    description: str  # rest of the class docstring
    base_url: str
    version: str  # gracy.__version__
    config_summary: ConfigSummary = field(default_factory=ConfigSummary)
    groups: list[EndpointGroup] = field(default_factory=list)
