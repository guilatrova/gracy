"""OpenAPI 3.1.0 emitter, built on the static introspection in _inspect.py.

Gracy-specific behavior that has no OpenAPI vocabulary (retry/throttle/queue
summaries, per-endpoint overrides) rides in `x-gracy` extension blocks.
"""

from __future__ import annotations

import types
import typing as t

from gracy._types import UNSET
from gracy.docs._inspect import collect, split_optional, strip_annotated
from gracy.docs._model import EndpointDoc, EndpointGroup, ParamDoc, StatusOutcome
from gracy.endpoints import EndpointSpec

if t.TYPE_CHECKING:
    from gracy.client import Gracy

__all__ = ["build_openapi"]

_REF_TEMPLATE = "#/components/schemas/{model}"
_SCALAR_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}


# --------------------------------------------------------------------------- schemas


def _type_schema(tp: t.Any, components: dict[str, t.Any]) -> dict[str, t.Any]:
    """Schema for a real type object; pydantic models are hoisted into components."""
    tp = strip_annotated(tp)
    try:
        import pydantic
    except ImportError:
        return {}
    try:
        if isinstance(tp, type) and issubclass(tp, pydantic.BaseModel):
            schema = tp.model_json_schema(ref_template=_REF_TEMPLATE)
            for name, sub in schema.pop("$defs", {}).items():
                components.setdefault(name, sub)
            components.setdefault(tp.__name__, schema)
            return {"$ref": f"#/components/schemas/{tp.__name__}"}
        schema = pydantic.TypeAdapter(tp).json_schema(ref_template=_REF_TEMPLATE)
        for name, sub in schema.pop("$defs", {}).items():
            components.setdefault(name, sub)
        return schema
    except Exception:  # noqa: BLE001 - non-adaptable annotation: generic schema
        return {}


def _param_schema(param: ParamDoc, default: t.Any) -> dict[str, t.Any]:
    """Schema from the humanized type_repr (str/int/float/bool -> JSON types)."""
    schema: dict[str, t.Any]
    if param.type_repr in _SCALAR_TYPES:
        schema = {"type": _SCALAR_TYPES[param.type_repr]}
    elif param.type_repr == "dict" or param.type_repr.startswith("dict["):
        schema = {"type": "object"}
    elif param.type_repr == "list" or param.type_repr.startswith("list["):
        schema = {"type": "array"}
    else:
        schema = {}
    if default is not UNSET and isinstance(default, (str, int, float, bool, types.NoneType)):
        schema["default"] = default
    return schema


def _return_content_schema(
    return_type: t.Any, components: dict[str, t.Any]
) -> dict[str, t.Any] | None:
    tp = strip_annotated(return_type)
    if tp is None or tp is types.NoneType:
        return None
    base, optional = split_optional(tp)
    schema = _type_schema(base, components)
    if optional:
        schema = {"anyOf": [schema, {"type": "null"}]}
    return schema


# --------------------------------------------------------------------------- operations


def _outcome_description(outcome: StatusOutcome) -> str:
    description = "returns null" if outcome.outcome == "returns None" else outcome.outcome
    if outcome.detail:
        description = f"{description}: {outcome.detail}"
    return description


def _operation(
    group: EndpointGroup,
    endpoint: EndpointDoc,
    spec: EndpointSpec,
    components: dict[str, t.Any],
) -> dict[str, t.Any]:
    op: dict[str, t.Any] = {
        "operationId": f"{group.name}.{endpoint.name}" if group.name else endpoint.name
    }
    if endpoint.summary:
        op["summary"] = endpoint.summary
    if endpoint.description:
        op["description"] = endpoint.description

    defaults = {p.name: p.default for p in spec.params}
    hints: dict[str, t.Any] = {}
    if spec.func is not None:
        try:
            hints = t.get_type_hints(spec.func, include_extras=True)
        except Exception:  # noqa: BLE001 - unresolvable forward refs: schemas degrade to {}
            hints = {}

    parameters: list[dict[str, t.Any]] = []
    for param in endpoint.params:
        if param.kind == "body":
            body_schema = (
                _type_schema(hints[param.name], components)
                if param.name in hints
                else _param_schema(param, UNSET)
            )
            op["requestBody"] = {
                "required": param.required,
                "content": {"application/json": {"schema": body_schema or {"type": "object"}}},
            }
            continue
        parameters.append(
            {
                "name": param.name,
                "in": param.kind,
                "required": param.required or param.kind == "path",
                "schema": _param_schema(param, defaults.get(param.name, UNSET)),
            }
        )
    if parameters:
        op["parameters"] = parameters

    responses: dict[str, t.Any] = {}
    success: dict[str, t.Any] = {"description": "Successful response"}
    schema = _return_content_schema(spec.return_type, components)
    if schema is not None:
        success["content"] = {"application/json": {"schema": schema}}
    responses["200"] = success
    for outcome in endpoint.status_map:
        key = str(outcome.status)
        description = _outcome_description(outcome)
        if key in responses:
            responses[key]["description"] = description
        else:
            responses[key] = {"description": description}
    op["responses"] = responses

    if endpoint.config_notes:
        op["x-gracy"] = {"overrides": list(endpoint.config_notes)}
    return op


# --------------------------------------------------------------------------- document


def build_openapi(cls: type[Gracy]) -> dict[str, t.Any]:
    doc, spec_index = collect(cls)
    components: dict[str, t.Any] = {}
    paths: dict[str, dict[str, t.Any]] = {}

    for group in doc.groups:
        for endpoint in group.endpoints:
            spec = spec_index[(group.name, endpoint.name)]
            op = _operation(group, endpoint, spec, components)
            method = endpoint.http_method.lower()
            path_key = endpoint.path
            if method in paths.get(path_key, {}):
                # Two endpoints share (path, method) — OpenAPI cannot express that,
                # so the later one gets a disambiguated key instead of vanishing.
                path_key = f"{endpoint.path}#{op['operationId']}"
            paths.setdefault(path_key, {})[method] = op

    info: dict[str, t.Any] = {"title": doc.title, "version": doc.version}
    if doc.description:
        info["description"] = doc.description

    document: dict[str, t.Any] = {
        "openapi": "3.1.0",
        "info": info,
        "servers": [{"url": doc.base_url}],
        "paths": paths,
    }
    if components:
        document["components"] = {"schemas": components}

    summary = doc.config_summary
    x_gracy: dict[str, t.Any] = {}
    if summary.retry:
        x_gracy["retry"] = summary.retry
    if summary.throttle:
        x_gracy["throttle"] = list(summary.throttle)
    if summary.concurrency:
        x_gracy["concurrency"] = summary.concurrency
    if summary.queue:
        x_gracy["queue"] = summary.queue
    if summary.decoder:
        x_gracy["decoder"] = summary.decoder
    if x_gracy:
        document["x-gracy"] = x_gracy
    return document
