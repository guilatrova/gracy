"""ExploreSession - the `gracy explore` engine (no REPL in here).

Every request runs through a real, lazily-built internal :class:`gracy.Gracy`
client, so retry/throttle/timeout policies behave exactly like production.
Every mutation is snapshotted (undo) and persisted to the session json file.

Secrets: "$VAR"/"${VAR}" placeholders in headers/query/body strings are
resolved from the environment at EXECUTION time only; the session file always
stores the UNRESOLVED placeholder, and gracy's default Scrub header set
(authorization/cookie/x-api-key) is redacted before persisting.
"""

from __future__ import annotations

import base64
import copy
import json
import re
import time
import typing as t
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gracy._types import Response
from gracy.client import Gracy
from gracy.config import GracyConfig
from gracy.explore._env import (  # noqa: F401  # re-exported for compat
    _ENV_RE,
    _redact_values,
    _referenced_env_values,
    _resolve_env_any,
    env_vars_in,
    resolve_env,
)
from gracy.explore._pathutils import (  # noqa: F401  # re-exported for compat
    _tokenize_path,
    default_param_name,
    flatten_keys,
    join_segments,
    resolve_json_path,
    split_segments,
    template_matches,
)
from gracy.explore._policies import (  # noqa: F401  # re-exported for compat
    auth_header_value,
    on_action_value,
    parse_auth,
    parse_retry,
    parse_throttle,
    retry_to_config,
    throttle_to_config,
    validate_on_action,
)
from gracy.replay import Scrub

__all__ = ["ExploreSession", "StepResult"]

SCHEMA_VERSION: t.Final = 1
MAX_STORED_BODY: t.Final = 256 * 1024  # response bodies are capped in the session file
_PREVIEW_CHARS: t.Final = 400

_CAPTURE_RE: t.Final = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_SCRUBBED_HEADERS: t.Final = frozenset(h.lower() for h in Scrub().headers)


# --------------------------------------------------------------------------- step result


@dataclass
class StepResult:
    step_id: int
    method: str
    url: str
    path: str
    status: int | None
    elapsed_ms: float
    body_preview: t.Any  # parsed json, or the head of the text body
    ok: bool
    error: str | None
    matched_endpoint: str | None
    template_proposal: str | None
    model_drift: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- the session


class _RetryableStatus(Exception):
    """Internal: raised by the explorer's validator so Retry(on=status(...)) triggers."""


class _RetryStatusValidator:
    """The explorer disables the status policy (any status is a valid answer), so
    retryable statuses need a validator to produce the exception Retry keys on."""

    def __init__(self, codes: t.Iterable[int]) -> None:
        self._codes = {int(c) for c in codes}

    def check(self, response: Response) -> None:
        if response.status in self._codes:
            raise _RetryableStatus(f"status {response.status} is configured as retryable")


class _ExplorerClient(Gracy):
    """Internal ad-hoc client; base_url/config are set per instance before build()."""


class ExploreSession:
    def __init__(self, session_path: str | Path | None = None, base_url: str | None = None) -> None:
        self.session_path: Path = Path(session_path) if session_path is not None else Path("gracy_explore.json")
        self._data: dict[str, t.Any] = {
            "schema": SCHEMA_VERSION,
            "base_url": None,
            "policies": {},
            "steps": [],
            "endpoints": {},
            "models": {},
            "captures": {},
        }
        if self.session_path.exists():
            loaded = json.loads(self.session_path.read_text("utf-8"))
            if loaded.get("schema") != SCHEMA_VERSION:
                raise ValueError(
                    f"Session file {self.session_path} has schema {loaded.get('schema')!r}; "
                    f"this gracy understands schema {SCHEMA_VERSION}"
                )
            self._data.update(loaded)
            # normalise step ids to a contiguous 1..N (older sessions may carry
            # gaps left by drops before ids were kept contiguous)
            for i, step in enumerate(self._data.get("steps", []), start=1):
                step["id"] = i
        if base_url is not None:
            self._data["base_url"] = base_url

        self._client: Gracy | None = None
        self._client_dirty = True
        self._client_loop: t.Any = None
        self._undo_stack: list[tuple[str, dict[str, t.Any]]] = []

    # ------------------------------------------------------------------ properties

    @property
    def base_url(self) -> str | None:
        return self._data.get("base_url")

    @base_url.setter
    def base_url(self, value: str | None) -> None:
        self._data["base_url"] = value
        self._client_dirty = True

    @property
    def steps(self) -> list[dict[str, t.Any]]:
        return self._data["steps"]

    @property
    def captures(self) -> dict[str, t.Any]:
        return self._data.setdefault("captures", {})

    # ------------------------------------------------------------------ undo bookkeeping

    def _snapshot(self, label: str) -> None:
        self._undo_stack.append((label, copy.deepcopy(self._data)))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

    def _rollback(self) -> None:
        """Revert the most recent _snapshot in memory (no persist). Lets an
        operation stay atomic: if a mutation half-applies and then fails, we
        undo it so a failed command never leaves the session poisoned."""
        if self._undo_stack:
            _, snapshot = self._undo_stack.pop()
            self._data = snapshot
            self._client_dirty = True

    def undo(self) -> str:
        """Drop the last step / last mutation. Returns a human line."""
        if not self._undo_stack:
            return "nothing to undo"
        label, snapshot = self._undo_stack.pop()
        self._data = snapshot
        self._client_dirty = True
        self.persist()
        return f"undid {label}"

    # ------------------------------------------------------------------ internal client

    def _build_config(self) -> GracyConfig:
        import dataclasses

        policies = self._data["policies"]
        kwargs: dict[str, t.Any] = {"status_policy": None}  # explorer accepts EVERY status
        if "retry" in policies:
            # suppress=True: exhausted retries still hand back the last response
            kwargs["retry"] = dataclasses.replace(retry_to_config(policies["retry"]), suppress=True)
            kwargs["validators"] = [_RetryStatusValidator(policies["retry"]["codes"])]
        if "throttle" in policies:
            kwargs["throttle"] = throttle_to_config(policies["throttle"])
        if "timeout" in policies:
            kwargs["timeout"] = float(policies["timeout"])
        return GracyConfig(**kwargs)

    async def _get_client(self) -> Gracy:
        import asyncio

        loop = asyncio.get_running_loop()
        if self._client is not None and (self._client_dirty or loop is not self._client_loop):
            if loop is self._client_loop:
                await self._client.aclose()
            self._client = None  # stale loop: drop the reference, its loop is gone
        if self._client is None:
            client = _ExplorerClient()
            client.base_url = self._data.get("base_url") or ""  # type: ignore[misc]
            client.config = self._build_config()  # type: ignore[misc]
            await client.build()
            self._client = client
            self._client_loop = loop
            self._client_dirty = False
        return self._client

    async def aclose(self) -> None:
        """Close the internal client (safe to call multiple times)."""
        import asyncio

        if self._client is not None:
            try:
                if asyncio.get_running_loop() is self._client_loop:
                    await self._client.aclose()
            except RuntimeError:
                pass
            self._client = None

    # ------------------------------------------------------------------ execute

    # ------------------------------------------------------------------ captures

    def peek(self, path: str) -> t.Any:
        """Resolve a dot/bracket path against the LAST response, without storing.
        Used to preview a value before `set` and by the `peek` command."""
        steps = self._data["steps"]
        if not steps:
            raise ValueError("no request yet - run one first")
        last = steps[-1]
        if "response_json" not in last:
            raise ValueError("the last response is not JSON - nothing to read")
        return resolve_json_path(last["response_json"], path)

    def capture(self, name: str, path: str) -> t.Any:
        """Snapshot a value from the LAST response into the session (concrete data).

        The value is resolved against the last step's parsed JSON at set-time and
        stored under ``captures[name]``; later ``{{name}}`` refs expand to it."""
        if not name.isidentifier():
            raise ValueError(f"{name!r} is not a valid capture name")
        value = self.peek(path)
        self._snapshot(f"set {name} = {path}")
        self.captures[name] = value
        self.persist()
        return value

    def _resolve_captures_str(self, value: str) -> str:
        """Expand ``{{name}}`` refs to str(captured value); unknown -> ValueError."""

        def _sub(m: re.Match[str]) -> str:
            name = m.group(1)
            captures = self._data.get("captures", {})
            if name not in captures:
                raise ValueError(f"no capture named {name!r} - set it with `set {name} <path>`")
            return str(captures[name])

        return _CAPTURE_RE.sub(_sub, value)

    def _resolve_captures_any(self, value: t.Any) -> t.Any:
        """Recursively expand ``{{name}}`` in every string of a JSON-ish tree."""
        if isinstance(value, str):
            return self._resolve_captures_str(value)
        if isinstance(value, dict):
            return {k: self._resolve_captures_any(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_captures_any(v) for v in value]
        return value

    def _policy_headers(self, *, resolve: bool) -> dict[str, str]:
        policies = self._data["policies"]
        headers: dict[str, str] = {}
        for name, value in (policies.get("headers") or {}).items():
            headers[name] = resolve_env(value) if resolve else value
        if "auth" in policies:
            headers["Authorization"] = auth_header_value(policies["auth"], resolve=resolve)
        return headers

    async def execute(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | str | None = None,
        body_json: t.Any | None = None,
    ) -> StepResult:
        method = method.upper()
        # -- {{name}} capture refs expand FIRST, to concrete values. The result is
        # what gets STORED (captures are exploration data, not secrets); $VAR stays
        # unresolved in storage and is only resolved for the wire below.
        path = self._resolve_captures_str(path)
        if query is not None:
            query = {k: (self._resolve_captures_str(v) if isinstance(v, str) else v) for k, v in query.items()}
        if headers is not None:
            headers = {k: self._resolve_captures_str(v) for k, v in headers.items()}
        if isinstance(body, str):
            body = self._resolve_captures_str(body)
        if body_json is not None:
            body_json = self._resolve_captures_any(body_json)

        if "?" in path:  # tolerate query strings pasted into the path
            from urllib.parse import parse_qsl, urlsplit

            split = urlsplit(path)
            path = split.path
            merged = dict(parse_qsl(split.query))
            merged.update(query or {})
            query = merged

        if not path.lower().startswith(("http://", "https://")) and not self._data.get("base_url"):
            raise ValueError("No base_url set - use set_policy(base_url=...) or pass an absolute URL")

        # -- resolved (wire) values vs unresolved (persisted) values.
        # The PATH is env-resolved for the wire too (so `/x/${VAR}` works and
        # never reaches format_url with a stray `{VAR}`), but stored unresolved.
        resolved_path = resolve_env(path)
        resolved_query = {k: resolve_env(str(v)) for k, v in (query or {}).items()}
        request_headers = self._policy_headers(resolve=True)
        for k, v in (headers or {}).items():
            request_headers[k] = resolve_env(v)
        resolved_body: bytes | str | None = body
        if isinstance(body, str):
            resolved_body = resolve_env(body)
        resolved_json = _resolve_env_any(body_json) if body_json is not None else None

        client = await self._get_client()
        status: int | None = None
        error: str | None = None
        response: Response | None = None
        started = time.monotonic()
        try:
            result = await client.request(
                method,
                resolved_path,
                params=resolved_query or None,
                headers=request_headers or None,
                content=resolved_body,
                json=resolved_json,
            )
            if isinstance(result, Response):
                response = result
                status = response.status
            else:  # suppressed transport failure after retries: no response at all
                error = "request failed with no response (transport error after retries)"
        except Exception as exc:  # noqa: BLE001 - explorer reports, never crashes
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = (
            response.elapsed * 1000.0 if response is not None else (time.monotonic() - started) * 1000.0
        )

        body_preview: t.Any = None
        if response is not None:
            try:
                body_preview = response.json()
            except ValueError:
                body_preview = response.text[:_PREVIEW_CHARS]

        matched = self._match_endpoint(method, path)
        drift = self._model_drift(matched, body_preview) if matched else []
        proposal = None if matched else self._template_proposal(method, path)

        self._snapshot(f"step {self._next_step_id()} {method} {path}")
        step: dict[str, t.Any] = {
            "id": self._next_step_id(),
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "query": dict(query or {}),  # UNRESOLVED
            "headers": self._scrub_headers(headers or {}),  # UNRESOLVED + scrubbed
            "status": status,
            "elapsed_ms": elapsed_ms,
            "endpoint": matched,
        }
        if body_json is not None:
            step["body_json"] = body_json  # UNRESOLVED
        elif body is not None:
            raw = body.encode("utf-8") if isinstance(body, str) else body  # UNRESOLVED
            step["body_b64"] = base64.b64encode(raw).decode("ascii")
        if error is not None:
            step["error"] = error
        if response is not None:
            step["response_headers"] = [list(kv) for kv in response.headers]
            # A server may echo a resolved secret back - redact any env value the
            # request referenced before the response touches disk.
            secrets = _referenced_env_values(headers, query, body, body_json, self._policy_headers(resolve=False))
            # Store the parsed JSON in full (never truncated) - model inference
            # and the replay cassette both rely on it being valid. The raw b64
            # blob is only a capped fallback for non-JSON bodies.
            try:
                step["response_json"] = _redact_values(json.loads(response.body), secrets)
            except ValueError:
                redacted = response.body[:MAX_STORED_BODY]
                for secret in secrets:
                    redacted = redacted.replace(secret.encode("utf-8"), b"***")
                step["response_body_b64"] = base64.b64encode(redacted).decode("ascii")
        self._data["steps"].append(step)
        self.persist()

        url = self._full_url(path, resolved_query)
        return StepResult(
            step_id=step["id"],
            method=method,
            url=url,
            path=path,
            status=status,
            elapsed_ms=elapsed_ms,
            body_preview=body_preview,
            ok=error is None and status is not None and status < 400,
            error=error,
            matched_endpoint=matched,
            template_proposal=proposal,
            model_drift=drift,
        )

    def _next_step_id(self) -> int:
        steps = self._data["steps"]
        return (max((s["id"] for s in steps), default=0)) + 1

    def _full_url(self, path: str, query: dict[str, str]) -> str:
        from gracy.endpoints import append_query, join_url

        url = join_url(self._data.get("base_url") or "", path)
        return append_query(url, query)

    @staticmethod
    def _scrub_headers(headers: dict[str, str]) -> dict[str, str]:
        return {k: ("***" if k.lower() in _SCRUBBED_HEADERS else v) for k, v in headers.items()}

    # ------------------------------------------------------------------ endpoint matching / drift

    def _match_endpoint(self, method: str, path: str) -> str | None:
        for name, ep in self._data["endpoints"].items():
            if ep["method"] == method and template_matches(ep["template"], path):
                return name
        return None

    def _endpoint_steps(self, name: str) -> list[dict[str, t.Any]]:
        return [s for s in self._data["steps"] if s.get("endpoint") == name]

    def _response_json(self, step: dict[str, t.Any]) -> t.Any:
        if "response_json" in step:
            return step["response_json"]
        raw = step.get("response_body_b64")
        if not raw:
            return None
        try:
            return json.loads(base64.b64decode(raw))
        except ValueError:
            return None

    def _model_drift(self, endpoint: str, new_body: t.Any) -> list[str]:
        if not isinstance(new_body, dict):
            return []
        seen: set[str] = set()
        prior = False
        for step in self._endpoint_steps(endpoint):
            parsed = self._response_json(step)
            if isinstance(parsed, dict):
                prior = True
                seen |= flatten_keys(parsed)
        if not prior:
            return []
        ep = self._data["endpoints"][endpoint]
        model_name = ep.get("response_model") or self._default_model_name(endpoint)
        new_keys = sorted(flatten_keys(new_body) - seen)
        return [f"{model_name}: field '{key}' seen for the first time" for key in new_keys]

    def _template_proposal(self, method: str, path: str) -> str | None:
        p_segs = split_segments(path)
        for name, ep in self._data["endpoints"].items():
            if ep["method"] != method:
                continue
            t_segs = split_segments(ep["template"])
            if len(t_segs) != len(p_segs):
                continue
            for step in self._endpoint_steps(name):
                s_segs = split_segments(step["path"])
                if len(s_segs) != len(p_segs):
                    continue
                diffs = [i for i, (a, b) in enumerate(zip(s_segs, p_segs)) if a != b]
                if len(diffs) != 1:
                    continue
                index = diffs[0]
                param = next((p["name"] for p in ep.get("params", []) if p["index"] == index), None)
                if param is None:
                    taken = {p["name"] for p in ep.get("params", [])}
                    param = default_param_name(p_segs, index, taken)
                proposed = list(t_segs)
                proposed[index] = "{" + param + "}"
                return join_segments(proposed)
        return None

    # ------------------------------------------------------------------ naming endpoints

    def name_endpoint(self, name: str, step_id: int = -1) -> str:
        """Name a step's request as an endpoint; auto-templates against the
        other steps already assigned to this endpoint. Returns the template."""
        step = self._find_step(step_id)
        endpoints = self._data["endpoints"]
        existing = endpoints.get(name)
        if existing is not None and existing["method"] != step["method"]:
            raise ValueError(
                f"Endpoint {name!r} is {existing['method']}, step {step['id']} is {step['method']}"
            )
        self._snapshot(f"name_endpoint {name}")
        try:
            step["endpoint"] = name
            if existing is None:
                endpoints[name] = {
                    "method": step["method"],
                    "template": step["path"],
                    "params": [],
                    "on": {},
                    "response_model": None,
                    "request_model": None,
                }
            template = self._recompute_template(name)
        except Exception:
            # a failed fold (e.g. a path-depth clash) must not poison the
            # endpoint: undo the half-applied assignment before re-raising
            self._rollback()
            raise
        self.persist()
        return template

    def _find_step(self, step_id: int) -> dict[str, t.Any]:
        steps = self._data["steps"]
        if not steps:
            raise ValueError("No steps recorded yet")
        if step_id == -1:
            return steps[-1]
        for step in steps:
            if step["id"] == step_id:
                return step
        raise ValueError(f"No step with id {step_id}")

    def _recompute_template(self, name: str) -> str:
        ep = self._data["endpoints"][name]
        paths = [split_segments(s["path"]) for s in self._endpoint_steps(name)]
        if not paths:
            return ep["template"]
        length = len(paths[0])
        if any(len(p) != length for p in paths):
            example: dict[int, str] = {}
            for s in self._endpoint_steps(name):
                example.setdefault(len(split_segments(s["path"])), s["path"])
            detail = ", ".join(
                f"{d} segment{'' if d == 1 else 's'} ({example[d]})" for d in sorted(example)
            )
            raise ValueError(
                f"Endpoint {name!r}: its requests don't share a path depth ({detail}), "
                f"so they can't collapse into one URL template. Keep the odd one on its "
                f"own endpoint (or `undo` this fold)."
            )
        by_index = {p["index"]: p["name"] for p in ep.get("params", [])}
        params: list[dict[str, t.Any]] = []
        segments: list[str] = []
        for i in range(length):
            values = {p[i] for p in paths}
            if len(values) == 1 and i not in by_index:
                segments.append(paths[0][i])
            else:
                taken = {p["name"] for p in params}
                pname = by_index.get(i) or default_param_name(paths[0], i, taken)
                params.append({"name": pname, "index": i})
                segments.append("{" + pname + "}")
        ep["params"] = params
        ep["template"] = join_segments(segments)
        return ep["template"]

    def set_param_name(self, endpoint: str, index: int, name: str) -> str:
        ep = self._require_endpoint(endpoint)
        if not name.isidentifier():
            raise ValueError(f"{name!r} is not a valid parameter name")
        param = next((p for p in ep.get("params", []) if p["index"] == index), None)
        if param is None:
            raise ValueError(
                f"Endpoint {endpoint!r} has no parameter at segment index {index} "
                f"(template: {ep['template']})"
            )
        self._snapshot(f"param {index} as {name}")
        param["name"] = name
        segments = split_segments(ep["template"])
        segments[index] = "{" + name + "}"
        ep["template"] = join_segments(segments)
        self.persist()
        return ep["template"]

    def _require_endpoint(self, name: str) -> dict[str, t.Any]:
        ep = self._data["endpoints"].get(name)
        if ep is None:
            raise ValueError(f"No endpoint named {name!r}; known: {sorted(self._data['endpoints'])}")
        return ep

    def _last_endpoint(self) -> str:
        """The endpoint implicit commands (on/model/param) target: the endpoint
        of the MOST RECENT request. We do NOT skip back to an older named
        endpoint - that silently edits something you're not looking at."""
        steps = self._data["steps"]
        if not steps:
            raise ValueError("no request yet - run one first")
        last = steps[-1]
        if last.get("endpoint"):
            return t.cast(str, last["endpoint"])
        raise ValueError(
            f"the last request {last['method']} {last['path']} isn't a named endpoint yet - "
            f"name it with `endpoint <name>` first"
        )

    # ------------------------------------------------------------------ endpoint metadata

    def set_on(self, endpoint_or_last: str | None, status: int, action: str) -> None:
        """action grammar: "none" | "raise:<ExcName>" | a literal like "{}"."""
        validate_on_action(action)
        name = endpoint_or_last or self._last_endpoint()
        ep = self._require_endpoint(name)
        self._snapshot(f"on {status} {action} ({name})")
        ep.setdefault("on", {})[str(int(status))] = action
        self.persist()

    def set_model_name(self, name: str, endpoint: str | None = None) -> None:
        """Names the RESPONSE model; "name!request" renames the request model."""
        target = "response_model"
        if name.endswith("!request"):
            name = name[: -len("!request")]
            target = "request_model"
        if not name.isidentifier():
            raise ValueError(f"{name!r} is not a valid model name")
        ep_name = endpoint or self._last_endpoint()
        ep = self._require_endpoint(ep_name)
        self._snapshot(f"model {name} ({ep_name})")
        ep[target] = name
        self._data["models"][f"{ep_name}:{'request' if target == 'request_model' else 'response'}"] = name
        self.persist()

    def rename_endpoint(self, old: str, new: str) -> None:
        endpoints = self._data["endpoints"]
        if old not in endpoints:
            raise ValueError(f"no endpoint named {old!r}")
        if new in endpoints:
            raise ValueError(f"endpoint {new!r} already exists")
        if not new.isidentifier():
            raise ValueError(f"{new!r} is not a valid endpoint name")
        self._snapshot(f"rename endpoint {old} -> {new}")
        endpoints[new] = endpoints.pop(old)
        for step in self._data["steps"]:
            if step.get("endpoint") == old:
                step["endpoint"] = new
        for key in (f"{old}:response", f"{old}:request"):
            if key in self._data["models"]:
                self._data["models"][key.replace(old + ":", new + ":", 1)] = self._data["models"].pop(key)
        self.persist()

    def rename_model(self, old: str, new: str) -> None:
        if not new.isidentifier():
            raise ValueError(f"{new!r} is not a valid model name")
        targets = [
            (ep, f)
            for ep in self._data["endpoints"].values()
            for f in ("response_model", "request_model")
            if ep.get(f) == old
        ]
        if not targets:
            raise ValueError(f"no model named {old!r}")
        self._snapshot(f"rename model {old} -> {new}")
        for ep, field_name in targets:
            ep[field_name] = new
        for key, value in list(self._data["models"].items()):
            if value == old:
                self._data["models"][key] = new
        self.persist()

    def drop_endpoint(self, name: str) -> dict[str, t.Any]:
        """Remove a named endpoint. Each of its steps is re-homed into another
        endpoint whose template already covers it (so dropping a redundant
        endpoint like /pokemon/pikachu folds its step into /pokemon/{pokemon}),
        or left unnamed if nothing covers it. Returns {'freed': n, 'absorbed':
        {endpoint: count}}."""
        endpoints = self._data["endpoints"]
        if name not in endpoints:
            raise ValueError(f"no endpoint named {name!r}; known: {sorted(endpoints)}")
        self._snapshot(f"drop endpoint {name}")
        del endpoints[name]  # remove first so its steps never re-match itself
        for key in (f"{name}:response", f"{name}:request"):
            self._data["models"].pop(key, None)
        freed = 0
        absorbed: dict[str, int] = {}
        for step in self._data["steps"]:
            if step.get("endpoint") == name:
                freed += 1
                new = self._match_endpoint(step["method"], step["path"])
                step["endpoint"] = new
                if new:
                    absorbed[new] = absorbed.get(new, 0) + 1
        for ep_name in absorbed:
            try:
                self._recompute_template(ep_name)
            except ValueError:
                pass  # newly-absorbed steps clash; keep the existing template
        self.persist()
        return {"freed": freed, "absorbed": absorbed}

    def drop_step(self, step_id: int) -> dict[str, t.Any]:
        """Remove one recorded request. If it belonged to an endpoint, the
        endpoint is re-templated over its remaining steps (dropping the odd
        one out can even repair a path-depth clash). Returns a small summary."""
        steps = self._data["steps"]
        target = next((s for s in steps if s["id"] == step_id), None)
        if target is None:
            known = ", ".join(str(s["id"]) for s in steps) or "(none)"
            raise ValueError(f"no step with id {step_id}; recorded: {known}")
        self._snapshot(f"drop step {step_id}")
        endpoint = target.get("endpoint")
        steps.remove(target)
        # keep ids a contiguous 1..N list (what `show history` shows and what
        # `drop step <n>` expects); a gap after a drop just confuses the numbering
        for i, s in enumerate(steps, start=1):
            s["id"] = i
        if endpoint and endpoint in self._data["endpoints"] and self._endpoint_steps(endpoint):
            try:
                self._recompute_template(endpoint)
            except ValueError:
                pass  # the leftover steps still clash; leave the template as-is
        self.persist()
        return {"step_id": step_id, "path": target["path"], "endpoint": endpoint}

    def prune_steps(self) -> dict[str, int]:
        """Drop every unnamed step (one not folded into any endpoint) at once
        and renumber the survivors 1..N. Exploration leaves these behind when
        you re-run a request just to look at it; this clears the clutter that
        `list` hides. Snapshotted, so `undo` brings them all back. Returns
        {'removed': n, 'kept': m}."""
        steps = self._data["steps"]
        kept = [s for s in steps if s.get("endpoint")]
        removed = len(steps) - len(kept)
        if not removed:
            return {"removed": 0, "kept": len(steps)}
        self._snapshot("prune unnamed steps")
        for i, s in enumerate(kept, start=1):
            s["id"] = i
        self._data["steps"] = kept
        self.persist()
        return {"removed": removed, "kept": len(kept)}

    def _default_model_name(self, endpoint: str) -> str:
        from gracy.explore._infer import pascal

        return pascal(endpoint) + "Response"

    # ------------------------------------------------------------------ policies

    def set_policy(
        self,
        *,
        retry: str | None = None,
        throttle: str | None = None,
        timeout: float | None = None,
        auth: str | None = None,
        header: tuple[str, str] | None = None,
        base_url: str | None = None,
    ) -> str:
        """Parse + store policies. Returns a human confirmation line."""
        changes: list[str] = []
        staged: list[t.Callable[[], None]] = []
        policies = self._data["policies"]

        if retry is not None:
            parsed_retry = parse_retry(retry)
            retry_conf = retry_to_config(parsed_retry)  # validate eagerly
            staged.append(lambda: policies.__setitem__("retry", parsed_retry))
            wait = parsed_retry.get("wait", 1.0)
            wait_text = (
                f"backoff {wait['initial']:g}s x{wait['multiplier']:g}" if isinstance(wait, dict) else f"wait {wait:g}s"
            )
            changes.append(
                f"retry: {retry_conf.attempts} retries on "
                + ",".join(str(c) for c in parsed_retry["codes"])
                + f", {wait_text}"
            )
        if throttle is not None:
            parsed_throttle = parse_throttle(throttle)
            staged.append(lambda: policies.__setitem__("throttle", parsed_throttle))
            changes.append(f"throttle: {parsed_throttle['limit']} req / {parsed_throttle['per']}")
        if timeout is not None:
            timeout_value = float(timeout)
            staged.append(lambda: policies.__setitem__("timeout", timeout_value))
            changes.append(f"timeout: {timeout_value:g}s")
        if auth is not None:
            parsed_auth = parse_auth(auth)
            staged.append(lambda: policies.__setitem__("auth", parsed_auth))
            changes.append(f"auth: {parsed_auth['scheme']} (resolved at request time)")
        if header is not None:
            header_name, header_value = header
            staged.append(lambda: policies.setdefault("headers", {}).__setitem__(header_name, header_value))
            changes.append(f"header: {header_name}: {header_value}")
        if base_url is not None:
            staged.append(lambda: self._data.__setitem__("base_url", base_url.rstrip("/")))
            changes.append(f"base_url: {base_url.rstrip('/')}")

        if not staged:
            return "no policy changes"
        self._snapshot("set_policy " + "; ".join(changes))
        for apply in staged:
            apply()
        self._client_dirty = True
        self.persist()
        return "; ".join(changes)

    # ------------------------------------------------------------------ views

    def history(self) -> list[dict[str, t.Any]]:
        out: list[dict[str, t.Any]] = []
        for step in self._data["steps"]:
            parsed = self._response_json(step)
            if parsed is None and step.get("response_body_b64"):
                parsed = base64.b64decode(step["response_body_b64"]).decode("utf-8", "replace")[:_PREVIEW_CHARS]
            status = step.get("status")
            out.append(
                {
                    "step_id": step["id"],
                    "method": step["method"],
                    "url": self._full_url(step["path"], step.get("query") or {}),
                    "path": step["path"],
                    "status": status,
                    "elapsed_ms": step.get("elapsed_ms", 0.0),
                    "body_preview": parsed,
                    "ok": step.get("error") is None and status is not None and status < 400,
                    "error": step.get("error"),
                    "matched_endpoint": step.get("endpoint"),
                    "template_proposal": None,
                    "model_drift": [],
                }
            )
        return out

    def endpoints(self) -> dict[str, t.Any]:
        summary: dict[str, t.Any] = {}
        for name, ep in self._data["endpoints"].items():
            summary[name] = {
                "method": ep["method"],
                "template": ep["template"],
                "params": copy.deepcopy(ep.get("params", [])),
                "on": dict(ep.get("on", {})),
                "response_model": ep.get("response_model"),
                "request_model": ep.get("request_model"),
                "steps": len(self._endpoint_steps(name)),
            }
        return summary

    # ------------------------------------------------------------------ drift check

    def representative_step(self, name: str) -> dict[str, t.Any] | None:
        """The step used to re-probe an endpoint: the most recent 2xx with a
        response body, falling back to the most recent step with any response."""
        steps = self._endpoint_steps(name)
        for step in reversed(steps):
            status = step.get("status")
            if status is not None and 200 <= status < 300 and self._response_json(step) is not None:
                return step
        for step in reversed(steps):
            if self._response_json(step) is not None:
                return step
        return None

    async def reissue_live(self, step: dict[str, t.Any]) -> tuple[int | None, t.Any, str | None]:
        """Re-send a recorded step's request against the LIVE API without
        recording it. Returns (status, parsed_json_or_None, error). Policy auth
        headers are re-applied; per-request headers scrubbed at record time are
        not resent (they were redacted), which `--check` documents."""
        method = str(step["method"])
        path = str(step["path"])
        query = {k: resolve_env(str(v)) for k, v in (step.get("query") or {}).items()}
        headers = self._policy_headers(resolve=True)
        body_json = _resolve_env_any(step["body_json"]) if "body_json" in step else None
        body: str | None = None
        if body_json is None and step.get("body_b64"):
            body = resolve_env(base64.b64decode(step["body_b64"]).decode("utf-8", "replace"))

        client = await self._get_client()
        try:
            result = await client.request(
                method, path, params=query or None, headers=headers or None, content=body, json=body_json
            )
        except Exception as exc:  # noqa: BLE001 - check reports, never crashes
            return None, None, f"{type(exc).__name__}: {exc}"
        if not isinstance(result, Response):
            return None, None, "request failed with no response (transport error after retries)"
        try:
            return result.status, result.json(), None
        except ValueError:
            return result.status, None, None

    async def check_all(self) -> list[t.Any]:
        """Re-probe every named endpoint and diff its live shape vs the recording."""
        from gracy.explore._check import EndpointDrift, diff_shape

        results: list[EndpointDrift] = []
        for name, ep in self._data["endpoints"].items():
            drift = EndpointDrift(endpoint=name, method=ep["method"], template=ep["template"], ok=True)
            step = self.representative_step(name)
            if step is None:
                drift.ok, drift.note = False, "no recorded sample to compare"
                results.append(drift)
                continue
            drift.status_recorded = step.get("status")
            status, live_json, error = await self.reissue_live(step)
            drift.status_live = status
            if error is not None:
                drift.ok, drift.error = False, error
            else:
                drift.shape = diff_shape(self._response_json(step), live_json)
                status_changed = status != drift.status_recorded
                drift.ok = not drift.shape.has_drift and not status_changed
            results.append(drift)
        return results

    def model_preview(self, name: str | None = None) -> str:
        from gracy.explore import _codegen

        return _codegen.render_models_source(self._data, only=name)

    def class_preview(self) -> str:
        from gracy.explore import _codegen

        return _codegen.render_class_source(self._data, _codegen.class_name_for(self.session_path.stem))

    # ------------------------------------------------------------------ persistence / codegen

    def persist(self) -> Path:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(json.dumps(self._data, indent=2) + "\n", "utf-8")
        return self.session_path

    def save_code(self, out: str | Path, *, tests: bool = False) -> list[Path]:
        from gracy.explore import _codegen

        return _codegen.save_code(self._data, Path(out), tests=tests)
