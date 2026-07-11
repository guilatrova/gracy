"""gracy explore --stdio (persistent JSONL loop) and --check (drift detection)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gracy.explore._check import ShapeDiff, diff_shape, flatten_typed


def run_cli(*args: str, stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gracy.cli", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=os.environ.copy(),
        timeout=90,
    )


# =========================================================================== flatten_typed


def test_flatten_typed_scalars_and_nesting() -> None:
    shape = flatten_typed({"id": 1, "name": "x", "meta": {"hp": 10, "ok": True}})
    assert shape == {"id": "int", "name": "str", "meta": "dict", "meta.hp": "int", "meta.ok": "bool"}


def test_flatten_typed_list_of_dicts_merges_items() -> None:
    shape = flatten_typed({"forms": [{"n": "a"}, {"n": "b", "extra": 1}]})
    assert shape["forms"] == "list"
    assert shape["forms[].n"] == "str"
    assert shape["forms[].extra"] == "int"  # field present in only one item still surfaces


# =========================================================================== diff_shape


def test_diff_shape_no_change() -> None:
    body = {"id": 1, "name": "x"}
    assert diff_shape(body, dict(body)).has_drift is False


def test_diff_shape_added_and_removed() -> None:
    d = diff_shape({"id": 1, "height": 4}, {"id": 1, "weight": 60})
    assert d.removed == ["height"]
    assert d.added == ["weight"]
    assert d.type_changed == []
    assert d.has_drift is True


def test_diff_shape_type_change() -> None:
    d = diff_shape({"id": 1}, {"id": "1"})
    assert d.type_changed == ["id: int -> str"]


def test_diff_shape_null_is_not_a_type_change() -> None:
    # recorded null, now populated (or vice versa) is not drift
    assert diff_shape({"next": None}, {"next": "url"}).has_drift is False
    assert diff_shape({"next": "url"}, {"next": None}).has_drift is False


def test_shape_diff_as_dict() -> None:
    d = ShapeDiff(added=["a"], removed=["b"], type_changed=["c: int -> str"])
    assert d.as_dict() == {"added": ["a"], "removed": ["b"], "type_changed": ["c: int -> str"]}


# =========================================================================== --stdio


def test_stdio_processes_jsonl_stream(test_server: str, tmp_path: Path) -> None:
    session = tmp_path / "stdio.json"
    stdin = (
        json.dumps({"cmd": "get /echo/one"}) + "\n"
        + json.dumps({"cmd": "endpoint get_echo"}) + "\n"
        + json.dumps({"cmd": "get /echo/two"}) + "\n"
    )
    proc = run_cli("explore", "--stdio", "--base", test_server, "--session", str(session), stdin=stdin)
    assert proc.returncode == 0, proc.stderr

    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 3
    assert lines[0]["status"] == 200 and lines[0]["step_id"] == 1
    assert lines[1]["endpoint"] == "get_echo"
    # 2nd call to a differing segment proposes a {param} template (not an auto-match yet)
    assert lines[2]["step_id"] == 2
    assert lines[2]["template_proposal"] == "/echo/{echo}"
    # session persisted across the in-memory run
    assert session.exists()
    data = json.loads(session.read_text())
    assert "get_echo" in data["endpoints"] and len(data["steps"]) == 2


def test_stdio_bad_command_emits_error_but_continues(test_server: str, tmp_path: Path) -> None:
    stdin = (
        "not json at all\n"
        + json.dumps({"cmd": "totally bogus command"}) + "\n"
        + json.dumps({"cmd": "get /echo/ok"}) + "\n"
    )
    proc = run_cli("explore", "--stdio", "--base", test_server, "--session", str(tmp_path / "s.json"), stdin=stdin)
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert "error" in lines[0]  # invalid JSON
    assert "error" in lines[1]  # unknown command
    assert lines[2]["status"] == 200  # stream survived and kept going


def test_stdio_accepts_bare_json_string_command(test_server: str, tmp_path: Path) -> None:
    stdin = json.dumps("get /echo/bare") + "\n"
    proc = run_cli("explore", "--stdio", "--base", test_server, "--session", str(tmp_path / "s.json"), stdin=stdin)
    assert proc.returncode == 0, proc.stderr
    out = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert out[0]["status"] == 200


# =========================================================================== --check


def _record_echo_endpoint(test_server: str, session: Path) -> None:
    stdin = json.dumps({"cmd": "get /echo/mew"}) + "\n" + json.dumps({"cmd": "endpoint get_echo"}) + "\n"
    proc = run_cli("explore", "--stdio", "--base", test_server, "--session", str(session), stdin=stdin)
    assert proc.returncode == 0, proc.stderr


def test_check_clean_when_shape_matches(test_server: str, tmp_path: Path) -> None:
    session = tmp_path / "check.json"
    _record_echo_endpoint(test_server, session)
    proc = run_cli("explore", "--check", "--base", test_server, "--session", str(session))
    assert proc.returncode == 0, proc.stderr
    assert "no drift" in proc.stdout
    assert "get_echo" in proc.stdout


def test_check_detects_drift_against_recording(test_server: str, tmp_path: Path) -> None:
    session = tmp_path / "check.json"
    _record_echo_endpoint(test_server, session)

    # Tamper the RECORDED sample so it disagrees with what the live server returns:
    # add a field the live /echo response won't have (-> "removed"), and drop one
    # it will have (-> "added").
    data = json.loads(session.read_text())
    step = next(s for s in data["steps"] if s.get("endpoint") == "get_echo")
    step["response_json"] = {"path": "/echo/mew", "ghost_field": 123}  # live has 'query'/'headers', not 'ghost_field'
    session.write_text(json.dumps(data, indent=2))

    proc = run_cli("explore", "--check", "--base", test_server, "--session", str(session), "--json")
    assert proc.returncode == 1, proc.stdout
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    ep = report["endpoints"][0]
    assert ep["ok"] is False
    assert "ghost_field" in ep["shape"]["removed"]
    assert "query" in ep["shape"]["added"] or "headers" in ep["shape"]["added"]


def test_check_no_named_endpoints_is_clean(test_server: str, tmp_path: Path) -> None:
    session = tmp_path / "empty.json"
    # a step but no named endpoint
    run_cli("explore", "--stdio", "--base", test_server, "--session", str(session),
            stdin=json.dumps({"cmd": "get /echo/x"}) + "\n")
    proc = run_cli("explore", "--check", "--session", str(session))
    assert proc.returncode == 0
    assert "no named endpoints" in (proc.stdout + proc.stderr)


async def test_check_all_programmatic(test_server: str, tmp_path: Path) -> None:
    from gracy.explore import ExploreSession

    session = ExploreSession(tmp_path / "prog.json", base_url=test_server)
    try:
        await session.execute("get", "/echo/mew")
        session.name_endpoint("get_echo")
        results = await session.check_all()
    finally:
        await session.aclose()

    assert len(results) == 1
    assert results[0].endpoint == "get_echo"
    assert results[0].ok is True  # live server returns the same shape it recorded
