"""Rendering helpers for the explore REPL (rich is imported lazily)."""

from __future__ import annotations

import typing as t

from gracy.explore._commands import Outcome
from gracy.explore._session import StepResult


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
