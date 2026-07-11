"""gracy.docs — generate API documentation from a Gracy subclass, statically.

Everything works from the CLASS alone: no instance is created, no scheduler,
transport or monitor is ever started. Zero required dependencies — pydantic is
used lazily for JSON schemas when it happens to be importable.

    from gracy.docs import inspect_api, to_openapi, to_yaml, to_json, to_html

    doc = inspect_api(PokeAPI)         # structured ApiDoc dataclasses
    spec = to_openapi(PokeAPI)         # OpenAPI 3.1.0 dict (with x-gracy blocks)
    print(to_yaml(PokeAPI))            # OpenAPI as YAML (hand-rolled, no pyyaml)

CLI: python -m gracy.docs 'module.path:ClassName' [--format yaml|json|html]
"""

from __future__ import annotations

import json as _json
import typing as t

from gracy.docs._model import (
    ApiDoc,
    ConfigSummary,
    EndpointDoc,
    EndpointGroup,
    ParamDoc,
    ReturnDoc,
    StatusOutcome,
)

if t.TYPE_CHECKING:
    from gracy.client import Gracy

__all__ = [
    "inspect_api",
    "to_openapi",
    "to_yaml",
    "to_json",
    "to_html",
    "ApiDoc",
    "ConfigSummary",
    "EndpointGroup",
    "EndpointDoc",
    "ParamDoc",
    "ReturnDoc",
    "StatusOutcome",
]


def inspect_api(cls: type[Gracy]) -> ApiDoc:
    """Statically introspect a Gracy subclass into an ApiDoc (no instantiation)."""
    from gracy.docs._inspect import collect

    return collect(cls)[0]


def to_openapi(cls: type[Gracy]) -> dict[str, t.Any]:
    """OpenAPI 3.1.0 document as a plain JSON-compatible dict."""
    from gracy.docs._openapi import build_openapi

    return build_openapi(cls)


def to_json(cls: type[Gracy]) -> str:
    """OpenAPI 3.1.0 document as pretty-printed JSON."""
    return _json.dumps(to_openapi(cls), indent=2) + "\n"


def to_yaml(cls: type[Gracy]) -> str:
    """OpenAPI 3.1.0 document as YAML (stdlib-only emitter — no pyyaml)."""
    from gracy.docs._yaml import dumps

    return dumps(to_openapi(cls))


def to_html(cls: type[Gracy]) -> str:
    """Self-contained HTML documentation page (rendered from inspect_api())."""
    from gracy.docs.html import render_html  # lazy: gracy.docs.html ships separately

    return render_html(inspect_api(cls))
