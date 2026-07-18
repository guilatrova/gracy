"""The `gracy explore` REPL and the shared command dispatcher (`gracy x` uses it too).

The module imports only the stdlib; rich is pulled in lazily inside the render
helpers so one-shot ``gracy x --json`` works without any optional dependency.
"""

from __future__ import annotations

import sys
import typing as t

from gracy.explore._commands import Outcome, execute_command
from gracy.explore._completion import (  # noqa: F401 - re-exported for compatibility
    COMMANDS,
    HISTORY_FILE,
    FormattedText,
    _Completer,
    _seg,
    _setup_readline,
    active_endpoint,
    candidates_for,
    describe_impact,
    rprompt_text,
    suggest_suffix,
)
from gracy.explore._parser import ParseError, parse_command
from gracy.explore._render import _make_console, render_outcome
from gracy.explore._session import ExploreSession, StepResult  # noqa: F401 - re-exported for compatibility

__all__ = ["Outcome", "execute_command", "run_repl"]

PROMPT: t.Final = "gracy› "


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
