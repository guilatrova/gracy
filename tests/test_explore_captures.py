"""Tests for the named-response-captures feature of `gracy explore`.

Covers the whole path: `set <name> <path>` parsing, `capture()` snapshotting
(dotted / [n] / negative-index / top-level key + every error), `{{name}}`
expansion inside `execute()` (baked into storage while $VAR stays unresolved),
non-conflict with $VAR and literal single-brace strings, resume, codegen, and
the live `describe_impact` toolbar preview.

Pure-resolution and error cases use a hand-built ExploreSession with `_data`
set directly; anything that needs a real round-trip runs against the conftest
`test_server` (its `/echo/...` route returns JSON echoing the path/query/headers,
`/status/{code}` echoes the posted body).
"""

from __future__ import annotations

import json
import typing as t
from pathlib import Path

import pytest

from gracy.explore import ExploreSession
from gracy.explore._parser import Command, ParseError, parse_command
from gracy.explore._session import SCHEMA_VERSION
from gracy.explore.repl import describe_impact


# --------------------------------------------------------------------------- fixtures / helpers


@pytest.fixture
async def make_session(tmp_path: Path, test_server: str) -> t.AsyncIterator[t.Callable[..., ExploreSession]]:
    sessions: list[ExploreSession] = []

    def factory(name: str = "session.json", *, base_url: str | None = None, use_server: bool = True) -> ExploreSession:
        url = base_url if base_url is not None else (test_server if use_server else None)
        session = ExploreSession(tmp_path / name, base_url=url)
        sessions.append(session)
        return session

    yield factory

    for session in sessions:
        await session.aclose()


def _hand_session(
    tmp_path: Path,
    *,
    name: str = "hand.json",
    steps: list[dict[str, t.Any]] | None = None,
    captures: dict[str, t.Any] | None = None,
) -> ExploreSession:
    """A session with no client, `_data` populated directly - for pure resolution."""
    session = ExploreSession(tmp_path / name)
    if steps is not None:
        session._data["steps"] = steps
    if captures is not None:
        session._data["captures"] = captures
    return session


def _impact_text(session: ExploreSession, line: str) -> str:
    """Flatten a describe_impact FormattedText into its plain-text content."""
    return "".join(text for _style, text in describe_impact(session, line))


# --------------------------------------------------------------------------- parse: `set`


def test_parse_set_command() -> None:
    cmd = parse_command("set berry results[0].name")
    assert isinstance(cmd, Command)
    assert cmd.kind == "set"
    assert cmd.name == "berry"
    assert cmd.path == "results[0].name"


def test_parse_set_too_few_tokens() -> None:
    with pytest.raises(ParseError):
        parse_command("set")  # no name, no path
    with pytest.raises(ParseError):
        parse_command("set x")  # name but no path


# --------------------------------------------------------------------------- capture(): resolution


def test_capture_resolves_dotted_index_negative_and_top_key(tmp_path: Path) -> None:
    body = {"items": [{"name": "first"}, {"name": "last"}], "count": 2}
    session = _hand_session(tmp_path, steps=[{"id": 1, "method": "GET", "path": "/x", "response_json": body}])

    assert session.capture("a", "items[0].name") == "first"  # dotted + [n]
    assert session.capture("b", "items[-1].name") == "last"  # negative index
    assert session.capture("c", "count") == 2  # top-level key
    assert session.capture("d", "items") == body["items"]  # top-level key -> list

    assert session.captures == {"a": "first", "b": "last", "c": 2, "d": body["items"]}
    # snapshot persisted to disk under "captures"
    stored = json.loads(session.session_path.read_text())
    assert stored["captures"] == {"a": "first", "b": "last", "c": 2, "d": body["items"]}


async def test_capture_snapshot_stored_in_session_file(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    result = await session.execute("get", "/echo/mew", query={"id": "42"})
    assert result.status == 200

    assert session.capture("who", "path") == "/echo/mew"
    assert session.capture("ident", "query.id") == "42"

    assert session.captures["who"] == "/echo/mew"
    stored = json.loads(session.session_path.read_text())
    assert stored["captures"] == {"who": "/echo/mew", "ident": "42"}


# --------------------------------------------------------------------------- capture(): errors


def test_capture_errors_when_no_request_yet(tmp_path: Path) -> None:
    session = _hand_session(tmp_path, steps=[])
    with pytest.raises(ValueError, match="no request yet"):
        session.capture("x", "a")


def test_capture_errors_when_last_response_not_json(tmp_path: Path) -> None:
    session = _hand_session(tmp_path, steps=[{"id": 1, "method": "GET", "path": "/x", "status": 200}])
    with pytest.raises(ValueError, match="not JSON"):
        session.capture("x", "a")


def test_capture_errors_on_missing_key(tmp_path: Path) -> None:
    session = _hand_session(tmp_path, steps=[{"id": 1, "method": "GET", "path": "/x", "response_json": {"a": 1}}])
    with pytest.raises(ValueError, match="no key 'b'"):
        session.capture("x", "b")


def test_capture_errors_on_index_out_of_range(tmp_path: Path) -> None:
    body = {"items": [1, 2]}
    session = _hand_session(tmp_path, steps=[{"id": 1, "method": "GET", "path": "/x", "response_json": body}])
    with pytest.raises(ValueError, match="out of range"):
        session.capture("x", "items[5]")


# --------------------------------------------------------------------------- {{name}} in execute()


async def test_execute_expands_captures_everywhere_and_bakes_storage(
    make_session: t.Callable[..., ExploreSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYVAR", "ENVVAL123")
    session = make_session()
    session.captures["berry"] = "mew"

    result = await session.execute(
        "get",
        "/echo/{{berry}}",  # capture in the PATH
        query={"cap": "{{berry}}", "tok": "$MYVAR"},  # capture + env in a QUERY value
        headers={"X-Cap": "{{berry}}", "X-Env": "$MYVAR"},  # capture + env in a HEADER value
        body_json={"name": "{{berry}}", "tok": "$MYVAR"},  # capture + env inside a body_json string
    )
    assert result.status == 200

    # -- wire form: BOTH {{berry}} and $MYVAR are resolved (the server saw concrete values)
    assert result.body_preview["path"] == "/echo/mew"
    assert result.body_preview["query"] == {"cap": "mew", "tok": "ENVVAL123"}
    assert result.body_preview["headers"]["x-cap"] == "mew"
    assert result.body_preview["headers"]["x-env"] == "ENVVAL123"

    # -- stored step: {{berry}} is BAKED to "mew"; $MYVAR stays UNRESOLVED as a placeholder
    stored = json.loads(session.session_path.read_text())
    step = stored["steps"][0]
    assert step["path"] == "/echo/mew"  # capture baked into the concrete path
    assert step["query"] == {"cap": "mew", "tok": "$MYVAR"}
    assert step["headers"] == {"X-Cap": "mew", "X-Env": "$MYVAR"}
    assert step["body_json"] == {"name": "mew", "tok": "$MYVAR"}

    # -- the resolved secret is never written to disk (echoed value redacted)
    assert "ENVVAL123" not in session.session_path.read_text()


async def test_execute_unknown_capture_raises_at_send(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    with pytest.raises(ValueError, match="no capture named 'ghost'"):
        await session.execute("get", "/echo/{{ghost}}")
    # nothing recorded - it raised before touching the wire
    assert session.history() == []


async def test_no_conflict_capture_env_and_literal_single_brace(
    make_session: t.Callable[..., ExploreSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYVAR", "ENVVAL123")
    session = make_session()
    session.captures["cap"] = "mew"

    # one body string mixing all three: {{cap}} capture, $MYVAR env, {literal} single-brace
    result = await session.execute("post", "/status/200", body="mix={{cap}}-$MYVAR-{literal}")
    assert result.status == 200

    # -- wire form: capture + env resolved, single-brace passed through verbatim
    assert result.body_preview["body"] == "mix=mew-ENVVAL123-{literal}"

    # -- stored form: capture baked, env placeholder kept, single-brace untouched
    import base64

    stored = json.loads(session.session_path.read_text())
    raw = base64.b64decode(stored["steps"][0]["body_b64"]).decode("utf-8")
    assert raw == "mix=mew-$MYVAR-{literal}"
    assert "ENVVAL123" not in session.session_path.read_text()


# --------------------------------------------------------------------------- resume


async def test_resume_captures_survive_reload(make_session: t.Callable[..., ExploreSession]) -> None:
    first = make_session("resume.json")
    await first.execute("get", "/echo/mew", query={"id": "99"})
    first.capture("who", "path")
    first.capture("ident", "query.id")

    resumed = make_session("resume.json", base_url=None, use_server=False)
    assert resumed.captures == {"who": "/echo/mew", "ident": "99"}


def test_resume_session_file_without_captures_key_loads(tmp_path: Path) -> None:
    legacy = {
        "schema": SCHEMA_VERSION,
        "base_url": "http://x",
        "policies": {},
        "steps": [],
        "endpoints": {},
        "models": {},
        # NOTE: no "captures" key at all (older session file)
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy))

    session = ExploreSession(path)
    assert session.captures == {}  # defaulted, not a KeyError
    session.captures["late"] = "value"  # and still usable
    assert session._resolve_captures_str("{{late}}") == "value"


# --------------------------------------------------------------------------- codegen


async def test_codegen_bakes_concrete_path_no_capture_leak(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    session = make_session()
    session.captures["berry"] = "mew"
    await session.execute("get", "/echo/{{berry}}")
    session.name_endpoint("get_echo")

    source = session.save_code(tmp_path / "gen" / "client.py")[0].read_text()
    assert '@get("/echo/mew")' in source  # concrete path, baked from the capture
    assert "{{" not in source  # no {{ }} template leaked into the generated client
    assert "berry" not in source


# --------------------------------------------------------------------------- describe_impact preview


def test_describe_impact_set_preview(tmp_path: Path) -> None:
    body = {"results": [{"name": "cheri"}]}
    session = _hand_session(tmp_path, steps=[{"id": 1, "method": "GET", "path": "/berry", "response_json": body}])
    # the toolbar previews the REAL value that will be captured
    assert _impact_text(session, "set berry results[0].name") == "captures results[0].name = 'cheri'"
    assert _impact_text(session, "peek results[0].name") == "shows results[0].name = 'cheri'"
    assert _impact_text(session, "set x nope.field") == "nope.field not found in the last response"


def test_peek_command_reads_without_capturing(tmp_path: Path) -> None:
    body = {"results": [{"name": "cheri", "id": 1}]}
    session = _hand_session(tmp_path, steps=[{"id": 1, "method": "GET", "path": "/berry", "response_json": body}])
    assert session.peek("results[0].name") == "cheri"
    assert session.peek("results[0]") == {"name": "cheri", "id": 1}
    assert session.captures == {}  # peek never stores
    with pytest.raises(ValueError, match="no key"):
        session.peek("nope")


def test_bare_json_path_suggests_peek() -> None:
    with pytest.raises(ParseError, match=r"did you mean `peek results\[0\]`"):
        parse_command("results[0]")
    with pytest.raises(ParseError, match="type 'help'"):
        parse_command("bogus")


def test_describe_impact_request_capture_set_vs_unset(tmp_path: Path) -> None:
    session = _hand_session(tmp_path)

    # unset: the preview warns the ref has no value yet
    unset = _impact_text(session, "get /echo/{{berry}}")
    assert "{{berry}} not set" in unset

    # set: the preview shows the ref resolving to its captured value
    session.captures["berry"] = "mew"
    setp = _impact_text(session, "get /echo/{{berry}}")
    assert "{{berry}}=" in setp
    assert "mew" in setp


async def test_env_var_in_path_resolves_and_stores_unresolved(tmp_path, test_server):
    """Reviewer finding #1: `${VAR}` in a path used to crash via format_url.
    It must resolve for the wire while staying unresolved (secret-safe) in storage."""
    import json as _json
    import os

    os.environ["GRACY_SEG"] = "echoed"
    s = ExploreSession(tmp_path / "envpath.json", base_url=test_server)
    try:
        r = await s.execute("get", "/echo/${GRACY_SEG}")
        assert r.status == 200
        assert r.body_preview["path"] == "/echo/echoed"  # env resolved on the wire
        data = _json.loads((tmp_path / "envpath.json").read_text())
        assert data["steps"][-1]["path"] == "/echo/${GRACY_SEG}"  # unresolved on disk
    finally:
        await s.aclose()


async def test_captures_and_env_and_curly_do_not_conflict(tmp_path, test_server):
    """The three sigils coexist: {{cap}} bakes, $VAR resolves at wire, a literal
    single-brace {x} in a body string is left untouched by capture resolution."""
    import os

    os.environ["GRACY_TOK"] = "sekret"
    s = ExploreSession(tmp_path / "mix.json", base_url=test_server)
    try:
        await s.execute("get", "/echo/seed")
        s.capture("seg", "path")  # "/echo/seed"
        r = await s.execute(
            "post", "/status/200",
            headers={"X-Cap": "{{seg}}", "X-Env": "${GRACY_TOK}"},
            body='{"literal": "{keepme}"}',
        )
        assert r.status == 200
        import json as _json
        step = _json.loads((tmp_path / "mix.json").read_text())["steps"][-1]
        # capture baked concrete in the stored header; env stays a placeholder; {keepme} untouched
        assert step["headers"]["X-Cap"] == "/echo/seed"
        assert step["headers"]["X-Env"] == "${GRACY_TOK}"
        import base64
        assert "{keepme}" in base64.b64decode(step["body_b64"]).decode()
    finally:
        await s.aclose()

