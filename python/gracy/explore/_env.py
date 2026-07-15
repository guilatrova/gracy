"""Env-placeholder interpolation for explore sessions.

"$VAR"/"${VAR}" placeholders in headers/query/body strings are resolved from
the environment at EXECUTION time only; the session file always stores the
UNRESOLVED placeholder.
"""

from __future__ import annotations

import os
import re
import typing as t

_ENV_RE: t.Final = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def resolve_env(value: str) -> str:
    """Replace $VAR / ${VAR} with os.environ values; UNSET vars stay literal."""

    def _sub(m: re.Match[str]) -> str:
        var = m.group(1) or m.group(2)
        return os.environ.get(var, m.group(0))

    return _ENV_RE.sub(_sub, value)


def _referenced_env_values(*pieces: t.Any) -> set[str]:
    """The concrete os.environ VALUES a request's unresolved strings reference.

    Used to scrub secrets a server ECHOES back: the request stores placeholders,
    but the response may contain the resolved value verbatim - redact it before
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
