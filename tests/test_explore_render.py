"""Regression tests for `render_outcome` - the rich render path of `gracy explore`.

These render every `Outcome` kind through a real `rich.console.Console` so a
missing lazy import (e.g. `Pretty` used outside the one helper that imported it)
fails loudly here instead of only at runtime in front of the user. The rest of
the explore suite drives `execute_command` and asserts on `Outcome` data, which
never touches the rich render helpers - that gap let a `NameError` ship.
"""

from __future__ import annotations

import io
import typing as t
from pathlib import Path

import pytest

from gracy.explore import ExploreSession
from gracy.explore._parser import parse_command
from gracy.explore.repl import Outcome, StepResult, execute_command, render_outcome


def _render(outcome: Outcome) -> str:
    """Render an Outcome through a real (non-tty, plain) rich Console -> text."""
    from rich.console import Console

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100, no_color=True)
    render_outcome(console, outcome)
    return buffer.getvalue()


def _step(**overrides: t.Any) -> StepResult:
    base: dict[str, t.Any] = dict(
        step_id=0,
        method="GET",
        url="https://pokeapi.co/api/v2/pokemon/pikachu",
        path="/pokemon/pikachu",
        status=200,
        elapsed_ms=12.3,
        body_preview={"name": "pikachu", "weight": 60},
        ok=True,
        error=None,
        matched_endpoint=None,
        template_proposal=None,
    )
    base.update(overrides)
    return StepResult(**base)


# --------------------------------------------------------------------------- peek (the regression)


def test_render_peek_does_not_raise_and_shows_value() -> None:
    """The exact bug: `peek` renders via `Pretty` in a branch that never imported it."""
    outcome = Outcome(
        "peek",
        {"ok": True, "path": "abilities", "value": ["static", "lightning-rod"]},
        "abilities = ['static', 'lightning-rod']",
    )
    out = _render(outcome)
    assert "abilities =" in out
    assert "static" in out
    assert "lightning-rod" in out


def test_render_peek_scalar_value() -> None:
    outcome = Outcome("peek", {"ok": True, "path": "weight", "value": 60}, "weight = 60")
    out = _render(outcome)
    assert "weight =" in out
    assert "60" in out


def test_render_peek_nested_dict_value() -> None:
    value = {"slot": 1, "type": {"name": "electric"}}
    outcome = Outcome("peek", {"ok": True, "path": "types[0]", "value": value}, repr(value))
    out = _render(outcome)
    assert "types[0] =" in out
    assert "electric" in out


# --------------------------------------------------------------------------- the other render branches


def test_render_step_with_body_preview() -> None:
    outcome = Outcome("request", {}, "GET ... -> 200", step=_step())
    out = _render(outcome)
    assert "GET" in out
    assert "200" in out
    assert "pikachu" in out


def test_render_step_error_branch() -> None:
    step = _step(status=None, ok=False, error="connection refused", body_preview=None)
    outcome = Outcome("request", {}, "error", step=step)
    out = _render(outcome)
    assert "connection refused" in out


def test_render_panel_branch() -> None:
    outcome = Outcome(
        "show_model",
        {"ok": True, "source": "class Pokemon(TypedDict): ..."},
        "class Pokemon(TypedDict): ...",
        panel=("Pokemon", "class Pokemon(TypedDict):\n    name: str\n"),
    )
    out = _render(outcome)
    assert "Pokemon" in out


def test_render_plain_human_branch() -> None:
    outcome = Outcome("help", {"ok": True}, "this is the help text")
    out = _render(outcome)
    assert "this is the help text" in out


def test_render_hints_are_printed() -> None:
    outcome = Outcome("peek", {"ok": True, "path": "x", "value": 1}, "x = 1", hints=["try `set foo x`"])
    out = _render(outcome)
    assert "try `set foo x`" in out


# --------------------------------------------------------------------------- end-to-end (parse -> execute -> render)


@pytest.mark.asyncio
async def test_peek_end_to_end_parse_execute_render(tmp_path: Path) -> None:
    """Drive the real path: a stored step -> `peek abilities` -> rendered, no raise."""
    session = ExploreSession(tmp_path / "s.json")
    session._data["steps"] = [
        {
            "step_id": 0,
            "method": "GET",
            "path": "/pokemon/pikachu",
            "response_json": {"name": "pikachu", "abilities": ["static"], "weight": 60},
        }
    ]

    outcome = await execute_command(session, parse_command("peek abilities"))
    assert outcome.kind == "peek"
    assert outcome.data["value"] == ["static"]

    out = _render(outcome)
    assert "abilities =" in out
    assert "static" in out
