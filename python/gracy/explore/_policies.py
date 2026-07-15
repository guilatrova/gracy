"""Explore policy grammar: retry/throttle/auth specs and on-action values."""

from __future__ import annotations

import base64
import re
import typing as t

from gracy.config import Backoff, Rate, Retry, Throttle, parse_duration
from gracy.explore._env import resolve_env

_RETRY_RE: t.Final = re.compile(
    r"^\s*(?P<attempts>\d+)\s+on\s+(?P<codes>\d+(?:\s*,\s*\d+)*)(?:\s+wait\s+(?P<wait>\S+))?\s*$"
)
_WAIT_RE: t.Final = re.compile(r"^(?P<initial>\d+(?:\.\d+)?)(?:x(?P<multiplier>\d+(?:\.\d+)?))?$")
_THROTTLE_RE: t.Final = re.compile(r"^\s*(?P<limit>\d+)\s*/\s*(?P<per>\S+)\s*$")


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


_BARE_WORD_ACTION: t.Final = re.compile(r"^[A-Za-z][\w\- ]*$")


def on_action_value(action: str) -> t.Any:
    """The value a non-special on-action maps to. A python literal ('{}', '0',
    '\"hi\"') is eval'd; a bare word/phrase ('unavailable', 'not found') is taken
    as a plain string, so you don't have to quote simple values."""
    import ast

    try:
        return ast.literal_eval(action)
    except (ValueError, SyntaxError):
        if _BARE_WORD_ACTION.match(action):
            return action
        raise


def validate_on_action(action: str) -> None:
    """Grammar: "none" | "raise:<ExcName>" | a python literal ('{}') or a bare word."""
    if action == "none":
        return
    if action.startswith("raise:"):
        name = action[len("raise:") :]
        if not name.isidentifier():
            raise ValueError(f"Invalid exception name in {action!r}")
        return
    try:
        on_action_value(action)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(
            f"Invalid on-action {action!r}; use 'none', 'raise:<ExcName>', a bare word, or a literal like '{{}}'"
        ) from exc
