"""The `endpoint` / `rename` / `list` command verbs and create-vs-fold echo."""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest

from gracy.explore import ExploreSession
from gracy.explore._parser import ParseError, parse_command
from gracy.explore.repl import execute_command


@pytest.fixture
async def make_session(tmp_path: Path, test_server: str) -> t.AsyncIterator[t.Callable[..., ExploreSession]]:
    sessions: list[ExploreSession] = []

    def factory(name: str = "s.json") -> ExploreSession:
        s = ExploreSession(tmp_path / name, base_url=test_server)
        sessions.append(s)
        return s

    yield factory
    for s in sessions:
        await s.aclose()


# --------------------------------------------------------------------------- parser


def test_parse_endpoint_replaces_name() -> None:
    cmd = parse_command("endpoint get_pokemon")
    assert cmd.kind == "endpoint" and cmd.name == "get_pokemon"
    with pytest.raises(ParseError):  # the old verb is gone
        parse_command("name get_pokemon")


def test_parse_ep_is_alias_of_endpoint() -> None:
    cmd = parse_command("ep get_pokemon")
    assert cmd.kind == "endpoint" and cmd.name == "get_pokemon"
    # same arity rules as the long form
    for bad in ("ep", "ep a b"):
        with pytest.raises(ParseError):
            parse_command(bad)


def test_parse_rename() -> None:
    c = parse_command("rename endpoint old new")
    assert (c.kind, c.target, c.name, c.value) == ("rename", "endpoint", "old", "new")
    c2 = parse_command("rename model Pokemon PokemonDetail")
    assert (c2.target, c2.name, c2.value) == ("model", "Pokemon", "PokemonDetail")
    for bad in ("rename endpoint only-two", "rename widget a b", "rename endpoint a b c"):
        with pytest.raises(ParseError):
            parse_command(bad)


def test_parse_drop() -> None:
    ep = parse_command("drop endpoint pikachu")
    assert (ep.kind, ep.target, ep.name) == ("drop", "endpoint", "pikachu")
    st = parse_command("drop step 3")
    assert (st.kind, st.target, st.index) == ("drop", "step", 3)
    for bad in ("drop", "drop endpoint", "drop widget x", "drop step abc", "drop step"):
        with pytest.raises(ParseError):
            parse_command(bad)


def test_parse_list_and_ls_alias_show_endpoints() -> None:
    for text in ("list", "ls"):
        cmd = parse_command(text)
        assert cmd.kind == "show" and cmd.target == "endpoints"


# --------------------------------------------------------------------------- echo: create vs fold


async def test_endpoint_echo_created_then_folded(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/one")
    created = await execute_command(session, parse_command("endpoint get_echo"))
    assert created.data["created"] is True
    assert "created endpoint" in created.human and "get_echo" in created.human

    await session.execute("get", "/echo/two")
    folded = await execute_command(session, parse_command("endpoint get_echo"))
    assert folded.data["created"] is False
    assert "folded into" in folded.human
    assert folded.data["template"] == "/echo/{echo}"


async def test_ep_alias_creates_and_folds_like_endpoint(
    make_session: t.Callable[..., ExploreSession],
) -> None:
    session = make_session()
    await session.execute("get", "/echo/one")
    created = await execute_command(session, parse_command("ep get_echo"))
    assert created.data["created"] is True and "get_echo" in session.endpoints()

    await session.execute("get", "/echo/two")
    folded = await execute_command(session, parse_command("ep get_echo"))
    assert folded.data["created"] is False
    assert folded.data["template"] == "/echo/{echo}"


async def test_ep_completion_and_toolbar(make_session: t.Callable[..., ExploreSession]) -> None:
    from gracy.explore.repl import COMMANDS, candidates_for, describe_impact

    assert "ep" in COMMANDS  # Tab surfaces the alias at the top level
    session = make_session()
    await session.execute("get", "/echo/one")
    session.name_endpoint("get_echo")
    # `ep <TAB>` offers existing endpoint names to fold into, same as `endpoint`
    assert candidates_for(session, "ep ", "get") == ["get_echo"]
    # the impact toolbar reads the alias through the parser (kind == endpoint)
    plain = "".join(seg[1] for seg in describe_impact(session, "ep NewThing"))
    assert "creates endpoint" in plain and "NewThing" in plain


async def test_echo_has_no_rich_markup(make_session: t.Callable[..., ExploreSession]) -> None:
    # human text is plain (gracy x prints it raw); markup would leak as literal text
    session = make_session()
    await session.execute("get", "/echo/x")
    out = await execute_command(session, parse_command("endpoint e"))
    assert "[bold]" not in out.human and "[/bold]" not in out.human


# --------------------------------------------------------------------------- rename


async def test_rename_endpoint(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    out = await execute_command(session, parse_command("rename endpoint get_echo fetch_echo"))
    assert out.data == {"ok": True, "target": "endpoint", "old": "get_echo", "new": "fetch_echo"}
    assert "fetch_echo" in session.endpoints() and "get_echo" not in session.endpoints()
    # steps re-pointed to the new name
    assert all(s.get("endpoint") in (None, "fetch_echo") for s in session.history())


async def test_rename_endpoint_errors(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    with pytest.raises(ValueError, match="no endpoint named"):
        session.rename_endpoint("nope", "x")
    await session.execute("get", "/berry/cheri")
    session.name_endpoint("get_berry")
    with pytest.raises(ValueError, match="already exists"):
        session.rename_endpoint("get_echo", "get_berry")


async def test_rename_model(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    session.set_model_name("EchoResponse")
    out = await execute_command(session, parse_command("rename model EchoResponse Echo"))
    assert out.data["new"] == "Echo"
    assert session.endpoints()["get_echo"]["response_model"] == "Echo"
    with pytest.raises(ValueError, match="no model named"):
        session.rename_model("Ghost", "X")


# --------------------------------------------------------------------------- drop


async def test_drop_endpoint_removes_it_and_frees_steps(
    make_session: t.Callable[..., ExploreSession],
) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    await session.execute("get", "/berry/cheri")
    session.name_endpoint("get_berry")

    out = await execute_command(session, parse_command("drop endpoint get_echo"))
    assert out.data == {"ok": True, "target": "endpoint", "name": "get_echo", "freed": 1}
    assert "get_echo" not in session.endpoints() and "get_berry" in session.endpoints()
    # the step survives in history but is no longer named
    echo_step = next(s for s in session.history() if s["path"] == "/echo/mew")
    assert echo_step["matched_endpoint"] is None
    with pytest.raises(ValueError, match="no endpoint named"):
        session.drop_endpoint("get_echo")


async def test_drop_redundant_overlapping_endpoint(
    make_session: t.Callable[..., ExploreSession],
) -> None:
    """The reported scenario: a separately-named endpoint whose literal path is
    subsumed by another's template (its own distinct step) can be dropped,
    leaving the templated one, which is untouched."""
    session = make_session()
    await session.execute("get", "/echo/mew")  # step 1
    await session.execute("get", "/echo/ditto")  # step 2
    session.name_endpoint("many", 1)
    assert session.name_endpoint("many", 2) == "/echo/{echo}"  # templates
    await session.execute("get", "/echo/pika")  # step 3, its own distinct step
    session.name_endpoint("just_pika", 3)  # a redundant literal endpoint that overlaps

    await execute_command(session, parse_command("drop endpoint just_pika"))
    assert list(session.endpoints()) == ["many"]
    assert session.endpoints()["many"]["template"] == "/echo/{echo}"
    assert session.endpoints()["many"]["steps"] == 2  # the templated one is untouched


async def test_drop_step_re_templates_endpoint(
    make_session: t.Callable[..., ExploreSession],
) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")  # step 1
    await session.execute("get", "/echo/ditto")  # step 2
    session.name_endpoint("get_echo", 1)
    session.name_endpoint("get_echo", 2)
    assert session.endpoints()["get_echo"]["steps"] == 2

    out = await execute_command(session, parse_command("drop step 2"))
    assert out.data["target"] == "step" and out.data["endpoint"] == "get_echo"
    assert session.endpoints()["get_echo"]["steps"] == 1
    assert not any(s["step_id"] == 2 for s in session.history())
    with pytest.raises(ValueError, match="no step with id"):
        session.drop_step(999)


async def test_drop_is_undoable(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    await execute_command(session, parse_command("drop endpoint get_echo"))
    assert "get_echo" not in session.endpoints()
    session.undo()
    assert "get_echo" in session.endpoints()


async def test_drop_impact_and_completion(make_session: t.Callable[..., ExploreSession]) -> None:
    from gracy.explore.repl import candidates_for, describe_impact

    def _tb(line: str) -> str:
        return "".join(seg[1] for seg in describe_impact(session, line))

    session = make_session()
    await session.execute("get", "/echo/mew")  # step 1, named
    session.name_endpoint("get_echo")
    await session.execute("get", "/echo/orphan")  # step 2, unnamed

    # endpoint preview shows the template + freed-step count
    ep = _tb("drop endpoint get_echo")
    assert "removes endpoint" in ep and "/echo/mew" in ep and "frees 1 step" in ep
    assert "no endpoint named 'ghost'" in _tb("drop endpoint ghost")

    # step preview shows method + path + which endpoint (regression: this branch
    # used the wrong key and crashed, so the footer silently showed nothing)
    named = _tb("drop step 1")
    assert "removes step 1" in named and "GET /echo/mew" in named and "get_echo" in named
    orphan = _tb("drop step 2")
    assert "GET /echo/orphan" in orphan and "unnamed" in orphan
    assert "no step with id 99" in _tb("drop step 99")

    assert candidates_for(session, "drop ", "") == ["endpoint ", "step "]
    assert candidates_for(session, "drop endpoint ", "get") == ["get_echo"]


# --------------------------------------------------------------------------- list


async def test_list_lists_endpoints(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    out = await execute_command(session, parse_command("list"))
    assert "get_echo" in out.human and "/echo/mew" in out.human


async def test_on_accepts_bare_words_and_renders_as_strings(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    from gracy.explore._parser import parse_command

    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    # bare word (no quotes needed) maps the status to that string
    await execute_command(session, parse_command("on 404 unavailable"))
    await execute_command(session, parse_command("on 500 none"))
    files = session.save_code(tmp_path / "api.py")
    src = files[0].read_text()
    assert "404: 'unavailable'" in src
    assert "500: None" in src
