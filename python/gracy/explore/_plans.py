"""Session data -> EndpointPlan list (the planning half of codegen).

Shared quoting/naming helpers plus the plan builder that turns recorded
steps into ``EndpointPlan``s consumed by the render functions in
``gracy.explore._codegen``.
"""

from __future__ import annotations

import base64
import json
import typing as t
from dataclasses import dataclass, field

from gracy.explore._infer import InferredModel, dedupe_models, infer, pascal, safe_identifier
from gracy.explore._session import _ENV_RE, split_segments


def dq(value: str) -> str:
    """Double-quoted python string literal (json escapes are python-valid)."""
    return json.dumps(value)


def class_name_for(stem: str) -> str:
    name = pascal(stem)
    return name if name.isidentifier() else "ExploredAPI"


def env_expr(value: str) -> str:
    """'Bearer $TOK' -> '"Bearer " + os.environ.get("TOK", "")' (literal when no vars)."""
    parts: list[str] = []
    pos = 0
    for m in _ENV_RE.finditer(value):
        if m.start() > pos:
            parts.append(dq(value[pos : m.start()]))
        var = m.group(1) or m.group(2)
        parts.append(f'os.environ.get("{var}", "")')
        pos = m.end()
    if pos < len(value):
        parts.append(dq(value[pos:]))
    return " + ".join(parts) if parts else '""'


# --------------------------------------------------------------------------- planning


@dataclass
class EndpointPlan:
    name: str  # sanitized method name
    method: str
    template: str
    path_params: list[str]  # in template order
    query_params: list[tuple[str, str]]  # (name, default) in signature order
    body_kind: str | None  # None | "json" | "raw"
    request_model: InferredModel | None
    response_model: InferredModel | None  # root model (or list item model)
    response_shape: str  # "model" | "list_model" | "dict" | "list" | "str" | "any"
    on: dict[int, str]
    allow_codes: list[int]
    test_step: dict[str, t.Any] | None
    test_path_args: dict[str, str] = field(default_factory=dict)


def _parse_response(step: dict[str, t.Any]) -> t.Any:
    if "response_json" in step:
        return step["response_json"]
    raw = step.get("response_body_b64")
    if not raw:
        return None
    body = base64.b64decode(raw)
    try:
        return json.loads(body)
    except ValueError:
        return body.decode("utf-8", "replace")


def _step_has_response(step: dict[str, t.Any]) -> bool:
    return "response_json" in step or bool(step.get("response_body_b64"))


def _response_body_bytes(step: dict[str, t.Any]) -> bytes:
    """The exact response bytes for the replay cassette. JSON responses are
    stored parsed (never truncated), so re-serialize them; else use the blob."""
    if "response_json" in step:
        return json.dumps(step["response_json"]).encode("utf-8")
    raw = step.get("response_body_b64")
    return base64.b64decode(raw) if raw else b""


def _path_args_for(template: str, path: str) -> dict[str, str]:
    args: dict[str, str] = {}
    for t_seg, p_seg in zip(split_segments(template), split_segments(path)):
        if t_seg.startswith("{") and t_seg.endswith("}"):
            args[t_seg[1:-1]] = p_seg
    return args


def build_plans(data: dict[str, t.Any]) -> list[EndpointPlan]:
    plans: list[EndpointPlan] = []
    for raw_name, ep in data.get("endpoints", {}).items():
        steps = [s for s in data.get("steps", []) if s.get("endpoint") == raw_name]
        name = safe_identifier(raw_name)
        template = ep["template"]
        path_params = [seg[1:-1] for seg in split_segments(template) if seg.startswith("{") and seg.endswith("}")]

        # -- query params: first-seen order, last-seen value wins as the default
        query_order: list[str] = []
        query_defaults: dict[str, str] = {}
        for step in steps:
            for key, value in (step.get("query") or {}).items():
                if not str(key).isidentifier() or key in path_params:
                    continue
                if key not in query_defaults:
                    query_order.append(key)
                query_defaults[key] = str(value)

        # -- request body
        body_kind: str | None = None
        request_model: InferredModel | None = None
        json_bodies = [s["body_json"] for s in steps if "body_json" in s]
        if json_bodies:
            body_kind = "json"
            dict_bodies = [b for b in json_bodies if isinstance(b, dict)]
            if dict_bodies and len(dict_bodies) == len(json_bodies):
                request_model = infer(dict_bodies, ep.get("request_model") or pascal(raw_name) + "Request")
        elif any("body_b64" in s for s in steps):
            body_kind = "raw"

        # -- on map + status policy
        on = {int(k): str(v) for k, v in (ep.get("on") or {}).items()}
        seen_statuses = {s["status"] for s in steps if s.get("status") is not None}
        allow_codes = sorted(c for c in seen_statuses if not (200 <= c < 300) and c not in on)

        # -- response shape from samples (prefer 2xx, un-mapped steps)
        sample_steps = [
            s for s in steps if s.get("status") is not None and 200 <= s["status"] < 300 and s["status"] not in on
        ]
        if not sample_steps:
            sample_steps = [s for s in steps if s.get("status") is not None and s["status"] not in on]
        samples = [_parse_response(s) for s in sample_steps]
        samples = [s for s in samples if s is not None]

        response_model: InferredModel | None = None
        shape = "str"
        if samples:
            if all(isinstance(s, dict) for s in samples):
                shape = "model"
                response_model = infer(samples, ep.get("response_model") or pascal(raw_name) + "Response")
            elif all(isinstance(s, list) for s in samples):
                items = [item for s in samples for item in s]
                if items and all(isinstance(i, dict) for i in items):
                    shape = "list_model"
                    response_model = infer(items, ep.get("response_model") or pascal(raw_name) + "ResponseItem")
                else:
                    shape = "list"
            elif all(isinstance(s, str) for s in samples):
                shape = "str"
            else:
                shape = "any"

        # -- the step the generated test replays (last one with a response)
        test_step = next(
            (s for s in reversed(steps) if s.get("status") is not None and _step_has_response(s)), None
        )

        plan = EndpointPlan(
            name=name,
            method=ep["method"],
            template=template,
            path_params=path_params,
            query_params=[(k, query_defaults[k]) for k in query_order],
            body_kind=body_kind,
            request_model=request_model,
            response_model=response_model,
            response_shape=shape,
            on=on,
            allow_codes=allow_codes,
            test_step=test_step,
        )
        if test_step is not None:
            plan.test_path_args = _path_args_for(template, test_step["path"])
        plans.append(plan)
    return plans


def _collect_models(plans: t.Sequence[EndpointPlan]) -> tuple[list[InferredModel], dict[str, str]]:
    roots: list[InferredModel] = []
    for plan in plans:
        if plan.request_model is not None:
            roots.append(plan.request_model)
        if plan.response_model is not None:
            roots.append(plan.response_model)
    return dedupe_models(roots)


def _exception_names(plans: t.Sequence[EndpointPlan]) -> list[str]:
    names: list[str] = []
    for plan in plans:
        for action in plan.on.values():
            if action.startswith("raise:") and action[len("raise:") :] not in names:
                names.append(action[len("raise:") :])
    return names
