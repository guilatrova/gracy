"""Minimal YAML emitter (block style, 2-space indent) — gracy has ZERO runtime deps.

Supports dicts (str keys), lists/tuples, str/int/float/bool/None. Strings are
kept plain only when unambiguously safe; everything else is JSON-double-quoted
(a valid YAML scalar). Multiline strings become literal blocks (|- / |), with a
quoted fallback when literal style can't represent them losslessly.
"""

from __future__ import annotations

import json
import re
import typing as t

__all__ = ["dumps"]

_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-/ ]*$")  # conservative: quote anything else
_KEYWORDS = frozenset({"true", "false", "null", "yes", "no", "on", "off", "none", "~"})


def _scalar(value: t.Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if _PLAIN.match(value) and value == value.rstrip() and value.lower() not in _KEYWORDS:
            return value
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"Cannot serialize {type(value).__name__!r} to YAML: {value!r}")


def _literal_block(value: str, indent: int) -> list[str] | None:
    """Literal-block lines for a multiline string, or None when unrepresentable."""
    chomp, body = "-", value
    if body.endswith("\n") and not body.endswith("\n\n"):
        chomp, body = "", body[:-1]
    lines = body.split("\n")
    if body.endswith("\n") or lines[0].startswith(" ") or any(ln != ln.rstrip() for ln in lines):
        return None  # trailing newlines / indented first line / trailing spaces: quote instead
    pad = " " * indent
    return ["|" + chomp] + [pad + ln if ln else "" for ln in lines]


def _emit_entry(prefix: str, value: t.Any, indent: int, out: list[str]) -> None:
    """Emit `value` after `prefix` ("key:" or "-"); children indent by 2."""
    if isinstance(value, dict):
        if not value:
            out.append(prefix + " {}")
            return
        out.append(prefix)
        _emit_mapping(value, indent + 2, out)
    elif isinstance(value, (list, tuple)):
        if not value:
            out.append(prefix + " []")
            return
        out.append(prefix)
        _emit_sequence(value, indent + 2, out)
    elif isinstance(value, str) and "\n" in value:
        block = _literal_block(value, indent + 2)
        if block is None:
            out.append(prefix + " " + json.dumps(value, ensure_ascii=False))
        else:
            out.append(prefix + " " + block[0])
            out.extend(block[1:])
    else:
        out.append(prefix + " " + _scalar(value))


def _emit_mapping(mapping: dict[str, t.Any], indent: int, out: list[str]) -> None:
    pad = " " * indent
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise TypeError(f"YAML mapping keys must be str (JSON-compatible), got {key!r}")
        _emit_entry(f"{pad}{_scalar(key)}:", value, indent, out)


def _emit_sequence(seq: t.Sequence[t.Any], indent: int, out: list[str]) -> None:
    pad = " " * indent
    for item in seq:
        _emit_entry(pad + "-", item, indent, out)


def dumps(data: t.Any) -> str:
    out: list[str] = []
    if isinstance(data, dict):
        _emit_mapping(data, 0, out) if data else out.append("{}")
    elif isinstance(data, (list, tuple)):
        _emit_sequence(data, 0, out) if data else out.append("[]")
    elif isinstance(data, str) and "\n" in data:
        block = _literal_block(data, 2)
        if block is None:
            out.append(json.dumps(data, ensure_ascii=False))
        else:
            out.extend(block)
    else:
        out.append(_scalar(data))
    return "\n".join(out) + "\n"
