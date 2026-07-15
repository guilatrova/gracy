"""URL-path segment/template helpers and dot/bracket JSON-path resolution."""

from __future__ import annotations

import re
import typing as t

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


# --------------------------------------------------------------------------- capture paths


def _tokenize_path(path: str) -> list[str | int]:
    """Break 'results[0].name' into ['results', 0, 'name'] (bare keys + [int] indices)."""
    tokens: list[str | int] = []
    i, n = 0, len(path)
    while i < n:
        ch = path[i]
        if ch == ".":
            i += 1
            continue
        if ch == "[":
            end = path.find("]", i)
            if end == -1:
                raise ValueError(f"unclosed '[' in path {path!r}")
            inner = path[i + 1 : end]
            try:
                tokens.append(int(inner))
            except ValueError:
                raise ValueError(f"invalid list index {inner!r} in path {path!r}") from None
            i = end + 1
            continue
        j = i
        while j < n and path[j] not in ".[":
            j += 1
        key = path[i:j]
        if not key:
            raise ValueError(f"empty segment in path {path!r}")
        tokens.append(key)
        i = j
    if not tokens:
        raise ValueError(f"empty path {path!r}")
    return tokens


def resolve_json_path(root: t.Any, path: str) -> t.Any:
    """Walk a dot/bracket path into parsed JSON; errors name the failing segment."""
    current = root
    traversed = ""
    for token in _tokenize_path(path):
        if isinstance(token, int):
            traversed += f"[{token}]"
            if not isinstance(current, list):
                raise ValueError(f"cannot index into non-list at {traversed!r}")
            try:
                current = current[token]
            except IndexError:
                raise ValueError(f"index {token} out of range at {traversed!r}") from None
        else:
            traversed = f"{traversed}.{token}" if traversed else token
            if not isinstance(current, dict):
                raise ValueError(f"cannot read key {token!r} from non-object at {traversed!r}")
            if token not in current:
                raise ValueError(f"no key {token!r} at {traversed!r}")
            current = current[token]
    return current
