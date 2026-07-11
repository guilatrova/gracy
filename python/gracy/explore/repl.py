"""The `gracy explore` REPL and the shared command dispatcher (`gracy x` uses it too).

The module imports only the stdlib; rich is pulled in lazily inside the render
helpers so one-shot ``gracy x --json`` works without any optional dependency.
"""

from __future__ import annotations

import sys
import typing as t
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gracy.explore._parser import HELP_TEXT, Command, ParseError, parse_command
from gracy.explore._session import ExploreSession, StepResult, split_segments

__all__ = ["Outcome", "execute_command", "run_repl"]

HISTORY_FILE: t.Final = Path.home() / ".gracy_history"
PROMPT: t.Final = "gracy› "


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
            f"✨ one segment differs from an existing endpoint — template proposal: "
            f"{result.template_proposal} (run `name <endpoint>` to fold it in)"
        )
    hints.extend(f"✨ {line}" for line in result.model_drift)
    if result.matched_endpoint is None and result.template_proposal is None and result.error is None:
        hints.append(f"✨ tip: `name {_suggest_name(result.path)}` to save this request as an endpoint")

    status = str(result.status) if result.status is not None else "ERROR"
    human = f"{result.method} {result.url} -> {status} ({result.elapsed_ms:.1f} ms)"
    if result.error:
        human += f" — {result.error}"
    return Outcome("request", asdict(result), human, step=result, hints=hints)


def _suggest_name(path: str) -> str:
    from gracy.explore._infer import pascal

    segments = split_segments(path)
    return pascal(segments[0]) if segments else "Endpoint"


async def _do_name(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.name is not None
    template = session.name_endpoint(cmd.name)
    hints: list[str] = []
    params = session.endpoints()[cmd.name]["params"]
    if params:
        listed = ", ".join(f"{{{p['name']}}} (segment {p['index']})" for p in params)
        hints.append(f"✨ params: {listed} — rename with `param <index> as <name>`")
    return Outcome(
        "name",
        {"ok": True, "endpoint": cmd.name, "template": template},
        f"endpoint '{cmd.name}': {template}",
        hints=hints,
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
            raise ValueError("no steps yet — run a request first")
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
    raise ValueError(f"unknown show target {cmd.target!r}")


async def _do_undo(session: ExploreSession, cmd: Command) -> Outcome:
    line = session.undo()
    return Outcome("undo", {"ok": True, "undo": line}, line)


async def _do_save(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.path is not None
    paths = session.save_code(cmd.path, tests=cmd.tests)
    written = [str(p) for p in paths]
    return Outcome("save", {"ok": True, "written": written}, "wrote " + ", ".join(written))


async def _do_help(session: ExploreSession, cmd: Command) -> Outcome:
    return Outcome("help", {"ok": True, "help": HELP_TEXT}, HELP_TEXT)


async def _do_quit(session: ExploreSession, cmd: Command) -> Outcome:
    return Outcome("quit", {"ok": True}, "bye")


_HANDLERS: t.Final[dict[str, t.Callable[[ExploreSession, Command], t.Awaitable[Outcome]]]] = {
    "request": _do_request,
    "name": _do_name,
    "model": _do_model,
    "on": _do_on,
    "param": _do_param,
    "retry": _do_policy,
    "throttle": _do_policy,
    "timeout": _do_policy,
    "auth": _do_policy,
    "header": _do_policy,
    "base": _do_policy,
    "show": _do_show,
    "undo": _do_undo,
    "save": _do_save,
    "help": _do_help,
    "quit": _do_quit,
}


# --------------------------------------------------------------------------- rendering (rich, lazy)


def _make_console() -> t.Any:
    try:
        from rich.console import Console
    except ImportError:  # pragma: no cover - venvs in this repo have rich
        raise RuntimeError(
            "gracy explore needs the optional 'rich' package — install it with: pip install 'gracy[rich]'"
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
    else:
        console.print(outcome.human)
    for hint in outcome.hints:
        console.print(f"[magenta]{hint}[/magenta]")


# --------------------------------------------------------------------------- the loop


def _setup_readline() -> None:
    try:
        import atexit
        import readline

        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
        readline.set_history_length(1000)
        atexit.register(lambda: _write_history(readline))
    except Exception:  # noqa: BLE001 - readline is best-effort (absent on some builds)
        pass


def _write_history(readline: t.Any) -> None:
    try:
        readline.write_history_file(str(HISTORY_FILE))
    except OSError:
        pass


async def run_repl(session: ExploreSession) -> int:
    """The `gracy explore` loop; errors are printed, never fatal. Returns exit code."""
    console = _make_console()
    is_tty = sys.stdin.isatty()
    if is_tty:
        _setup_readline()

    console.print(f"[bold]gracy explorer[/bold] — session [cyan]{session.session_path}[/cyan]")
    base = session.base_url or "(not set — `base <url>`)"
    console.print(f"base_url: [cyan]{base}[/cyan] · type [bold]help[/bold] for commands · Ctrl-D or quit to leave")

    while True:
        try:
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
