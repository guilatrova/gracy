"""Session data -> generated gracy v2 client module (+ optional replay tests).

The generated module is import-clean, pyright-friendly, and deterministic:
pydantic models (deduped), user exceptions for ``raise:`` on-actions, a Gracy
subclass with @get/@post/... endpoints (on= maps, Annotated params, inferred
request/response models), policies in GracyConfig, and header/auth policies as
a ``TransportConfig`` whose env-var placeholders become ``os.environ.get()``.

``save_code(tests=True)`` also emits ``test_<stem>.py`` + a SqliteStorage
cassette built from the recorded steps, so the tests run green offline.
"""

from __future__ import annotations

import ast
import base64
import json
import typing as t
from dataclasses import dataclass, field
from pathlib import Path

from gracy._types import RequestSpec, Response
from gracy.endpoints import append_query, format_url, join_url
from gracy.explore._infer import InferredModel, dedupe_models, infer, pascal, render_pydantic, safe_identifier
from gracy.explore._session import _ENV_RE, split_segments

__all__ = ["class_name_for", "render_class_source", "render_models_source", "render_module", "save_code"]

_METHOD_DECORATORS: t.Final = {
    "GET": "get",
    "POST": "post",
    "PUT": "put",
    "PATCH": "patch",
    "DELETE": "delete",
    "HEAD": "head",
    "OPTIONS": "options",
}


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


# --------------------------------------------------------------------------- rendering: pieces


def _exception_names(plans: t.Sequence[EndpointPlan]) -> list[str]:
    names: list[str] = []
    for plan in plans:
        for action in plan.on.values():
            if action.startswith("raise:") and action[len("raise:") :] not in names:
                names.append(action[len("raise:") :])
    return names


def _on_source(plan: EndpointPlan) -> str | None:
    if not plan.on:
        return None
    parts: list[str] = []
    for code in sorted(plan.on):
        action = plan.on[code]
        if action == "none":
            parts.append(f"{code}: None")
        elif action.startswith("raise:"):
            parts.append(f"{code}: raises({action[len('raise:'):]})")
        else:
            parts.append(f"{code}: {repr(ast.literal_eval(action))}")
    return "on={" + ", ".join(parts) + "}"


def _return_type(plan: EndpointPlan, name_map: dict[str, str]) -> str:
    if plan.response_shape == "model":
        assert plan.response_model is not None
        base = name_map.get(plan.response_model.name, plan.response_model.name)
    elif plan.response_shape == "list_model":
        assert plan.response_model is not None
        item = name_map.get(plan.response_model.name, plan.response_model.name)
        base = f"list[{item}]"
    elif plan.response_shape in ("dict", "list", "str"):
        base = plan.response_shape
    else:
        base = "t.Any"
    if any(action == "none" for action in plan.on.values()) and "None" not in base:
        base += " | None"
    return base


def _body_type(plan: EndpointPlan, name_map: dict[str, str]) -> str:
    if plan.body_kind == "raw":
        return "str"
    if plan.request_model is not None:
        model = name_map.get(plan.request_model.name, plan.request_model.name)
        return f"{model} | dict"
    return "t.Any"


def _endpoint_source(plan: EndpointPlan, name_map: dict[str, str]) -> list[str]:
    decorator_args = [dq(plan.template)]
    on_src = _on_source(plan)
    if on_src:
        decorator_args.append(on_src)
    if plan.allow_codes:
        decorator_args.append(f"status_policy=allow({', '.join(str(c) for c in plan.allow_codes)})")

    params = ["self"]
    for p in plan.path_params:
        params.append(f"{p}: t.Annotated[str, Path]")
    if plan.body_kind is not None:
        params.append(f"body: t.Annotated[{_body_type(plan, name_map)}, Body]")
    for q_name, q_default in plan.query_params:
        params.append(f"{q_name}: t.Annotated[str, Query] = {dq(q_default)}")

    decorator = f"    @{_METHOD_DECORATORS[plan.method]}({', '.join(decorator_args)})"
    signature = f"    async def {plan.name}({', '.join(params)}) -> {_return_type(plan, name_map)}: ..."
    return [decorator, signature]


def _config_source(data: dict[str, t.Any], *, needs_decoder: bool) -> tuple[list[str], set[str]]:
    """(config attribute lines, gracy names needed)."""
    policies = data.get("policies", {})
    entries: list[str] = []
    names: set[str] = set()
    if needs_decoder:
        entries.append("decoder=PydanticDecoder()")
        names.add("PydanticDecoder")
    if "retry" in policies:
        retry = policies["retry"]
        codes = ", ".join(str(c) for c in retry["codes"])
        wait = retry.get("wait", 1.0)
        if isinstance(wait, dict):
            wait_src = f"Backoff({wait['initial']:g}, multiplier={wait['multiplier']:g})"
            names.add("Backoff")
        else:
            wait_src = f"{float(wait):g}"
        entries.append(f"retry=Retry(on=status({codes}), attempts={retry['attempts']}, wait={wait_src})")
        names.update({"Retry", "status"})
    if "throttle" in policies:
        throttle = policies["throttle"]
        entries.append(f"throttle=Throttle(rules=[Rate({throttle['limit']}, per={dq(throttle['per'])})])")
        names.update({"Throttle", "Rate"})
    if "timeout" in policies:
        entries.append(f"timeout={float(policies['timeout']):g}")
    if not entries:
        return [], names
    names.add("GracyConfig")
    lines = ["    config = GracyConfig("]
    lines.extend(f"        {entry}," for entry in entries)
    lines.append("    )")
    return lines, names


def _transport_source(data: dict[str, t.Any], class_name: str) -> tuple[list[str], set[str], set[str]]:
    """(lines, gracy names, stdlib modules) for header/auth policies."""
    policies = data.get("policies", {})
    headers: dict[str, str] = dict(policies.get("headers") or {})
    stdlib: set[str] = set()
    entries: list[tuple[str, str]] = []
    for name, value in headers.items():
        expr = env_expr(value)
        if "os.environ" in expr:
            stdlib.add("os")
        entries.append((name, expr))
    auth = policies.get("auth")
    if auth is not None:
        if auth["scheme"] == "bearer":
            expr = env_expr("Bearer " + auth["token"])
            if "os.environ" in expr:
                stdlib.add("os")
        else:
            user, password = env_expr(auth["user"]), env_expr(auth["password"])
            if "os.environ" in user + password:
                stdlib.add("os")
            expr = f'"Basic " + base64.b64encode(({user} + ":" + {password}).encode()).decode()'
            stdlib.add("base64")
        entries.append(("Authorization", expr))
    if not entries:
        return [], set(), set()

    lines = ["TRANSPORT_CONFIG = TransportConfig(", "    base_headers={"]
    lines.extend(f"        {dq(name)}: {expr}," for name, expr in entries)
    lines.extend(["    },", ")"])
    factory = [
        "",
        "",
        f"def build_client(**kwargs: t.Any) -> {class_name}:",
        f'    """{class_name} wired with the recorded base headers."""',
        '    kwargs.setdefault("transport", RustTransport(TRANSPORT_CONFIG))',
        f"    return {class_name}(**kwargs)",
    ]
    return lines + factory, {"TransportConfig", "RustTransport"}, stdlib


# --------------------------------------------------------------------------- rendering: module


def render_models_source(data: dict[str, t.Any], only: str | None = None) -> str:
    plans = build_plans(data)
    roots: list[InferredModel] = []
    for plan in plans:
        for model in (plan.request_model, plan.response_model):
            if model is not None and (only is None or model.name == only):
                roots.append(model)
    unique, _ = dedupe_models(roots)
    source = render_pydantic(unique)
    if not source:
        return "# no models inferred yet\n" if only is None else f"# no model named {only!r}\n"
    return source


def render_class_source(data: dict[str, t.Any], class_name: str) -> str:
    plans = build_plans(data)
    _, name_map = _collect_models(plans)
    needs_decoder = any(p.response_shape in ("model", "list_model") for p in plans)
    lines = [f"class {class_name}(Gracy):"]
    base_url = data.get("base_url")
    if base_url:
        lines.append(f"    base_url = {dq(base_url)}")
    config_lines, _ = _config_source(data, needs_decoder=needs_decoder)
    if config_lines:
        lines.append("")
        lines.extend(config_lines)
    for plan in plans:
        lines.append("")
        lines.extend(_endpoint_source(plan, name_map))
    if len(lines) == 1 or (len(lines) == 2 and base_url):
        lines.append("    pass  # no endpoints named yet")
    return "\n".join(lines) + "\n"


def render_module(data: dict[str, t.Any], class_name: str) -> str:
    plans = build_plans(data)
    models, name_map = _collect_models(plans)
    models_src = render_pydantic(models)
    exceptions = _exception_names(plans)
    needs_decoder = any(p.response_shape in ("model", "list_model") for p in plans)

    gracy_names: set[str] = {"Gracy"}
    for plan in plans:
        gracy_names.add(_METHOD_DECORATORS[plan.method])
        if plan.path_params:
            gracy_names.add("Path")
        if plan.query_params:
            gracy_names.add("Query")
        if plan.body_kind is not None:
            gracy_names.add("Body")
        if plan.allow_codes:
            gracy_names.add("allow")
        if any(a.startswith("raise:") for a in plan.on.values()):
            gracy_names.add("raises")
    if exceptions:
        gracy_names.add("GracyUserDefinedException")

    config_lines, config_names = _config_source(data, needs_decoder=needs_decoder)
    gracy_names |= config_names
    transport_lines, transport_names, stdlib = _transport_source(data, class_name)
    gracy_names |= transport_names

    needs_typing = bool(transport_lines) or "t.Any" in models_src
    for plan in plans:
        if plan.path_params or plan.query_params or plan.body_kind is not None:
            needs_typing = True
        if _return_type(plan, name_map).startswith("t.Any"):
            needs_typing = True

    out: list[str] = [
        f'"""{class_name} — generated by `gracy explore` from a recorded explore session."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    for module in sorted(stdlib):
        out.append(f"import {module}")
    if needs_typing:
        out.append("import typing as t")
    if stdlib or needs_typing:
        out.append("")
    if models_src:
        pydantic_names = ["BaseModel"] + (["Field"] if "Field(" in models_src else [])
        out.append(f"from pydantic import {', '.join(pydantic_names)}")
        out.append("")
    out.append(f"from gracy import {', '.join(sorted(gracy_names))}")
    out.append("")

    if models_src:
        out.extend(["", models_src.rstrip("\n"), ""])
    for exc_name in exceptions:
        out.extend(
            [
                "",
                f"class {exc_name}(GracyUserDefinedException):",
                '    BASE_MESSAGE = "{METHOD} {URL} returned {STATUS}"',
                "",
            ]
        )

    out.append("")
    class_lines = [f"class {class_name}(Gracy):"]
    base_url = data.get("base_url")
    if base_url:
        class_lines.append(f"    base_url = {dq(base_url)}")
    if config_lines:
        class_lines.append("")
        class_lines.extend(config_lines)
    for plan in plans:
        class_lines.append("")
        class_lines.extend(_endpoint_source(plan, name_map))
    if len(class_lines) == 1 or (len(class_lines) == 2 and base_url):
        class_lines.append("    pass  # no endpoints named yet")
    out.extend(class_lines)

    if transport_lines:
        out.extend(["", ""])
        out.extend(transport_lines)

    import re as _re

    return _re.sub(r"\n{4,}", "\n\n\n", "\n".join(out)).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- tests + cassette


def _test_call_args(plan: EndpointPlan) -> str:
    args = [f"{name}={dq(value)}" for name, value in plan.test_path_args.items()]
    if plan.body_kind == "json" and plan.test_step is not None and "body_json" in plan.test_step:
        args.append(f"body={repr(plan.test_step['body_json'])}")
    elif plan.body_kind == "raw" and plan.test_step is not None and "body_b64" in plan.test_step:
        raw = base64.b64decode(plan.test_step["body_b64"]).decode("utf-8", "replace")
        args.append(f"body={dq(raw)}")
    return ", ".join(args)


def _test_assertion(plan: EndpointPlan, name_map: dict[str, str]) -> tuple[list[str], set[str]]:
    """(assertion lines after `result = ...`, module names to import)."""
    assert plan.test_step is not None
    status = plan.test_step["status"]
    action = plan.on.get(status)
    if action == "none":
        return ["            assert result is None"], set()
    if action is not None and not action.startswith("raise:"):
        return [f"            assert result == {repr(ast.literal_eval(action))}"], set()

    if plan.response_shape == "model":
        assert plan.response_model is not None
        model = name_map.get(plan.response_model.name, plan.response_model.name)
        return [f"            assert isinstance(result, {model})"], {model}
    if plan.response_shape == "list_model":
        assert plan.response_model is not None
        item = name_map.get(plan.response_model.name, plan.response_model.name)
        return [
            "            assert isinstance(result, list)",
            f"            assert all(isinstance(entry, {item}) for entry in result)",
        ], {item}
    if plan.response_shape in ("dict", "list", "str"):
        return [f"            assert isinstance(result, {plan.response_shape})"], set()
    return [f"            assert result.status == {status}"], set()


def _endpoint_url(data: dict[str, t.Any], plan: EndpointPlan) -> str:
    """EXACTLY the URL the generated endpoint produces at call time."""
    base = data.get("base_url") or ""
    url = format_url(join_url(base, plan.template), plan.test_path_args)
    query = {name: default for name, default in plan.query_params}  # signature order = wire order
    return append_query(url, query)


def _cassette_content(plan: EndpointPlan) -> bytes | None:
    """EXACTLY the body bytes the generated endpoint sends (json.dumps parity)."""
    assert plan.test_step is not None
    if plan.body_kind == "json" and "body_json" in plan.test_step:
        return json.dumps(plan.test_step["body_json"]).encode("utf-8")
    if plan.body_kind == "raw" and "body_b64" in plan.test_step:
        return base64.b64decode(plan.test_step["body_b64"])
    return None


def render_tests(data: dict[str, t.Any], stem: str, class_name: str) -> str:
    plans = [p for p in build_plans(data) if p.test_step is not None]
    _, name_map = _collect_models(plans)

    module_names: set[str] = {class_name}
    uses_pytest = False
    blocks: list[str] = []
    for plan in plans:
        step = plan.test_step
        assert step is not None
        call_args = _test_call_args(plan)
        action = plan.on.get(step["status"])
        lines = [f"def test_{plan.name}() -> None:", "    async def run() -> None:"]
        lines.append(f"        async with {class_name}(replay=_replay()) as api:")
        if action is not None and action.startswith("raise:"):
            exc_name = action[len("raise:") :]
            module_names.add(exc_name)
            uses_pytest = True
            lines.append(f"            with pytest.raises({exc_name}):")
            lines.append(f"                await api.{plan.name}({call_args})")
        else:
            lines.append(f"            result = await api.{plan.name}({call_args})")
            assertion, needed = _test_assertion(plan, name_map)
            module_names |= needed
            lines.extend(assertion)
            if 200 <= step["status"] < 300 and step["status"] not in plan.on:
                request_args = [dq(plan.method), dq(plan.template)]
                if plan.test_path_args:
                    request_args.append(repr(plan.test_path_args))
                kwargs: list[str] = []
                if plan.query_params:
                    kwargs.append(f"params={repr({k: v for k, v in plan.query_params})}")
                if plan.body_kind == "json" and "body_json" in step:
                    kwargs.append(f"json={repr(step['body_json'])}")
                elif plan.body_kind == "raw" and "body_b64" in step:
                    raw = base64.b64decode(step["body_b64"]).decode("utf-8", "replace")
                    kwargs.append(f"content={dq(raw)}")
                call = ", ".join(request_args + kwargs)
                lines.append(f"            raw = await api.request({call})")
                lines.append(f"            assert raw.status == {step['status']}")
        lines.extend(["", "    asyncio.run(run())"])
        blocks.append("\n".join(lines))

    out = [
        f'"""Replay tests for {stem}.py — generated by `gracy explore`; runs offline against the cassette."""',
        "",
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import pathlib",
        "",
    ]
    if uses_pytest:
        out.extend(["import pytest", ""])
    out.append("from gracy import Replay, SqliteStorage")
    out.append("")
    out.append(f"from {stem} import {', '.join(sorted(module_names))}")
    out.extend(
        [
            "",
            f'CASSETTE = pathlib.Path(__file__).with_name("{stem}.cassette.db")',
            "",
            "",
            "def _replay() -> Replay:",
            "    return Replay(mode=\"replay\", storage=SqliteStorage(CASSETTE), display_report=False)",
            "",
        ]
    )
    for block in blocks:
        out.extend(["", block, ""])
    import re as _re

    return _re.sub(r"\n{4,}", "\n\n\n", "\n".join(out)).rstrip("\n") + "\n"


def build_cassette(data: dict[str, t.Any], cassette_path: Path) -> None:
    from gracy.replay import DEFAULT_MATCH_ON, SqliteStorage, match_hash

    if cassette_path.exists():
        cassette_path.unlink()
    storage = SqliteStorage(cassette_path)
    storage._prepare_sync()
    try:
        for plan in build_plans(data):
            step = plan.test_step
            if step is None:
                continue
            url = _endpoint_url(data, plan)
            spec = RequestSpec(
                method=plan.method,
                url=url,
                uurl=url,
                headers=(),
                content=_cassette_content(plan),
            )
            response = Response(
                status=int(step["status"]),
                headers=tuple((str(k), str(v)) for k, v in (step.get("response_headers") or [])),
                body=_response_body_bytes(step),
                url=url,
                elapsed=float(step.get("elapsed_ms", 0.0)) / 1000.0,
            )
            key = match_hash(spec, DEFAULT_MATCH_ON, None)
            storage._record_sync(key, spec, response, None)
        storage._flush_sync()
    finally:
        if storage._conn is not None:
            storage._conn.close()


def save_code(data: dict[str, t.Any], out: Path, *, tests: bool = False) -> list[Path]:
    out = out.with_suffix(".py") if out.suffix != ".py" else out
    out.parent.mkdir(parents=True, exist_ok=True)
    stem = out.stem
    class_name = class_name_for(stem)

    files = [out]
    out.write_text(render_module(data, class_name), "utf-8")
    if tests:
        test_path = out.with_name(f"test_{stem}.py")
        cassette_path = out.with_name(f"{stem}.cassette.db")
        test_path.write_text(render_tests(data, stem, class_name), "utf-8")
        build_cassette(data, cassette_path)
        files.extend([test_path, cassette_path])
    return files
