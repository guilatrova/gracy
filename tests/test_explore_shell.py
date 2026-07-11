"""gracy explorer shell tests — command parser, `gracy x` one-shot mode, REPL, umbrella CLI."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from gracy.explore._parser import ParseError, parse_command

# ===========================================================================
# parser matrix
# ===========================================================================


def test_parse_get_with_query_pairs() -> None:
    cmd = parse_command("get /pokemon/mew limit==10 offset==0")
    assert cmd.kind == "request"
    assert cmd.method == "GET"
    assert cmd.path == "/pokemon/mew"
    assert cmd.query == {"limit": "10", "offset": "0"}
    assert cmd.body_json is None and cmd.body is None


def test_parse_all_methods() -> None:
    for method in ("get", "post", "put", "patch", "delete", "head"):
        cmd = parse_command(f"{method} /x")
        assert cmd.kind == "request" and cmd.method == method.upper()


def test_parse_body_string_and_json_fields() -> None:
    cmd = parse_command('post /users name=gui active:=true n:=2 tags:=\'["a","b"]\'')
    assert cmd.body_json == {"name": "gui", "active": True, "n": 2, "tags": ["a", "b"]}
    assert cmd.query == {}


def test_parse_json_fields_unquoted_httpie_style() -> None:
    # bare k:=[...] / k:={...} survive shlex (the quotes inside are protected pre-tokenize)
    cmd = parse_command('post /status/201 kind=note priority:=2 tags:=["a","b"] meta:={"x": 1}')
    assert cmd.body_json == {"kind": "note", "priority": 2, "tags": ["a", "b"], "meta": {"x": 1}}
    with pytest.raises(ParseError, match="invalid JSON value"):
        parse_command('post /x tags:=["a",]')


def test_parse_headers_and_query_and_body_mix() -> None:
    cmd = parse_command("post /u q==1 name=x -H 'Accept: application/json' -H 'X-K: a:b'")
    assert cmd.headers == {"Accept": "application/json", "X-K": "a:b"}
    assert cmd.query == {"q": "1"}
    assert cmd.body_json == {"name": "x"}


def test_parse_inline_json_unquoted_with_spaces() -> None:
    cmd = parse_command('post /x {"a": 1, "b": [1, 2], "c": {"d": "}"}}')
    assert cmd.body_json == {"a": 1, "b": [1, 2], "c": {"d": "}"}}


def test_parse_at_file_bodies(tmp_path: Path) -> None:
    json_file = tmp_path / "body.json"
    json_file.write_text('{"user": "gui"}', "utf-8")
    cmd = parse_command(f"post /x @{json_file}")
    assert cmd.body_json == {"user": "gui"}

    raw_file = tmp_path / "body.txt"
    raw_file.write_text("plain text body", "utf-8")
    cmd2 = parse_command(f"post /x @{raw_file}")
    assert cmd2.body == "plain text body" and cmd2.body_json is None


def test_parse_request_errors() -> None:
    with pytest.raises(ParseError, match="needs a <path>"):
        parse_command("get")
    with pytest.raises(ParseError, match="cannot mix"):
        parse_command('post /x a=1 {"b": 2}')
    with pytest.raises(ParseError, match="invalid JSON value"):
        parse_command("post /x a:=not-json")
    with pytest.raises(ParseError, match="-H needs a value"):
        parse_command("get /x -H")
    with pytest.raises(ParseError, match="invalid header"):
        parse_command("get /x -H nocolon")
    with pytest.raises(ParseError, match="unbalanced braces"):
        parse_command('post /x {"a": 1')
    with pytest.raises(ParseError, match="cannot read"):
        parse_command("post /x @/definitely/not/here.json")
    with pytest.raises(ParseError, match="unexpected token"):
        parse_command("get /x stray")


def test_parse_name_model_on_param() -> None:
    assert parse_command("name GetPokemon").name == "GetPokemon"
    assert parse_command("model Pokemon").name == "Pokemon"
    assert parse_command("model CreateUser!request").name == "CreateUser!request"

    on = parse_command("on 404 none")
    assert (on.status, on.action) == (404, "none")
    assert parse_command("on 404 raise:PokemonNotFound").action == "raise:PokemonNotFound"
    assert parse_command("on 404 {}").action == "{}"

    param = parse_command("param 1 as pokemon_name")
    assert (param.index, param.name) == (1, "pokemon_name")


def test_parse_policies() -> None:
    assert parse_command("retry 3 on 429,503 wait 0.5x2").spec == "3 on 429,503 wait 0.5x2"
    assert parse_command("throttle 5/1s").spec == "5/1s"
    assert parse_command("timeout 2.5").value == "2.5"
    assert parse_command("auth bearer $TOK").spec == "bearer $TOK"
    assert parse_command("auth basic gui secret").spec == "basic gui secret"
    header = parse_command("header X-Api-Key $KEY")
    assert (header.name, header.value) == ("X-Api-Key", "$KEY")
    assert parse_command("base https://api.example.com").value == "https://api.example.com"


def test_parse_policy_errors_are_parse_errors() -> None:
    for bad in ("retry banana", "retry 3 on abc", "throttle nope", "timeout soon", "auth digest x", "on 404 raise:9x"):
        with pytest.raises(ParseError):
            parse_command(bad)


def test_parse_show_save_and_singletons() -> None:
    assert parse_command("show last").target == "last"
    assert parse_command("show endpoints").target == "endpoints"
    assert parse_command("show history").target == "history"
    assert parse_command("show class").target == "class"
    show_model = parse_command("show model Pokemon")
    assert (show_model.target, show_model.name) == ("model", "Pokemon")
    assert parse_command("show model").name is None

    save = parse_command("save api.py --tests")
    assert (save.path, save.tests) == ("api.py", True)
    assert parse_command("save api.py").tests is False

    assert parse_command("undo").kind == "undo"
    assert parse_command("help").kind == "help"
    assert parse_command("quit").kind == "quit"
    assert parse_command("exit").kind == "quit"


def test_parse_unknown_and_empty() -> None:
    with pytest.raises(ParseError, match="unknown command"):
        parse_command("frobnicate /x")
    with pytest.raises(ParseError, match="empty command"):
        parse_command("   ")
    with pytest.raises(ParseError, match="show"):
        parse_command("show everything")


# ===========================================================================
# `gracy x` one-shot agent mode (subprocess)
# ===========================================================================


def run_cli(
    *args: str,
    stdin: str | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "gracy.cli", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=full_env,
        timeout=90,
    )


def test_x_get_json(test_server: str, tmp_path: Path) -> None:
    session = tmp_path / "s.json"
    proc = run_cli("x", "get /echo/hi", "--base", test_server, "--session", str(session), "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == 200
    assert data["step_id"] == 1
    assert data["ok"] is True
    assert data["body_preview"]["path"] == "/echo/hi"
    assert session.exists()


def test_x_human_line(test_server: str, tmp_path: Path) -> None:
    proc = run_cli("x", "get /echo/hi", "--base", test_server, "--session", str(tmp_path / "s.json"))
    assert proc.returncode == 0, proc.stderr
    assert "GET" in proc.stdout and "200" in proc.stdout


def test_x_agent_sequence_and_save(test_server: str, tmp_path: Path) -> None:
    """get -> name -> on -> save across separate invocations sharing the session file."""
    session = str(tmp_path / "seq.json")

    first = run_cli("x", "get /echo/mew", "--base", test_server, "--session", session, "--json")
    assert first.returncode == 0, first.stderr

    named = run_cli("x", "name Echo", "--session", session, "--json")
    assert named.returncode == 0, named.stderr
    assert json.loads(named.stdout)["endpoint"] == "Echo"

    on = run_cli("x", "on 404 none", "--session", session, "--json")
    assert on.returncode == 0, on.stderr
    assert json.loads(on.stdout) == {"ok": True, "endpoint": "Echo", "status": 404, "action": "none"}

    endpoints = run_cli("x", "show endpoints", "--session", session, "--json")
    ep = json.loads(endpoints.stdout)["endpoints"]["Echo"]
    assert ep["on"] == {"404": "none"}

    out = tmp_path / "echo_api.py"
    saved = run_cli("x", f"save {out}", "--session", session, "--json")
    assert saved.returncode == 0, saved.stderr
    assert str(out) in json.loads(saved.stdout)["written"]
    assert out.exists()

    module_name = f"gen_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, out)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # imports clean: models + Gracy class


def test_x_bad_grammar_exit_2_json() -> None:
    proc = run_cli("x", "frobnicate /x", "--json")
    assert proc.returncode == 2
    assert "error" in json.loads(proc.stdout)


def test_x_bad_grammar_exit_2_human() -> None:
    proc = run_cli("x", "get", "--json")
    assert proc.returncode == 2
    assert "error" in json.loads(proc.stdout)
    human = run_cli("x", "get")
    assert human.returncode == 2
    assert "usage" in human.stderr


def test_x_connection_refused_exit_1_json(tmp_path: Path) -> None:
    proc = run_cli(
        "x", "get /nope", "--base", "http://127.0.0.1:9", "--session", str(tmp_path / "s.json"), "--json"
    )
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["error"]
    assert data["status"] is None and data["ok"] is False


def test_x_execution_error_exit_1(tmp_path: Path) -> None:
    proc = run_cli("x", "name Echo", "--session", str(tmp_path / "empty.json"), "--json")
    assert proc.returncode == 1
    assert "No steps recorded" in json.loads(proc.stdout)["error"]


def test_x_env_vars_resolved_on_wire_unresolved_in_session(test_server: str, tmp_path: Path) -> None:
    session = tmp_path / "env.json"
    proc = run_cli(
        "x",
        "get /echo/env tok==$GRACY_SHELL_TOKEN",
        "--base",
        test_server,
        "--session",
        str(session),
        "--json",
        env={"GRACY_SHELL_TOKEN": "sekret"},
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["body_preview"]["query"]["tok"] == "sekret"  # resolved on the wire
    stored = json.loads(session.read_text("utf-8"))
    assert stored["steps"][0]["query"]["tok"] == "$GRACY_SHELL_TOKEN"  # never persisted resolved
    assert "sekret" not in session.read_text("utf-8")


def test_x_show_history_json(test_server: str, tmp_path: Path) -> None:
    session = str(tmp_path / "h.json")
    run_cli("x", "get /echo/a", "--base", test_server, "--session", session)
    run_cli("x", "get /status/404", "--session", session)
    proc = run_cli("x", "show history", "--session", session, "--json")
    history = json.loads(proc.stdout)["history"]
    assert [h["status"] for h in history] == [200, 404]


# ===========================================================================
# REPL (piped stdin — the non-tty path)
# ===========================================================================


def test_repl_smoke_pipe(test_server: str, tmp_path: Path) -> None:
    out = tmp_path / "out.py"
    proc = run_cli(
        "explore",
        test_server,
        "--session",
        str(tmp_path / "repl.json"),
        stdin=f"get /echo/hi\nname Echo\nsave {out}\nquit\n",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "200" in proc.stdout
    assert "wrote" in proc.stdout and "out.py" in proc.stdout
    assert out.exists()


def test_repl_survives_errors_and_eof(test_server: str, tmp_path: Path) -> None:
    proc = run_cli(
        "explore",
        test_server,
        "--session",
        str(tmp_path / "repl.json"),
        stdin="totally bogus\nget /status/404\nshow endpoints\n",  # no quit: EOF path
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "unknown command" in proc.stdout
    assert "404" in proc.stdout


def test_repl_banner_and_hints(test_server: str, tmp_path: Path) -> None:
    proc = run_cli(
        "explore",
        test_server,
        "--session",
        str(tmp_path / "banner.json"),
        stdin="get /echo/mew\nquit\n",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "gracy explorer" in proc.stdout
    assert test_server in proc.stdout  # banner shows base_url
    assert "help" in proc.stdout
    assert "name Echo" in proc.stdout  # auto-name hint


def test_explore_alias(test_server: str, tmp_path: Path) -> None:
    proc = run_cli("explore", test_server, "--session", str(tmp_path / "alias.json"), stdin="quit\n", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "gracy explorer" in proc.stdout


# ===========================================================================
# umbrella CLI: version / delegation / usage
# ===========================================================================


def test_version() -> None:
    proc = run_cli("--version")
    assert proc.returncode == 0
    assert proc.stdout.startswith("gracy ")

    import gracy

    assert gracy.__version__ in proc.stdout


def test_no_args_exit_2() -> None:
    proc = run_cli()
    assert proc.returncode == 2
    assert "usage" in proc.stderr


def test_unknown_command_exit_2() -> None:
    proc = run_cli("bogus")
    assert proc.returncode == 2
    assert "unknown command" in proc.stderr


def test_monitor_once_passthrough(tmp_path: Path) -> None:
    proc = run_cli("monitor", "--once", env={"GRACY_MONITOR_DIR": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr


def test_docs_help_passthrough() -> None:
    proc = run_cli("docs", "--help")
    assert proc.returncode == 0
    assert "usage" in proc.stdout


def test_installed_gracy_entry_point(test_server: str, tmp_path: Path) -> None:
    """The `gracy` console script itself (rebuilt via maturin develop) works."""
    script = Path(sys.executable).parent / "gracy"
    if not script.exists():
        pytest.skip("gracy entry point not installed in this venv")
    proc = subprocess.run(
        [str(script), "x", "get /echo/hi", "--base", test_server, "--session", str(tmp_path / "ep.json"), "--json"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == 200
