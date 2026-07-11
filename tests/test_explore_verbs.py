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


def test_parse_rename() -> None:
    c = parse_command("rename endpoint old new")
    assert (c.kind, c.target, c.name, c.value) == ("rename", "endpoint", "old", "new")
    c2 = parse_command("rename model Pokemon PokemonDetail")
    assert (c2.target, c2.name, c2.value) == ("model", "Pokemon", "PokemonDetail")
    for bad in ("rename endpoint only-two", "rename widget a b", "rename endpoint a b c"):
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


# --------------------------------------------------------------------------- list


async def test_list_lists_endpoints(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    out = await execute_command(session, parse_command("list"))
    assert "get_echo" in out.human and "/echo/mew" in out.human
