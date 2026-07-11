"""Live context helpers: active endpoint (rprompt) + command impact preview (toolbar)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gracy.explore import ExploreSession
from gracy.explore.repl import active_endpoint, describe_impact, rprompt_text


def _plain(fmt: list) -> str:
    return "".join(text for _, text in fmt)


def _classes(fmt: list) -> list[str]:
    return [cls for cls, _ in fmt]


@pytest.fixture
def session(tmp_path: Path) -> ExploreSession:
    s = ExploreSession(tmp_path / "ctx.json")
    s._data["base_url"] = "https://pokeapi.co/api/v2"
    # two endpoints; the LAST request touched 'berries' -> that's the active target
    s._data["steps"] = [
        {"id": 1, "method": "GET", "path": "/pokemon/pikachu", "endpoint": "get_pokemon", "status": 200},
        {"id": 2, "method": "GET", "path": "/berry", "endpoint": "berries", "status": 200},
    ]
    s._data["endpoints"] = {
        "get_pokemon": {"method": "GET", "template": "/pokemon/{name}", "params": [],
                        "on": {}, "response_model": "Pokemon", "request_model": None},
        "berries": {"method": "GET", "template": "/berry", "params": [],
                    "on": {}, "response_model": None, "request_model": None},
    }
    return s


# --------------------------------------------------------------------------- active endpoint / rprompt


def test_active_endpoint_is_last_requests_endpoint(session: ExploreSession) -> None:
    assert active_endpoint(session) == "berries"


def test_active_endpoint_none_until_a_request(tmp_path: Path) -> None:
    s = ExploreSession(tmp_path / "empty.json", base_url="https://x")
    assert active_endpoint(s) is None


def test_rprompt_shows_active_endpoint_and_steps(session: ExploreSession) -> None:
    assert _plain(rprompt_text(session)) == "active berries · 1 step"
    assert "class:rprompt.ep" in _classes(rprompt_text(session))  # endpoint gets its own (cyan) style


def test_rprompt_falls_back_to_base_url(tmp_path: Path) -> None:
    s = ExploreSession(tmp_path / "e.json", base_url="https://x")
    assert _plain(rprompt_text(s)) == "[https://x]"


# --------------------------------------------------------------------------- impact preview


def test_impact_model_names_the_active_endpoint(session: ExploreSession) -> None:
    # THE motivating bug: which endpoint gets the model? Now it's explicit.
    got = describe_impact(session, "model Pokemon")
    assert _plain(got) == "names the response model of berries → Pokemon"
    assert "class:tb.target" in _classes(got)  # 'berries' highlighted


def test_impact_model_request_suffix(session: ExploreSession) -> None:
    assert _plain(describe_impact(session, "model Foo!request")) == "names the request-body model of berries → Foo"


def test_impact_on_and_param(session: ExploreSession) -> None:
    assert _plain(describe_impact(session, "on 404 none")) == "berries: status 404  → returns None"
    assert _plain(describe_impact(session, "on 500 raise:Boom")) == "berries: status 500  → raises Boom"
    assert _plain(describe_impact(session, "param 1 as name")) == "berries: rename param 1  → {name}"


def test_impact_warns_when_no_active_endpoint(tmp_path: Path) -> None:
    s = ExploreSession(tmp_path / "e.json", base_url="https://x")
    s._data["steps"] = [{"id": 1, "method": "GET", "path": "/x", "endpoint": None, "status": 200}]
    got = describe_impact(s, "model Thing")
    assert _plain(got) == "no active endpoint: run a request first"
    assert _classes(got) == ["class:tb.warn"]


def test_impact_endpoint_create_vs_fold(session: ExploreSession) -> None:
    assert _plain(describe_impact(session, "endpoint berries")) == "folds the last request into berries"
    assert _plain(describe_impact(session, "endpoint Fresh")) == "creates endpoint Fresh from GET /berry"


def test_impact_request_matches_existing(session: ExploreSession) -> None:
    assert _plain(describe_impact(session, "get /pokemon/mew")) == "send GET /pokemon/mew · matches get_pokemon"


def test_impact_save_reports_files_and_counts(session: ExploreSession) -> None:
    assert _plain(describe_impact(session, "save api.py --tests")) == "writes api.py + tests + cassette · 2 endpoints"
    assert _plain(describe_impact(session, "save api.py")) == "writes api.py · 2 endpoints"


def test_impact_rename_and_policies(session: ExploreSession) -> None:
    assert _plain(describe_impact(session, "rename endpoint berries fruit")) == "renames endpoint berries → fruit"
    assert _plain(describe_impact(session, "rename endpoint ghost x")) == "no endpoint named 'ghost'"
    assert _plain(describe_impact(session, "throttle 5/1s")) == "sets throttle  → 5/1s"


def test_impact_empty_and_partial(session: ExploreSession) -> None:
    assert "type a command" in _plain(describe_impact(session, ""))
    # a half-typed command shows its grammar, not a scary error
    assert _plain(describe_impact(session, "model")).startswith("model <Name>")


def test_impact_never_has_em_dash(session: ExploreSession) -> None:
    for line in ["model X", "on 404 none", "no such", "save a.py", "endpoint E", "rename endpoint a b"]:
        assert "—" not in _plain(describe_impact(session, line))
