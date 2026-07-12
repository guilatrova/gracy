"""Command grammar for the gracy explorer (`gracy explore` REPL and `gracy x` one-shot).

httpie-flavoured request pairs:
    k==v        query parameter
    k=v         body field (string value)
    k:=v        body field (raw JSON value)
    @file       body from a file (JSON when it parses, raw text otherwise)
    {inline}    inline JSON body (may be unquoted - braces are matched pre-shlex)
    -H 'N: v'   request header

Pure parsing (the only I/O is reading an ``@file`` body); every error is a
:class:`ParseError` carrying a usage hint.
"""

from __future__ import annotations

import json
import shlex
import typing as t
from dataclasses import dataclass, field
from pathlib import Path

from gracy.explore._session import parse_auth, parse_retry, parse_throttle, validate_on_action

__all__ = ["Command", "ParseError", "parse_command", "HELP_TEXT"]

METHODS: t.Final = ("get", "post", "put", "patch", "delete", "head")
SHOW_TARGETS: t.Final = ("last", "model", "class", "endpoints", "history", "captures")

USAGE: t.Final[dict[str, str]] = {
    "request": "get|post|put|patch|delete|head <path> [k==v]... [k=v]... [k:=v]... [@file] [{json}] [-H 'Name: v']...",
    "endpoint": "endpoint <EndpointName>    turn the last request into a named endpoint (repeat to fold a 2nd call in)",
    "model": "model <Name>[!request]",
    "rename": "rename endpoint|model <old> <new>",
    "list": "list | ls    list named endpoints (alias of 'show endpoints')",
    "on": "on <status> none|raise:<ExcName>|<literal>    e.g. on 404 none",
    "param": "param <index> as <name>",
    "peek": "peek <path>    show a value from the last response, e.g. peek results[0].name",
    "set": "set <name> <path>    capture a value from the last response, e.g. set berry results[0].name",
    "retry": "retry <n> on <codes> [wait <s>[x<mult>]]    e.g. retry 3 on 429,503 wait 0.5x2",
    "throttle": "throttle <n>/<per>    e.g. throttle 5/1s",
    "timeout": "timeout <seconds>",
    "auth": "auth bearer <token> | auth basic <user> <pass>",
    "header": "header <Name> <value>",
    "base": "base <url>",
    "show": "show last|model [Name]|class|endpoints|history|captures",
    "undo": "undo",
    "save": "save <file.py> [--tests]",
    "help": "help",
    "quit": "quit | exit",
}

HELP_TEXT: t.Final = "\n".join(
    (
        "commands:",
        *(f"  {usage}" for usage in USAGE.values()),
        "",
        "request pairs: k==v query · k=v body string · k:=v body json · @file body · {inline json} body",
        "env vars: $VAR / ${VAR} in headers/query/body resolve at request time (never stored resolved)",
        "captures: `set name <path>` snapshots a last-response value; {{name}} in a request expands to it (stored concrete)",
    )
)


class ParseError(ValueError):
    """A command line that does not match the grammar (message includes usage)."""

    def __init__(self, message: str, usage_key: str | None = None) -> None:
        if usage_key is not None:
            message = f"{message}\nusage: {USAGE[usage_key]}"
        super().__init__(message)


@dataclass
class Command:
    kind: str  # request|endpoint|model|rename|on|param|set|retry|throttle|timeout|auth|header|base|show|undo|save|help|quit
    method: str | None = None
    path: str | None = None  # request path (also the capture <path> for kind "set")
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    body_json: t.Any = None
    name: str | None = None  # endpoint/model/param name (or `show model <Name>`)
    status: int | None = None
    action: str | None = None
    index: int | None = None
    spec: str | None = None  # retry/throttle/auth policy spec (validated at parse time)
    value: str | None = None  # timeout/header value/base url
    target: str | None = None  # show target
    tests: bool = False


# --------------------------------------------------------------------------- inline json extraction


def _extract_json_spans(text: str) -> tuple[str, str | None, dict[str, t.Any]]:
    """Pull shell-unquoted JSON spans out of *text* BEFORE shlex tokenizes it.

    Two shapes are extracted (shlex would otherwise eat the inner quotes):
    - a word-initial ``{...}`` span -> the inline JSON body (``post /x {"a": 1}``)
    - ``key:={...}`` / ``key:=[...]`` pairs -> JSON body fields
      (``post /x tags:=["a","b"]`` works exactly like httpie)

    A ``{`` inside shell quotes (e.g. ``-H 'X: {v}'``) is left alone.
    Returns ``(remaining_text, inline_json | None, json_fields)``.
    """
    inline_json: str | None = None
    json_fields: dict[str, t.Any] = {}
    while True:
        span = _find_json_span(text)
        if span is None:
            return text.strip(), inline_json, json_fields
        start, end, key = span
        raw = text[start if key is None else text.index(":=", start) + 2 : end]
        if key is None:
            if inline_json is not None:
                raise ParseError("only one {inline json} body is allowed", "request")
            inline_json = raw
        else:
            try:
                json_fields[key] = json.loads(raw)
            except ValueError as exc:
                raise ParseError(f"invalid JSON value in {text[start:end]!r}: {exc}", "request") from None
        text = text[:start] + text[end:]


def _find_json_span(text: str) -> tuple[int, int, str | None] | None:
    """First unquoted JSON span: (start, end, key) - key None for an inline body."""
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch not in ("{", "["):
            continue
        if ch == "{" and (i == 0 or text[i - 1].isspace()):  # word-initial -> inline body
            return i, _match_brackets(text, i) + 1, None
        if text[i - 2 : i] == ":=":  # key:={...} or key:=[...]
            word_start = i - 2
            while word_start > 0 and not text[word_start - 1].isspace():
                word_start -= 1
            key = text[word_start : i - 2]
            if key and "'" not in key and '"' not in key:
                return word_start, _match_brackets(text, i) + 1, key
    return None


def _match_brackets(text: str, start: int) -> int:
    """Index of the bracket closing the ``{``/``[`` at *start* (JSON-string aware)."""
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in ("{", "["):
            depth += 1
        elif ch in ("}", "]"):
            depth -= 1
            if depth == 0:
                return i
    raise ParseError(f"unbalanced braces in JSON value: {text[start:]!r}", "request")


# --------------------------------------------------------------------------- request parsing


def _parse_request(
    method: str, tokens: list[str], inline_json: str | None, json_fields: dict[str, t.Any] | None = None
) -> Command:
    if not tokens:
        raise ParseError(f"{method} needs a <path>", "request")
    path = tokens[0]
    if path.startswith(("-", "@")) or "==" in path or ":=" in path:
        raise ParseError(f"{method} needs a <path> first (got {path!r})", "request")

    cmd = Command(kind="request", method=method.upper(), path=path)
    fields: dict[str, t.Any] = dict(json_fields or {})
    file_body: tuple[str, t.Any] | None = None  # ("json"|"raw", value)

    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "-H":
            if i + 1 >= len(tokens):
                raise ParseError("-H needs a value like -H 'Name: value'", "request")
            raw = tokens[i + 1]
            name, sep, value = raw.partition(":")
            if not sep or not name.strip():
                raise ParseError(f"invalid header {raw!r}; expected 'Name: value'", "request")
            cmd.headers[name.strip()] = value.strip()
            i += 2
            continue
        if token.startswith("@") and len(token) > 1:
            if file_body is not None:
                raise ParseError("only one @file body is allowed", "request")
            file_body = _read_body_file(token[1:])
        elif "==" in token:
            key, _, value = token.partition("==")
            if not key:
                raise ParseError(f"empty query name in {token!r}", "request")
            cmd.query[key] = value
        elif ":=" in token:
            key, _, value = token.partition(":=")
            if not key:
                raise ParseError(f"empty field name in {token!r}", "request")
            try:
                fields[key] = json.loads(value)
            except ValueError as exc:
                raise ParseError(f"invalid JSON value in {token!r}: {exc}", "request") from None
        elif "=" in token:
            key, _, value = token.partition("=")
            if not key:
                raise ParseError(f"empty field name in {token!r}", "request")
            fields[key] = value
        else:
            raise ParseError(f"unexpected token {token!r}", "request")
        i += 1

    bodies = sum((bool(fields), inline_json is not None, file_body is not None))
    if bodies > 1:
        raise ParseError("cannot mix k=v/k:=v fields, {inline json}, and @file bodies", "request")
    if inline_json is not None:
        try:
            cmd.body_json = json.loads(inline_json)
        except ValueError as exc:
            raise ParseError(f"invalid inline JSON body: {exc}", "request") from None
    elif file_body is not None:
        kind, value = file_body
        if kind == "json":
            cmd.body_json = value
        else:
            cmd.body = value
    elif fields:
        cmd.body_json = fields
    return cmd


def _read_body_file(path: str) -> tuple[str, t.Any]:
    file = Path(path).expanduser()
    try:
        text = file.read_text("utf-8")
    except OSError as exc:
        raise ParseError(f"cannot read @{path}: {exc}", "request") from None
    try:
        return "json", json.loads(text)
    except ValueError:
        return "raw", text


# --------------------------------------------------------------------------- other commands


def _exactly(tokens: list[str], n: int, usage_key: str) -> None:
    if len(tokens) != n:
        raise ParseError(f"wrong number of arguments for '{usage_key}'", usage_key)


def _parse_int(value: str, what: str, usage_key: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ParseError(f"{what} must be an integer (got {value!r})", usage_key) from None


def parse_command(line: str) -> Command:
    """Parse one explorer command line into a :class:`Command`.

    Raises :class:`ParseError` (with a usage hint) for anything off-grammar.
    """
    stripped = line.strip()
    if not stripped:
        raise ParseError("empty command; type 'help' for the command list")

    word = stripped.split(None, 1)[0].lower()
    inline_json: str | None = None
    json_fields: dict[str, t.Any] = {}
    if word in METHODS:  # only request lines may carry unquoted {json} / k:=[...] spans
        stripped, inline_json, json_fields = _extract_json_spans(stripped)

    try:
        tokens = shlex.split(stripped)
    except ValueError as exc:
        raise ParseError(f"cannot tokenize command: {exc}") from None
    if not tokens:  # line was pure inline json - no method
        raise ParseError("empty command; type 'help' for the command list")
    head, rest = tokens[0].lower(), tokens[1:]

    if head in METHODS:
        return _parse_request(head, rest, inline_json, json_fields)

    if head == "endpoint":
        _exactly(tokens, 2, "endpoint")
        return Command(kind="endpoint", name=rest[0])

    if head in ("list", "ls"):
        _exactly(tokens, 1, "list")
        return Command(kind="show", target="endpoints")

    if head == "rename":
        if len(rest) != 3 or rest[0].lower() not in ("endpoint", "model"):
            raise ParseError("expected 'rename endpoint|model <old> <new>'", "rename")
        return Command(kind="rename", target=rest[0].lower(), name=rest[1], value=rest[2])

    if head == "model":
        _exactly(tokens, 2, "model")
        return Command(kind="model", name=rest[0])

    if head == "on":
        if len(tokens) < 3:
            raise ParseError("on needs a status and an action", "on")
        status = _parse_int(rest[0], "status", "on")
        action = " ".join(rest[1:])
        try:
            validate_on_action(action)
        except ValueError as exc:
            raise ParseError(str(exc), "on") from None
        return Command(kind="on", status=status, action=action)

    if head == "param":
        _exactly(tokens, 4, "param")
        if rest[1].lower() != "as":
            raise ParseError("expected 'param <index> as <name>'", "param")
        index = _parse_int(rest[0], "index", "param")
        return Command(kind="param", index=index, name=rest[2])

    if head == "set":
        if len(tokens) < 3:
            raise ParseError("set needs a <name> and a <path>", "set")
        _exactly(tokens, 3, "set")
        return Command(kind="set", name=rest[0], path=rest[1])

    if head == "peek":
        _exactly(tokens, 2, "peek")
        return Command(kind="peek", path=rest[0])

    if head == "retry":
        if not rest:
            raise ParseError("retry needs a spec", "retry")
        spec = " ".join(rest)
        try:
            parse_retry(spec)
        except ValueError as exc:
            raise ParseError(str(exc), "retry") from None
        return Command(kind="retry", spec=spec)

    if head == "throttle":
        _exactly(tokens, 2, "throttle")
        try:
            parse_throttle(rest[0])
        except ValueError as exc:
            raise ParseError(str(exc), "throttle") from None
        return Command(kind="throttle", spec=rest[0])

    if head == "timeout":
        _exactly(tokens, 2, "timeout")
        try:
            float(rest[0])
        except ValueError:
            raise ParseError(f"timeout must be a number of seconds (got {rest[0]!r})", "timeout") from None
        return Command(kind="timeout", value=rest[0])

    if head == "auth":
        if not rest:
            raise ParseError("auth needs a spec", "auth")
        spec = " ".join(rest)
        try:
            parse_auth(spec)
        except ValueError as exc:
            raise ParseError(str(exc), "auth") from None
        return Command(kind="auth", spec=spec)

    if head == "header":
        if len(tokens) < 3:
            raise ParseError("header needs a name and a value", "header")
        return Command(kind="header", name=rest[0], value=" ".join(rest[1:]))

    if head == "base":
        _exactly(tokens, 2, "base")
        return Command(kind="base", value=rest[0])

    if head == "show":
        if not rest or rest[0].lower() not in SHOW_TARGETS:
            raise ParseError("show what?", "show")
        target = rest[0].lower()
        if target == "model" and len(rest) == 2:
            return Command(kind="show", target=target, name=rest[1])
        _exactly(tokens, 2, "show")
        return Command(kind="show", target=target)

    if head == "undo":
        _exactly(tokens, 1, "undo")
        return Command(kind="undo")

    if head == "save":
        if not rest:
            raise ParseError("save needs an output file", "save")
        tests = False
        path: str | None = None
        for token in rest:
            if token == "--tests":
                tests = True
            elif path is None and not token.startswith("-"):
                path = token
            else:
                raise ParseError(f"unexpected save argument {token!r}", "save")
        if path is None:
            raise ParseError("save needs an output file", "save")
        return Command(kind="save", path=path, tests=tests)

    if head == "help":
        return Command(kind="help")

    if head in ("quit", "exit"):
        return Command(kind="quit")

    if len(tokens) == 1 and ("[" in head or "." in head) and head[:1].isalpha():
        # a bare json-path like `results[0].name` -> they probably want to peek it
        raise ParseError(f"unknown command {head!r}; did you mean `peek {head}`?")
    raise ParseError(f"unknown command {head!r}; type 'help' for the command list")
