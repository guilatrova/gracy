"""Shape-drift detection: compare a recorded response shape against a live one.

Pure functions (no I/O) so they unit-test cleanly; the CLI (`gracy explore
--check`) does the live requests and feeds the two JSON bodies in here.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field


def _typename(value: t.Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def flatten_typed(value: t.Any, prefix: str = "") -> dict[str, str]:
    """Dot-path -> scalar/container type name for every leaf and node.

    Lists of dicts merge their items' shapes under ``path[]`` so a new field
    appearing in only some list items is still detected.
    """
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, sub in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out[path] = _typename(sub)
            out.update(flatten_typed(sub, path))
    elif isinstance(value, list):
        item_prefix = f"{prefix}[]"
        for item in value:
            if isinstance(item, (dict, list)):
                out.update(flatten_typed(item, item_prefix))
    return out


@dataclass
class ShapeDiff:
    added: list[str] = field(default_factory=list)  # in live, not recorded
    removed: list[str] = field(default_factory=list)  # in recorded, not live (the scary one)
    type_changed: list[str] = field(default_factory=list)  # "path: old -> new"

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.type_changed)

    def as_dict(self) -> dict[str, list[str]]:
        return {"added": self.added, "removed": self.removed, "type_changed": self.type_changed}


def diff_shape(recorded: t.Any, live: t.Any) -> ShapeDiff:
    """What changed going from the recorded body to the live one.

    ``null`` never counts as a type change against a concrete type (a field
    that was null when recorded and is now populated is not drift), but a
    field that vanished (``removed``) or whose concrete type flipped is.
    """
    rec = flatten_typed(recorded)
    now = flatten_typed(live)
    diff = ShapeDiff()
    for path in sorted(set(rec) | set(now)):
        r, n = rec.get(path), now.get(path)
        if r is None:
            diff.added.append(path)
        elif n is None:
            diff.removed.append(path)
        elif r != n and "null" not in (r, n):
            diff.type_changed.append(f"{path}: {r} -> {n}")
    return diff


@dataclass
class EndpointDrift:
    endpoint: str
    method: str
    template: str
    ok: bool  # True = no drift (and the live call succeeded)
    status_recorded: int | None = None
    status_live: int | None = None
    shape: ShapeDiff = field(default_factory=ShapeDiff)
    error: str | None = None
    note: str | None = None  # e.g. "no recorded sample to compare"

    def as_dict(self) -> dict[str, t.Any]:
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "template": self.template,
            "ok": self.ok,
            "status_recorded": self.status_recorded,
            "status_live": self.status_live,
            "shape": self.shape.as_dict(),
            "error": self.error,
            "note": self.note,
        }
