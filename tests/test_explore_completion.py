"""Tab-completion candidates for the `gracy explore` REPL (context-aware)."""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest

from gracy.explore import ExploreSession
from gracy.explore.repl import COMMANDS, _Completer


@pytest.fixture
def completer(tmp_path: Path) -> _Completer:
    s = ExploreSession(tmp_path / "ac.json")
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
    return _Completer(s)


def _cands(monkeypatch: pytest.MonkeyPatch, completer: _Completer, buffer: str, text: str) -> list[str]:
    import readline

    begidx = len(buffer) - len(text)
    monkeypatch.setattr(readline, "get_line_buffer", lambda: buffer, raising=False)
    monkeypatch.setattr(readline, "get_begidx", lambda: begidx, raising=False)
    return completer._candidates(text)


def test_first_word_completes_commands(monkeypatch: pytest.MonkeyPatch, completer: _Completer) -> None:
    got = _cands(monkeypatch, completer, "", "")
    assert [c.strip() for c in got] == list(COMMANDS)
    assert _cands(monkeypatch, completer, "ge", "ge") == ["get "]
    assert _cands(monkeypatch, completer, "sav", "sav") == ["save "]


def test_method_completes_seen_paths(monkeypatch: pytest.MonkeyPatch, completer: _Completer) -> None:
    assert _cands(monkeypatch, completer, "get /pok", "/pok") == ["/pokemon/pikachu"]
    assert _cands(monkeypatch, completer, "get /", "/") == ["/berry/cheri", "/pokemon/pikachu"]


def test_show_completes_targets_then_models(monkeypatch: pytest.MonkeyPatch, completer: _Completer) -> None:
    assert [c.strip() for c in _cands(monkeypatch, completer, "show ", "")] == [
        "last", "model", "class", "endpoints", "history", "captures"
    ]
    assert _cands(monkeypatch, completer, "show model ", "") == ["Pokemon"]


def test_name_completes_existing_endpoints(monkeypatch: pytest.MonkeyPatch, completer: _Completer) -> None:
    assert _cands(monkeypatch, completer, "endpoint get", "get") == ["get_pokemon"]


def test_on_completes_actions(monkeypatch: pytest.MonkeyPatch, completer: _Completer) -> None:
    assert _cands(monkeypatch, completer, "on 404 ", "") == ["none", "raise:"]
    assert _cands(monkeypatch, completer, "on 404 r", "r") == ["raise:"]


def test_auth_completes_schemes(monkeypatch: pytest.MonkeyPatch, completer: _Completer) -> None:
    assert [c.strip() for c in _cands(monkeypatch, completer, "auth ", "")] == ["bearer", "basic"]


def test_save_completes_files_and_tests_flag(
    monkeypatch: pytest.MonkeyPatch, completer: _Completer, tmp_path: Path
) -> None:
    (tmp_path / "myapi.py").write_text("")
    (tmp_path / "notes.txt").write_text("")
    files = _cands(monkeypatch, completer, f"save {tmp_path}/", f"{tmp_path}/")
    assert any(f.endswith("myapi.py") for f in files)
    assert not any(f.endswith("notes.txt") for f in files)  # only .py (or dirs) offered
    assert _cands(monkeypatch, completer, "save x --t", "--t") == ["--tests"]


def test_complete_state_protocol(monkeypatch: pytest.MonkeyPatch, completer: _Completer) -> None:
    import readline

    monkeypatch.setattr(readline, "get_line_buffer", lambda: "sh", raising=False)
    monkeypatch.setattr(readline, "get_begidx", lambda: 0, raising=False)
    assert completer.complete("sh", 0) == "show "
    assert completer.complete("sh", 1) is None  # readline stops when None
