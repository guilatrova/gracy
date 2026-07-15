"""The `gracy explore` REPL and the shared command dispatcher (`gracy x` uses it too).

The module imports only the stdlib; rich is pulled in lazily inside the render
helpers so one-shot ``gracy x --json`` works without any optional dependency.
"""

from __future__ import annotations

import json
import sys
import typing as t
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gracy.explore._parser import HELP_TEXT, METHODS, SHOW_TARGETS, USAGE, Command, ParseError, parse_command
from gracy.explore._session import _CAPTURE_RE, ExploreSession, StepResult, split_segments

__all__ = ["Outcome", "execute_command", "run_repl"]

HISTORY_FILE: t.Final = Path.home() / ".gracy_history"
PROMPT: t.Final = "gracy› "

# Top-level command words offered by Tab completion (kept in sync with the parser).
# `endpoint` before `ep` so the ghost hint prefers the full, clearer word.
COMMANDS: t.Final = (
    *METHODS,
    "endpoint", "ep", "model", "rename", "drop", "on", "param", "set", "peek", "retry", "throttle",
    "timeout", "auth", "header", "base", "show", "list", "ls", "undo", "export", "help", "quit", "exit",
)


@dataclass
class Outcome:
    """One executed command: machine dict (for --json), plain line, extras."""

    kind: str
    data: dict[str, t.Any]
    human: str
    step: StepResult | None = None
    hints: list[str] = field(default_factory=list)
    panel: tuple[str, str] | None = None  # (title, python source) for show model/class


# --------------------------------------------------------------------------- dispatch


async def execute_command(session: ExploreSession, cmd: Command) -> Outcome:
    """Run one parsed command against the session (shared by REPL and one-shot)."""
    handler = _HANDLERS.get(cmd.kind)
    if handler is None:  # unreachable through parse_command; belt and braces
        raise ValueError(f"unhandled command kind {cmd.kind!r}")
    return await handler(session, cmd)


async def _do_request(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.method is not None and cmd.path is not None
    result = await session.execute(
        cmd.method,
        cmd.path,
        query=cmd.query or None,
        headers=cmd.headers or None,
        body=cmd.body,
        body_json=cmd.body_json,
    )
    hints: list[str] = []
    if result.template_proposal:
        hints.append(
            f"✨ one segment differs from an existing endpoint - template proposal: "
            f"{result.template_proposal} (run `endpoint <name>` to fold it in)"
        )
    hints.extend(f"✨ {line}" for line in result.model_drift)
    if result.matched_endpoint is None and result.template_proposal is None and result.error is None:
        hints.append(f"✨ tip: `endpoint {_suggest_name(result.path)}` to turn this request into an endpoint")

    status = str(result.status) if result.status is not None else "ERROR"
    human = f"{result.method} {result.url} -> {status} ({result.elapsed_ms:.1f} ms)"
    if result.error:
        human += f" - {result.error}"
    return Outcome("request", asdict(result), human, step=result, hints=hints)


def _suggest_name(path: str) -> str:
    from gracy.explore._infer import pascal

    segments = split_segments(path)
    return pascal(segments[0]) if segments else "Endpoint"


async def _do_endpoint(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.name is not None
    existed = cmd.name in session.endpoints()
    template = session.name_endpoint(cmd.name)
    verb = "folded into" if existed else "created endpoint"
    hints: list[str] = []
    params = session.endpoints()[cmd.name]["params"]
    if params:
        listed = ", ".join(f"{{{p['name']}}} (segment {p['index']})" for p in params)
        hints.append(f"✨ params: {listed} - rename with `param <index> as <name>`")
    return Outcome(
        "endpoint",
        {"ok": True, "endpoint": cmd.name, "template": template, "created": not existed},
        f"{verb} '{cmd.name}' -> {template}",
        hints=hints,
    )


async def _do_rename(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.target is not None and cmd.name is not None and cmd.value is not None
    if cmd.target == "endpoint":
        session.rename_endpoint(cmd.name, cmd.value)
    else:
        session.rename_model(cmd.name, cmd.value)
    return Outcome(
        "rename",
        {"ok": True, "target": cmd.target, "old": cmd.name, "new": cmd.value},
        f"renamed {cmd.target} '{cmd.name}' -> '{cmd.value}'",
    )


async def _do_drop(session: ExploreSession, cmd: Command) -> Outcome:
    if cmd.target == "endpoint":
        assert cmd.name is not None
        freed = session.drop_endpoint(cmd.name)
        note = f" ({freed} step{'' if freed == 1 else 's'} now unnamed)" if freed else ""
        return Outcome(
            "drop",
            {"ok": True, "target": "endpoint", "name": cmd.name, "freed": freed},
            f"dropped endpoint '{cmd.name}'{note}",
        )
    assert cmd.index is not None
    info = session.drop_step(cmd.index)
    tail = f" (was in {info['endpoint']})" if info["endpoint"] else " (unnamed)"
    return Outcome(
        "drop",
        {"ok": True, "target": "step", **info},
        f"dropped step {cmd.index} {info['path']}{tail}",
    )


async def _do_model(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.name is not None
    session.set_model_name(cmd.name)
    which = "request" if cmd.name.endswith("!request") else "response"
    plain = cmd.name.removesuffix("!request")
    return Outcome("model", {"ok": True, "model": plain, "target": which}, f"{which} model named '{plain}'")


async def _do_on(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.status is not None and cmd.action is not None
    endpoint = session._last_endpoint()  # noqa: SLF001 - same package
    session.set_on(endpoint, cmd.status, cmd.action)
    return Outcome(
        "on",
        {"ok": True, "endpoint": endpoint, "status": cmd.status, "action": cmd.action},
        f"{endpoint}: on {cmd.status} -> {cmd.action}",
    )


async def _do_param(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.index is not None and cmd.name is not None
    endpoint = session._last_endpoint()  # noqa: SLF001 - same package
    template = session.set_param_name(endpoint, cmd.index, cmd.name)
    return Outcome(
        "param",
        {"ok": True, "endpoint": endpoint, "template": template},
        f"{endpoint}: {template}",
    )


async def _do_set(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.name is not None and cmd.path is not None
    value = session.capture(cmd.name, cmd.path)
    return Outcome(
        "set",
        {"ok": True, "name": cmd.name, "value": value, "path": cmd.path},
        f"captured {cmd.name} = {value} (from {cmd.path})",
    )


async def _do_peek(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.path is not None
    value = session.peek(cmd.path)  # resolve-only, no capture
    return Outcome(
        "peek",
        {"ok": True, "path": cmd.path, "value": value},
        f"{cmd.path} = {value!r}",
    )


async def _do_policy(session: ExploreSession, cmd: Command) -> Outcome:
    kwargs: dict[str, t.Any] = {}
    if cmd.kind == "retry":
        kwargs["retry"] = cmd.spec
    elif cmd.kind == "throttle":
        kwargs["throttle"] = cmd.spec
    elif cmd.kind == "timeout":
        kwargs["timeout"] = float(t.cast(str, cmd.value))
    elif cmd.kind == "auth":
        kwargs["auth"] = cmd.spec
    elif cmd.kind == "header":
        kwargs["header"] = (t.cast(str, cmd.name), t.cast(str, cmd.value))
    elif cmd.kind == "base":
        kwargs["base_url"] = cmd.value
    line = session.set_policy(**kwargs)
    return Outcome(cmd.kind, {"ok": True, "policy": line}, line)


async def _do_show(session: ExploreSession, cmd: Command) -> Outcome:
    if cmd.target == "last":
        history = session.history()
        if not history:
            raise ValueError("no steps yet - run a request first")
        last = history[-1]
        step = StepResult(**last)
        status = str(step.status) if step.status is not None else "ERROR"
        human = f"{step.method} {step.url} -> {status} ({step.elapsed_ms:.1f} ms)"
        return Outcome("request", last, human, step=step)
    if cmd.target == "model":
        source = session.model_preview(cmd.name)
        return Outcome("show_model", {"ok": True, "source": source}, source, panel=(cmd.name or "models", source))
    if cmd.target == "class":
        source = session.class_preview()
        return Outcome("show_class", {"ok": True, "source": source}, source, panel=("class", source))
    if cmd.target == "endpoints":
        endpoints = session.endpoints()
        lines = [
            f"{ep['method']:6} {ep['template']}  -> {name}"
            f" (steps={ep['steps']}, on={ep['on'] or '{}'},"
            f" response={ep['response_model'] or '(auto)'})"
            for name, ep in endpoints.items()
        ]
        return Outcome("endpoints", {"ok": True, "endpoints": endpoints}, "\n".join(lines) or "(no endpoints yet)")
    if cmd.target == "history":
        history = session.history()
        lines = [
            f"{h['step_id']:>3}  {h['method']:6} {h['path']}  "
            f"{h['status'] if h['status'] is not None else 'ERR'}  {h['elapsed_ms']:.0f}ms"
            + (f"  [{h['matched_endpoint']}]" if h["matched_endpoint"] else "")
            for h in history
        ]
        return Outcome("history", {"ok": True, "history": history}, "\n".join(lines) or "(no steps yet)")
    if cmd.target == "captures":
        captures = session.captures
        lines = [f"{name} = {value}" for name, value in captures.items()]
        return Outcome("captures", {"ok": True, "captures": dict(captures)}, "\n".join(lines) or "(none yet)")
    raise ValueError(f"unknown show target {cmd.target!r}")


async def _do_undo(session: ExploreSession, cmd: Command) -> Outcome:
    line = session.undo()
    return Outcome("undo", {"ok": True, "undo": line}, line)


async def _do_export(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.path is not None
    paths = session.save_code(cmd.path, tests=cmd.tests)
    written = [str(p) for p in paths]
    return Outcome("export", {"ok": True, "written": written}, "wrote " + ", ".join(written))


async def _do_help(session: ExploreSession, cmd: Command) -> Outcome:
    return Outcome("help", {"ok": True, "help": HELP_TEXT}, HELP_TEXT)


async def _do_quit(session: ExploreSession, cmd: Command) -> Outcome:
    return Outcome("quit", {"ok": True}, "bye")


_HANDLERS: t.Final[dict[str, t.Callable[[ExploreSession, Command], t.Awaitable[Outcome]]]] = {
    "request": _do_request,
    "endpoint": _do_endpoint,
    "rename": _do_rename,
    "drop": _do_drop,
    "model": _do_model,
    "on": _do_on,
    "param": _do_param,
    "set": _do_set,
    "peek": _do_peek,
    "retry": _do_policy,
    "throttle": _do_policy,
    "timeout": _do_policy,
    "auth": _do_policy,
    "header": _do_policy,
    "base": _do_policy,
    "show": _do_show,
    "undo": _do_undo,
    "export": _do_export,
    "help": _do_help,
    "quit": _do_quit,
}


# --------------------------------------------------------------------------- rendering (rich, lazy)


def _make_console() -> t.Any:
    try:
        from rich.console import Console
    except ImportError:  # pragma: no cover - venvs in this repo have rich
        raise RuntimeError(
            "gracy explore needs the optional 'rich' package - install it with: pip install 'gracy[rich]'"
        ) from None
    console = Console()
    if not console.is_terminal:  # stable layout for pipes / tests
        console = Console(width=120)
    return console


def _status_style(status: int | None) -> str:
    if status is None:
        return "bold red"
    if status < 300:
        return "bold green"
    if status < 400:
        return "bold cyan"
    if status < 500:
        return "bold yellow"
    return "bold red"


def _render_step(console: t.Any, step: StepResult) -> None:
    from rich.pretty import Pretty
    from rich.text import Text

    status = str(step.status) if step.status is not None else "ERROR"
    line = Text()
    line.append(step.method, style="bold")
    line.append(f" {step.url} ")
    line.append(status, style=_status_style(step.status))
    line.append(f"  {step.elapsed_ms:.1f} ms", style="dim")
    if step.matched_endpoint:
        line.append(f"  [{step.matched_endpoint}]", style="dim italic")
    console.print(line)
    if step.error:
        console.print(f"[red]{step.error}[/red]")
    if step.body_preview is not None:
        console.print(Pretty(step.body_preview, max_depth=4, max_length=24, max_string=200, indent_size=2))


def render_outcome(console: t.Any, outcome: Outcome) -> None:
    if outcome.step is not None:
        _render_step(console, outcome.step)
    elif outcome.panel is not None:
        from rich.panel import Panel
        from rich.syntax import Syntax

        title, source = outcome.panel
        console.print(Panel(Syntax(source, "python", background_color="default"), title=title, expand=False))
    elif outcome.kind == "peek":
        from rich.pretty import Pretty

        # pretty-print the resolved value so nested structures stay readable
        console.print(f"[dim]{outcome.data['path']} =[/dim]")
        console.print(Pretty(outcome.data["value"], max_depth=4, max_length=24, max_string=200, indent_size=2))
    else:
        console.print(outcome.human)
    for hint in outcome.hints:
        console.print(f"[magenta]{hint}[/magenta]")


# --------------------------------------------------------------------------- the loop


def _seen_paths(session: ExploreSession) -> list[str]:
    return sorted({step["path"] for step in session.history() if step.get("path")})


def _model_names(session: ExploreSession) -> list[str]:
    names = {ep["response_model"] for ep in session.endpoints().values() if ep.get("response_model")}
    return sorted(n for n in names if n)


def candidates_for(session: ExploreSession, leading: str, text: str) -> list[str]:
    """Context-aware completion candidates for the current word.

    ``leading`` is the line up to (not including) the word being typed; ``text``
    is that word. Shared by the readline completer and the prompt_toolkit
    completer + autosuggest so all three stay consistent.
    """
    parts = leading.split()

    if text.startswith("{{"):  # a capture ref: complete {{name}} from stored captures
        inner = text[2:].lstrip()
        return ["{{" + name + "}}" for name in session.captures if name.startswith(inner)]

    if not parts:  # first word -> command names
        return [c + " " for c in COMMANDS if c.startswith(text)]

    cmd = parts[0].lower()
    if cmd in METHODS:  # paths already seen this session
        return [p for p in _seen_paths(session) if p.startswith(text)]
    if cmd == "show":
        if len(parts) == 1:
            return [t_ + " " for t_ in SHOW_TARGETS if t_.startswith(text)]
        if len(parts) == 2 and parts[1] == "model":
            return [n for n in _model_names(session) if n.startswith(text)]
    if cmd in ("endpoint", "ep") and len(parts) == 1:  # a NEW name, or an existing one to fold into
        return [n for n in session.endpoints() if n.startswith(text)]
    if cmd == "rename":
        if len(parts) == 1:
            return [w + " " for w in ("endpoint", "model") if w.startswith(text)]
        if len(parts) == 2 and parts[1] == "endpoint":  # <old> position
            return [n for n in session.endpoints() if n.startswith(text)]
        if len(parts) == 2 and parts[1] == "model":
            return [n for n in _model_names(session) if n.startswith(text)]
    if cmd == "drop":
        if len(parts) == 1:
            return [w + " " for w in ("endpoint", "step") if w.startswith(text)]
        if len(parts) == 2 and parts[1] == "endpoint":
            return [n for n in session.endpoints() if n.startswith(text)]
    if cmd == "on" and len(parts) == 2:  # the action position
        return [a for a in ("none", "raise:") if a.startswith(text)]
    if cmd == "auth" and len(parts) == 1:
        return [s + " " for s in ("bearer", "basic") if s.startswith(text)]
    if cmd == "param" and len(parts) == 2:
        return ["as "] if "as".startswith(text) else []
    if cmd in ("export", "save"):
        import glob

        files = [p for p in glob.glob(text + "*") if p.endswith(".py") or Path(p).is_dir()]
        return files + (["--tests"] if "--tests".startswith(text) else [])
    return []


class _Completer:
    """readline (fallback) Tab completion adapter over ``candidates_for``."""

    def __init__(self, session: ExploreSession) -> None:
        self.session = session
        self._matches: list[str] = []

    def complete(self, text: str, state: int) -> str | None:
        if state == 0:
            try:
                self._matches = self._candidates(text)
            except Exception:  # noqa: BLE001 - completion must never break the prompt
                self._matches = []
        return self._matches[state] if state < len(self._matches) else None

    def _candidates(self, text: str) -> list[str]:
        import readline

        buffer = readline.get_line_buffer()
        return candidates_for(self.session, buffer[: readline.get_begidx()], text)


def _setup_readline(session: ExploreSession) -> None:
    try:
        import atexit
        import readline

        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
        readline.set_history_length(1000)
        atexit.register(lambda: _write_history(readline))

        completer = _Completer(session)
        readline.set_completer(completer.complete)
        readline.set_completer_delims(" ")  # only spaces split words, so "/path" completes whole
        # libedit (macOS default) vs GNU readline bind syntax differ
        if "libedit" in (getattr(readline, "__doc__", "") or ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:  # noqa: BLE001 - readline is best-effort (absent on some builds)
        pass


def _write_history(readline: t.Any) -> None:
    try:
        readline.write_history_file(str(HISTORY_FILE))
    except OSError:
        pass


# --------------------------------------------------------------------------- ghost text (prompt_toolkit)


def suggest_suffix(session: ExploreSession, text_before: str, history: t.Sequence[str] = ()) -> str:
    """The inline 'ghost text' to show after the cursor: the completion of the
    current word (e.g. 'g' -> 'et'), falling back to the most recent matching
    history line. Returns '' when there is nothing to suggest. Pure/testable."""
    if not text_before or text_before.endswith(" "):
        pass  # mid-space: only history can suggest a full-line continuation
    else:
        leading, _, word = text_before.rpartition(" ")
        leading = leading + " " if leading else ""
        for cand in candidates_for(session, leading, word):
            cand = cand.rstrip()
            if cand.startswith(word) and len(cand) > len(word):
                return cand[len(word):]
    for past in reversed(history):  # fish-style: newest matching history line
        if past.startswith(text_before) and len(past) > len(text_before):
            return past[len(text_before):]
    return ""


# --------------------------------------------------------------------------- live context (rprompt + toolbar)

FormattedText = t.List[t.Tuple[str, str]]


def active_endpoint(session: ExploreSession) -> str | None:
    """The endpoint that implicit commands (model/on/param) will affect: the
    endpoint of the MOST RECENT request (not skipping back to older named ones,
    which would silently edit something off-screen). None when the last request
    is unnamed or there are no requests yet."""
    steps = session.history()
    if not steps:
        return None
    return t.cast("str | None", steps[-1].get("matched_endpoint"))


def _last_request(session: ExploreSession) -> dict[str, t.Any] | None:
    steps = session.history()
    return steps[-1] if steps else None


def rprompt_text(session: ExploreSession) -> FormattedText:
    """Right-aligned context on the input line: what implicit commands act on."""
    last = _last_request(session)
    if last is None:
        base = session.base_url
        return [("class:rprompt", f"[{base}]" if base else "[no base_url]")]
    ep = last.get("matched_endpoint")
    if ep:
        steps = sum(1 for s in session.history() if s.get("matched_endpoint") == ep)
        return [
            ("class:rprompt", "active "),
            ("class:rprompt.ep", ep),
            ("class:rprompt", f" · {steps} step{'s' if steps != 1 else ''}"),
        ]
    # last request is unnamed: implicit commands have no target, show it plainly
    return [
        ("class:rprompt", f"{last.get('method')} {last.get('path')} · "),
        ("class:rprompt.warn", "unnamed"),
    ]


def _seg(cls: str, text: str) -> tuple[str, str]:
    return (f"class:{cls}", text)


def _capture_refs(cmd: Command) -> list[str]:
    """Capture names referenced as {{name}} in a request's path / query / headers / body."""
    parts: list[str] = [cmd.path or ""]
    parts.extend(str(v) for v in cmd.query.values())
    parts.extend(str(v) for v in cmd.headers.values())
    if cmd.body:
        parts.append(cmd.body)
    if cmd.body_json is not None:
        parts.append(json.dumps(cmd.body_json))
    names: list[str] = []
    for part in parts:
        for match in _CAPTURE_RE.finditer(part):
            if match.group(1) not in names:
                names.append(match.group(1))
    return names


def _action_desc(action: str) -> str:
    if action == "none":
        return "returns None"
    if action.startswith("raise:"):
        return f"raises {action[len('raise:'):]}"
    return f"returns {action}"


_UNRESOLVED: t.Final = object()


def _short_repr(value: t.Any, limit: int = 60) -> str:
    r = repr(value)
    return r if len(r) <= limit else r[: limit - 3] + "..."


def _resolve_preview(session: ExploreSession, path: str) -> t.Any:
    """A short repr of what <path> resolves to in the last response, or the
    _UNRESOLVED sentinel when it can't be read. Used to preview set/peek live."""
    if not path:
        return ""
    try:
        return _short_repr(session.peek(path))
    except Exception:  # noqa: BLE001 - the toolbar must never break
        return _UNRESOLVED


def describe_impact(session: ExploreSession, line: str) -> FormattedText:
    """Live 'what will this command do' preview for the bottom toolbar. Pure:
    inspects session state, never mutates. Assembled as styled segments."""
    line = line.strip()
    if not line:
        return [_seg("tb.muted", "type a command · Tab lists · → accepts the grey hint · help")]
    try:
        cmd = parse_command(line)
    except ParseError:
        head = line.split()[0].lower()
        hint = USAGE.get({"ls": "list", "ep": "endpoint", "save": "export"}.get(head, head))
        return [_seg("tb.muted", hint or "keep typing…")]

    ep = active_endpoint(session)
    arrow = _seg("tb.muted", " → ")

    def _no_target() -> FormattedText:
        last = _last_request(session)
        if last is None:
            return [_seg("tb.warn", "no request yet: run one first")]
        return [
            _seg("tb.warn", f"{last.get('method')} {last.get('path')} isn't an endpoint yet: "),
            _seg("tb.verb", "run "), _seg("tb.value", "endpoint <name>"), _seg("tb.verb", " first"),
        ]

    if cmd.kind == "request":
        segs = [_seg("tb.verb", "send "), _seg("tb.value", f"{cmd.method} {cmd.path}")]
        match = session._match_endpoint(cmd.method or "", cmd.path or "")  # noqa: SLF001
        if match:
            segs += [_seg("tb.muted", " · matches "), _seg("tb.target", match)]
        captures = session.captures
        for name in _capture_refs(cmd):
            ref = "{{" + name + "}}"
            if name in captures:
                segs += [_seg("tb.muted", f" · {ref}="), _seg("tb.value", str(captures[name]))]
            else:
                segs += [_seg("tb.warn", f" · {ref} not set")]
        return segs
    if cmd.kind in ("set", "peek"):
        verb = "captures" if cmd.kind == "set" else "shows"
        resolved = _resolve_preview(session, cmd.path or "")
        if resolved is _UNRESOLVED:
            return [_seg("tb.warn", f"{cmd.path} not found in the last response")]
        return [
            _seg("tb.verb", f"{verb} "), _seg("tb.muted", cmd.path or ""),
            _seg("tb.muted", " = "), _seg("tb.value", resolved),
        ]
    if cmd.kind == "endpoint":
        last = _last_request(session)
        if cmd.name in session.endpoints():
            return [_seg("tb.verb", "folds the last request into "), _seg("tb.target", cmd.name or "")]
        if last is None:
            return [_seg("tb.warn", "run a request first (nothing to name)")]
        return [
            _seg("tb.verb", "creates endpoint "), _seg("tb.target", cmd.name or ""),
            _seg("tb.muted", f" from {last.get('method')} {last.get('path')}"),
        ]
    if cmd.kind == "model":
        if ep is None:
            return _no_target()
        which = "request-body" if (cmd.name or "").endswith("!request") else "response"
        plain = (cmd.name or "").removesuffix("!request")
        return [
            _seg("tb.verb", f"names the {which} model of "), _seg("tb.target", ep),
            arrow, _seg("tb.value", plain),
        ]
    if cmd.kind == "on":
        if ep is None:
            return _no_target()
        return [
            _seg("tb.target", ep), _seg("tb.verb", f": status {cmd.status} "),
            arrow, _seg("tb.value", _action_desc(cmd.action or "")),
        ]
    if cmd.kind == "param":
        if ep is None:
            return _no_target()
        return [
            _seg("tb.target", ep), _seg("tb.verb", f": rename param {cmd.index} "),
            arrow, _seg("tb.value", "{" + (cmd.name or "") + "}"),
        ]
    if cmd.kind == "rename":
        exists = (cmd.name in session.endpoints()) if cmd.target == "endpoint" else True
        segs = [
            _seg("tb.verb", f"renames {cmd.target} "), _seg("tb.target", cmd.name or ""),
            arrow, _seg("tb.value", cmd.value or ""),
        ]
        if cmd.target == "endpoint" and not exists:
            return [_seg("tb.warn", f"no endpoint named '{cmd.name}'")]
        return segs
    if cmd.kind == "drop":
        if cmd.target == "endpoint":
            ep = session.endpoints().get(cmd.name or "")
            if ep is None:
                return [_seg("tb.warn", f"no endpoint named '{cmd.name}'")]
            n = ep["steps"]
            return [
                _seg("tb.verb", "removes endpoint "), _seg("tb.target", cmd.name or ""),
                _seg("tb.muted", f" ({ep['template']}) · frees {n} step{'' if n == 1 else 's'} (kept in history)"),
            ]
        step = next((s for s in session.history() if s["step_id"] == cmd.index), None)
        if step is None:
            return [_seg("tb.warn", f"no step with id {cmd.index}")]
        where = step["matched_endpoint"] or "unnamed"
        return [
            _seg("tb.verb", "removes step "), _seg("tb.value", str(cmd.index)),
            _seg("tb.muted", " · "), _seg("tb.target", f"{step['method']} {step['path']}"),
            _seg("tb.muted", f" · {where}"),
        ]
    if cmd.kind in ("retry", "throttle", "timeout", "auth", "header", "base"):
        detail = cmd.spec or cmd.value or (f"{cmd.name}={cmd.value}" if cmd.name else "")
        label = {"base": "base_url"}.get(cmd.kind, cmd.kind)
        return [_seg("tb.verb", f"sets {label} "), arrow, _seg("tb.value", str(detail))]
    if cmd.kind == "export":
        n_ep = len(session.endpoints())
        extra = " + tests + cassette" if cmd.tests else ""
        return [
            _seg("tb.verb", "writes "), _seg("tb.value", cmd.path or "the client"),
            _seg("tb.verb", extra), _seg("tb.muted", f" · {n_ep} endpoint{'s' if n_ep != 1 else ''}"),
        ]
    if cmd.kind == "show":
        return [_seg("tb.verb", f"shows {cmd.target}")]
    descriptions = {"undo": "undoes the last change", "help": "lists commands", "quit": "leaves the explorer"}
    return [_seg("tb.muted", descriptions.get(cmd.kind, cmd.kind))]


def _build_pt_session(session: ExploreSession) -> t.Any:
    """A prompt_toolkit PromptSession with inline ghost-text suggestions + Tab
    completion, or None when prompt_toolkit is not installed."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    class _GhostSuggest(AutoSuggest):
        def get_suggestion(self, buffer: t.Any, document: t.Any) -> t.Any:
            past = [s for s in buffer.history.get_strings()] if buffer.history else []
            suffix = suggest_suffix(session, document.text_before_cursor, past)
            return Suggestion(suffix) if suffix else None

    class _PTCompleter(Completer):
        def get_completions(self, document: t.Any, complete_event: t.Any) -> t.Iterator[t.Any]:
            leading, _, word = document.text_before_cursor.rpartition(" ")
            leading = leading + " " if leading else ""
            for cand in candidates_for(session, leading, word):
                yield Completion(cand.rstrip(), start_position=-len(word))

    # ANSI colors so it adapts to the user's light/dark terminal theme.
    style = Style.from_dict({
        "prompt": "bold",
        "rprompt": "fg:ansibrightblack",
        "rprompt.ep": "fg:ansicyan bold",
        "rprompt.warn": "fg:ansiyellow",
        "bottom-toolbar": "noreverse fg:ansibrightblack",  # a calm status line, not a reversed bar
        "tb.muted": "fg:ansibrightblack italic",
        "tb.verb": "fg:ansidefault",
        "tb.target": "fg:ansicyan bold",
        "tb.value": "fg:ansigreen",
        "tb.warn": "fg:ansiyellow",
    })

    def _toolbar() -> FormattedText:
        try:
            from prompt_toolkit.application import get_app

            text = get_app().current_buffer.text
            return [_seg("tb.muted", "↳ "), *describe_impact(session, text)]
        except Exception:  # noqa: BLE001 - the toolbar must never break the prompt
            return []

    def _rprompt() -> FormattedText:
        try:
            return rprompt_text(session)
        except Exception:  # noqa: BLE001
            return []

    try:
        return PromptSession(
            message=[("class:prompt", PROMPT)],
            history=FileHistory(str(HISTORY_FILE)),
            auto_suggest=_GhostSuggest(),
            completer=_PTCompleter(),
            complete_while_typing=False,  # Tab to open the menu; ghost text shows inline
            rprompt=_rprompt,
            bottom_toolbar=_toolbar,
            style=style,
        )
    except Exception:  # noqa: BLE001 - fall back to readline if the terminal rejects it
        return None


async def run_repl(session: ExploreSession) -> int:
    """The `gracy explore` loop; errors are printed, never fatal. Returns exit code."""
    console = _make_console()
    is_tty = sys.stdin.isatty()
    pt = _build_pt_session(session) if is_tty else None
    if is_tty and pt is None:
        _setup_readline(session)  # ghost text needs prompt_toolkit; readline still does Tab

    console.print(f"[bold]gracy explorer[/bold] - session [cyan]{session.session_path}[/cyan]")
    base = session.base_url or "(not set - `base <url>`)"
    hint = "type it or Tab-complete" if pt is None else "type it, → accepts the grey suggestion, Tab lists"
    console.print(f"base_url: [cyan]{base}[/cyan] · [bold]help[/bold] for commands ({hint}) · Ctrl-D to leave")

    while True:
        try:
            if pt is not None:
                line = await pt.prompt_async()
            else:
                line = input(PROMPT) if is_tty else input()
        except EOFError:
            break
        except KeyboardInterrupt:
            console.print()
            continue
        line = line.strip()
        if not line:
            continue
        try:
            cmd = parse_command(line)
        except ParseError as exc:
            console.print(f"[red]{exc}[/red]")
            continue
        if cmd.kind == "quit":
            break
        try:
            outcome = await execute_command(session, cmd)
        except Exception as exc:  # noqa: BLE001 - the REPL always survives
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
            continue
        render_outcome(console, outcome)

    await session.aclose()
    return 0
