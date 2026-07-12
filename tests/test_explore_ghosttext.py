"""Inline ghost-text suggestions (prompt_toolkit) for the `gracy explore` REPL."""

from __future__ import annotations

from pathlib import Path

import pytest

from gracy.explore import ExploreSession
from gracy.explore.repl import suggest_suffix


@pytest.fixture
def session(tmp_path: Path) -> ExploreSession:
    s = ExploreSession(tmp_path / "gt.json")
    s._data["base_url"] = "https://x"
    s._data["steps"] = [
        {"id": 1, "method": "GET", "path": "/pokemon/pikachu", "endpoint": "get_pokemon", "status": 200, "response_json": {}},
        {"id": 2, "method": "GET", "path": "/berry/cheri", "endpoint": None, "status": 200, "response_json": {}},
    ]
    s._data["endpoints"] = {
        "get_pokemon": {
            "method": "GET", "template": "/pokemon/{name}", "params": [],
            "on": {}, "response_model": "Pokemon", "request_model": None,
        }
    }
    return s


def test_ghost_completes_command_word(session: ExploreSession) -> None:
    assert suggest_suffix(session, "g") == "et"  # the requested behavior
    assert suggest_suffix(session, "ge") == "t"
    assert suggest_suffix(session, "exp") == "ort"


def test_ghost_completes_paths_and_endpoints(session: ExploreSession) -> None:
    assert suggest_suffix(session, "get /pok") == "emon/pikachu"
    assert suggest_suffix(session, "endpoint get") == "_pokemon"
    assert suggest_suffix(session, "on 404 no") == "ne"
    assert suggest_suffix(session, "auth be") == "arer"


def test_ghost_empty_when_no_match(session: ExploreSession) -> None:
    assert suggest_suffix(session, "xyz") == ""
    assert suggest_suffix(session, "get /zzz") == ""


def test_ghost_falls_back_to_history(session: ExploreSession) -> None:
    # no completion candidate for a full path, but a past line matches
    hist = ["get /berry/cheri", "export out.py --tests"]
    assert suggest_suffix(session, "export out.py --te", hist) == "sts"
    # history only kicks in when no word-completion wins; newest-first
    assert suggest_suffix(session, "get /berry/cher", hist) == "i"


def test_ghost_nothing_after_trailing_space_without_history(session: ExploreSession) -> None:
    assert suggest_suffix(session, "get ") == ""  # mid-space, no history
    assert suggest_suffix(session, "") == ""


def test_prompt_toolkit_session_builds_with_adapters(session: ExploreSession) -> None:
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.auto_suggest import AutoSuggest
    from prompt_toolkit.completion import Completer

    from gracy.explore.repl import _build_pt_session

    ps = _build_pt_session(session)
    assert ps is not None
    assert isinstance(ps.auto_suggest, AutoSuggest)
    assert isinstance(ps.completer, Completer)


def test_ghost_degrades_when_prompt_toolkit_missing(session: ExploreSession, monkeypatch: pytest.MonkeyPatch) -> None:
    # simulate prompt_toolkit not installed: _build_pt_session returns None,
    # the REPL then uses the readline path (still has Tab completion).
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *a: object, **k: object) -> object:
        if name == "prompt_toolkit" or name.startswith("prompt_toolkit."):
            raise ImportError("simulated missing prompt_toolkit")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from gracy.explore.repl import _build_pt_session

    assert _build_pt_session(session) is None
