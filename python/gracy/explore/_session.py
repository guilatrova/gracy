"""ExploreSession — the `gracy -i` engine (no REPL in here).

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
import os
import re
import time
import typing as t
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gracy._types import Response
from gracy.client import Gracy
from gracy.config import Backoff, GracyConfig, Rate, Retry, Throttle, parse_duration
from gracy.replay import Scrub

__all__ = ["ExploreSession", "StepResult"]

SCHEMA_VERSION: t.Final = 1
MAX_STORED_BODY: t.Final = 256 * 1024  # response bodies are capped in the session file
_PREVIEW_CHARS: t.Final = 400

_ENV_RE: t.Final = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_SCRUBBED_HEADERS: t.Final = frozenset(h.lower() for h in Scrub().headers)

_RETRY_RE: t.Final = re.compile(
    r"^\s*(?P<attempts>\d+)\s+on\s+(?P<codes>\d+(?:\s*,\s*\d+)*)(?:\s+wait\s+(?P<wait>\S+))?\s*$"
)
_WAIT_RE: t.Final = re.compile(r"^(?P<initial>\d+(?:\.\d+)?)(?:x(?P<multiplier>\d+(?:\.\d+)?))?$")
_THROTTLE_RE: t.Final = re.compile(r"^\s*(?P<limit>\d+)\s*/\s*(?P<per>\S+)\s*$")


# --------------------------------------------------------------------------- env interpolation


def resolve_env(value: str) -> str:
    """Replace $VAR / ${VAR} with os.environ values; UNSET vars stay literal."""

    def _sub(m: re.Match[str]) -> str:
        var = m.group(1) or m.group(2)
        return os.environ.get(var, m.group(0))

    return _ENV_RE.sub(_sub, value)


def _referenced_env_values(*pieces: t.Any) -> set[str]:
    """The concrete os.environ VALUES a request's unresolved strings reference.

    Used to scrub secrets a server ECHOES back: the request stores placeholders,
    but the response may contain the resolved value verbatim — redact it before
    the response ever touches disk (recordings are git-committable)."""
    values: set[str] = set()

    def walk(v: t.Any) -> None:
        if isinstance(v, str):
            for m in _ENV_RE.finditer(v):
                var = m.group(1) or m.group(2)
                env_val = os.environ.get(var)
                if env_val:
                    values.add(env_val)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    for piece in pieces:
        walk(piece)
    return values


def _redact_values(value: t.Any, secrets: set[str]) -> t.Any:
    """Deep-replace any exact secret occurrence (whole or substring) with '***'."""
    if not secrets:
        return value
    if isinstance(value, str):
        for secret in secrets:
            if secret in value:
                value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {k: _redact_values(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_values(v, secrets) for v in value]
    return value


def _resolve_env_any(value: t.Any) -> t.Any:
    """Recursively resolve env placeholders in every string of a JSON-ish tree."""
    if isinstance(value, str):
        return resolve_env(value)
    if isinstance(value, dict):
        return {k: _resolve_env_any(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_any(v) for v in value]
    return value


def env_vars_in(value: str) -> list[str]:
    return [m.group(1) or m.group(2) for m in _ENV_RE.finditer(value)]


# --------------------------------------------------------------------------- path helpers


def split_segments(path: str) -> list[str]:
    return [seg for seg in path.split("/") if seg]


def join_segments(segments: t.Sequence[str]) -> str:
    return "/" + "/".join(segments)


def template_matches(template: str, path: str) -> bool:
    t_segs, p_segs = split_segments(template), split_segments(path)
    if len(t_segs) != len(p_segs):
        return False
    return all(ts.startswith("{") and ts.endswith("}") or ts == ps for ts, ps in zip(t_segs, p_segs))


def default_param_name(segments: t.Sequence[str], index: int, taken: t.Collection[str]) -> str:
    """Deterministic default: the preceding literal segment ("/pokemon/x" -> "pokemon"),
    falling back to "param_<index>" when there is none (or it's taken)."""
    if index > 0:
        prev = re.sub(r"\W+", "_", segments[index - 1]).strip("_").lower()
        if prev and not prev[0].isdigit() and prev not in taken and not (
            segments[index - 1].startswith("{")
        ):
            return prev
    name = f"param_{index}"
    while name in taken:
        name += "_"
    return name


def flatten_keys(obj: t.Any, prefix: str = "") -> set[str]:
    """Dot-path field names of a JSON dict tree (for model-drift detection)."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            keys.add(path)
            keys |= flatten_keys(v, path)
    return keys


# --------------------------------------------------------------------------- policy grammar


def parse_retry(spec: str) -> dict[str, t.Any]:
    """'3 on 429,503 wait 0.5x2' -> structured policy dict (stored in the session)."""
    m = _RETRY_RE.match(spec)
    if not m:
        raise ValueError(f"Invalid retry spec {spec!r}; expected '<n> on <codes> [wait <s>[x<mult>]]'")
    policy: dict[str, t.Any] = {
        "spec": spec.strip(),
        "attempts": int(m.group("attempts")),
        "codes": [int(c.strip()) for c in m.group("codes").split(",")],
    }
    wait = m.group("wait")
    if wait is not None:
        wm = _WAIT_RE.match(wait)
        if not wm:
            raise ValueError(f"Invalid retry wait {wait!r}; expected e.g. '0.5' or '0.5x2'")
        if wm.group("multiplier") is not None:
            policy["wait"] = {"initial": float(wm.group("initial")), "multiplier": float(wm.group("multiplier"))}
        else:
            policy["wait"] = float(wm.group("initial"))
    return policy


def parse_throttle(spec: str) -> dict[str, t.Any]:
    """'5/1s' -> {"limit": 5, "per": "1s"}."""
    m = _THROTTLE_RE.match(spec)
    if not m:
        raise ValueError(f"Invalid throttle spec {spec!r}; expected '<n>/<per>' e.g. '5/1s'")
    per = m.group("per")
    parse_duration(per)  # validate eagerly
    return {"spec": spec.strip(), "limit": int(m.group("limit")), "per": per}


def parse_auth(spec: str) -> dict[str, t.Any]:
    """'bearer $TOK' | 'basic user pass' -> structured auth policy."""
    parts = spec.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return {"scheme": "bearer", "token": parts[1]}
    if len(parts) == 3 and parts[0].lower() == "basic":
        return {"scheme": "basic", "user": parts[1], "password": parts[2]}
    raise ValueError(f"Invalid auth spec {spec!r}; expected 'bearer <token>' or 'basic <user> <pass>'")


def retry_to_config(policy: dict[str, t.Any]) -> Retry:
    from gracy.config import status as status_set

    wait_raw = policy.get("wait", 1.0)
    wait: float | Backoff
    if isinstance(wait_raw, dict):
        wait = Backoff(initial=float(wait_raw["initial"]), multiplier=float(wait_raw["multiplier"]))
    else:
        wait = float(wait_raw)
    return Retry(on=status_set(*policy["codes"]), attempts=int(policy["attempts"]), wait=wait)


def throttle_to_config(policy: dict[str, t.Any]) -> Throttle:
    return Throttle(rules=[Rate(int(policy["limit"]), per=policy["per"])])


def auth_header_value(auth: dict[str, t.Any], *, resolve: bool) -> str:
    """The Authorization header value for an auth policy (env resolved when asked)."""
    if auth["scheme"] == "bearer":
        token = resolve_env(auth["token"]) if resolve else auth["token"]
        return f"Bearer {token}"
    user = resolve_env(auth["user"]) if resolve else auth["user"]
    password = resolve_env(auth["password"]) if resolve else auth["password"]
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


def validate_on_action(action: str) -> None:
    """Grammar: "none" | "raise:<ExcName>" | a python literal like "{}"."""
    import ast

    if action == "none":
        return
    if action.startswith("raise:"):
        name = action[len("raise:") :]
        if not name.isidentifier():
            raise ValueError(f"Invalid exception name in {action!r}")
        return
    try:
        ast.literal_eval(action)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(
            f"Invalid on-action {action!r}; use 'none', 'raise:<ExcName>', or a literal like '{{}}'"
        ) from exc


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
        }
        if self.session_path.exists():
            loaded = json.loads(self.session_path.read_text("utf-8"))
            if loaded.get("schema") != SCHEMA_VERSION:
                raise ValueError(
                    f"Session file {self.session_path} has schema {loaded.get('schema')!r}; "
                    f"this gracy understands schema {SCHEMA_VERSION}"
                )
            self._data.update(loaded)
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

    # ------------------------------------------------------------------ undo bookkeeping

    def _snapshot(self, label: str) -> None:
        self._undo_stack.append((label, copy.deepcopy(self._data)))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)

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
        if "?" in path:  # tolerate query strings pasted into the path
            from urllib.parse import parse_qsl, urlsplit

            split = urlsplit(path)
            path = split.path
            merged = dict(parse_qsl(split.query))
            merged.update(query or {})
            query = merged

        if not path.lower().startswith(("http://", "https://")) and not self._data.get("base_url"):
            raise ValueError("No base_url set — use set_policy(base_url=...) or pass an absolute URL")

        # -- resolved (wire) values vs unresolved (persisted) values
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
                path,
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
            # A server may echo a resolved secret back — redact any env value the
            # request referenced before the response touches disk.
            secrets = _referenced_env_values(headers, query, body, body_json, self._policy_headers(resolve=False))
            # Store the parsed JSON in full (never truncated) — model inference
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
            raise ValueError(f"Endpoint {name!r}: steps have different path depths; cannot template")
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
        for step in reversed(self._data["steps"]):
            if step.get("endpoint"):
                return t.cast(str, step["endpoint"])
        raise ValueError("No named endpoint yet — run name_endpoint() first")

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
