"""gracy explore engine tests — inference, live session (real pipeline), codegen."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import typing as t
import uuid
from pathlib import Path

import pytest

from gracy.explore import ExploreSession, StepResult
from gracy.explore._infer import InferredModel, dedupe_models, infer, render_pydantic

# ===========================================================================
# _infer unit matrix
# ===========================================================================


def test_infer_scalars() -> None:
    model = infer([{"a": "x", "b": 1, "c": 1.5, "d": True}], "M")
    kinds = {name: tp.kind for name, tp in model.fields.items()}
    assert kinds == {"a": "str", "b": "int", "c": "float", "d": "bool"}
    assert not any(tp.optional for tp in model.fields.values())


def test_infer_missing_field_becomes_optional() -> None:
    model = infer([{"a": 1, "b": "x"}, {"a": 2}], "M")
    assert model.fields["a"].optional is False
    assert model.fields["b"].optional is True
    assert "b: str | None = None" in render_pydantic([model])


def test_infer_none_value_becomes_optional() -> None:
    model = infer([{"a": None}, {"a": 3}], "M")
    assert model.fields["a"].kind == "int"
    assert model.fields["a"].optional is True


def test_infer_int_float_widens_to_float() -> None:
    model = infer([{"a": 1}, {"a": 2.5}], "M")
    assert model.fields["a"].kind == "float"
    # symmetric
    model2 = infer([{"a": 2.5}, {"a": 1}], "M")
    assert model2.fields["a"].kind == "float"


def test_infer_conflicting_scalars_become_any() -> None:
    model = infer([{"a": 1}, {"a": "x"}], "M")
    assert model.fields["a"].kind == "any"
    assert "a: t.Any" in render_pydantic([model])


def test_infer_nested_dict_naming() -> None:
    model = infer([{"base_stats": {"hp": 10}}], "Pokemon")
    nested = model.fields["base_stats"].model
    assert nested is not None and nested.name == "PokemonBaseStats"
    src = render_pydantic([model])
    assert "class PokemonBaseStats(BaseModel):" in src
    assert "base_stats: PokemonBaseStats" in src


def test_infer_list_of_dicts() -> None:
    model = infer([{"forms": [{"n": "a"}, {"n": "b", "extra": 1}]}], "Pokemon")
    forms = model.fields["forms"]
    assert forms.kind == "list" and forms.item is not None and forms.item.kind == "model"
    item = forms.item.model
    assert item is not None and item.fields["extra"].optional is True
    assert "forms: list[PokemonForms]" in render_pydantic([model])


def test_infer_empty_list_is_list_any() -> None:
    model = infer([{"tags": []}], "M")
    assert model.fields["tags"].kind == "list" and model.fields["tags"].item is None
    assert "tags: list[t.Any]" in render_pydantic([model])


def test_infer_list_items_merge_across_samples() -> None:
    model = infer([{"xs": [{"a": 1}]}, {"xs": [{"b": 2}]}], "M")
    item = model.fields["xs"].item
    assert item is not None and item.model is not None
    assert item.model.fields["a"].optional is True
    assert item.model.fields["b"].optional is True


def test_render_dedupes_identical_shapes() -> None:
    one = infer([{"name": "a", "url": "x"}], "BerryRef")
    two = infer([{"name": "b", "url": "y"}], "ItemRef")
    src = render_pydantic([one, two])
    assert "class BerryRef(BaseModel):" in src
    assert "ItemRef" not in src  # identical shape collapsed into the first name
    _, name_map = dedupe_models([one, two])
    assert name_map["ItemRef"] == "BerryRef"


def test_render_is_deterministic() -> None:
    samples = [{"z": 1, "a": {"k": [1, 2]}, "m": [{"q": None}]}, {"z": 2, "a": {"k": []}}]
    first = render_pydantic([infer(samples, "Root")])
    second = render_pydantic([infer(samples, "Root")])
    assert first == second
    # field order is first-seen, not alphabetical
    assert first.index("z:") < first.index("a:") < first.index("m:")


def test_infer_rejects_non_dict_samples() -> None:
    with pytest.raises(TypeError):
        infer([[1, 2, 3]], "M")


def test_render_empty_model_and_keyword_field() -> None:
    src = render_pydantic([infer([{}], "Empty"), infer([{"class": "x"}], "Kw")])
    assert "class Empty(BaseModel):\n    pass" in src
    assert 'class_: str = Field(alias="class")' in src


# ===========================================================================
# ExploreSession — live, against the conftest test_server (real pipeline)
# ===========================================================================


@pytest.fixture
async def make_session(tmp_path: Path, test_server: str) -> t.AsyncIterator[t.Callable[..., ExploreSession]]:
    sessions: list[ExploreSession] = []

    def factory(
        name: str = "session.json", *, base_url: str | None = None, use_server: bool = True
    ) -> ExploreSession:
        url = base_url if base_url is not None else (test_server if use_server else None)
        session = ExploreSession(tmp_path / name, base_url=url)
        sessions.append(session)
        return session

    yield factory

    for session in sessions:
        await session.aclose()


async def test_execute_get_records_step(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    result = await session.execute("get", "/echo/mew", query={"a": "1"})
    assert isinstance(result, StepResult)
    assert result.step_id == 1
    assert result.status == 200 and result.ok is True and result.error is None
    assert result.method == "GET" and result.path == "/echo/mew"
    assert result.body_preview["path"] == "/echo/mew"
    assert result.body_preview["query"] == {"a": "1"}
    assert result.elapsed_ms > 0
    assert session.session_path.exists()
    history = session.history()
    assert len(history) == 1 and history[0]["status"] == 200


async def test_execute_post_json_body(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    result = await session.execute("post", "/status/201", body_json={"user": "gui", "n": 2})
    assert result.status == 201
    assert result.ok is True
    assert json.loads(result.body_preview["body"]) == {"user": "gui", "n": 2}
    stored = json.loads(session.session_path.read_text())
    assert stored["steps"][0]["body_json"] == {"user": "gui", "n": 2}


async def test_execute_non_2xx_is_permissive(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    result = await session.execute("get", "/status/404")
    assert result.status == 404
    assert result.error is None  # explorer never raises for statuses
    assert result.ok is False


async def test_resume_from_file_roundtrip(make_session: t.Callable[..., ExploreSession]) -> None:
    first = make_session("resume.json")
    await first.execute("get", "/echo/mew")
    first.name_endpoint("get_echo")
    first.set_policy(timeout=9.0)

    resumed = make_session("resume.json", base_url=None, use_server=False)
    assert resumed.base_url == first.base_url  # loaded, not passed
    assert list(resumed.endpoints()) == ["get_echo"]
    assert len(resumed.history()) == 1
    result = await resumed.execute("get", "/echo/ditto")
    assert result.step_id == 2  # ids continue across resume


async def test_name_endpoint_templates_one_segment(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    await session.execute("get", "/echo/ditto")
    assert session.name_endpoint("get_echo", 1) == "/echo/mew"
    template = session.name_endpoint("get_echo", 2)
    assert template == "/echo/{echo}"
    summary = session.endpoints()["get_echo"]
    assert summary["params"] == [{"name": "echo", "index": 1}]
    assert summary["steps"] == 2


async def test_name_endpoint_templates_three_levels(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/team/red")
    await session.execute("get", "/echo/team/blue")
    session.name_endpoint("get_team", 1)
    template = session.name_endpoint("get_team", 2)
    assert template == "/echo/team/{team}"
    # a third differing level upgrades another segment too
    await session.execute("get", "/echo/squad/red")
    template = session.name_endpoint("get_team", 3)
    assert template == "/echo/{echo}/{team}"


async def test_set_param_name(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    await session.execute("get", "/echo/ditto")
    session.name_endpoint("get_echo", 1)
    session.name_endpoint("get_echo", 2)
    assert session.set_param_name("get_echo", 1, "name") == "/echo/{name}"
    with pytest.raises(ValueError):
        session.set_param_name("get_echo", 0, "nope")


async def test_retry_policy_actually_retries(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    confirmation = session.set_policy(retry="2 on 503 wait 0.02")
    assert "retry" in confirmation
    key = uuid.uuid4().hex
    result = await session.execute("get", f"/flaky/{key}", query={"fail_times": "2"})
    assert result.status == 200
    assert result.body_preview == {"ok": True, "calls": 3}  # initial + 2 real retries


async def test_retry_exhaustion_returns_last_response(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    session.set_policy(retry="1 on 503 wait 0.02")
    key = uuid.uuid4().hex
    result = await session.execute("get", f"/flaky/{key}", query={"fail_times": "5"})
    assert result.status == 503  # exhausted retries surface the response, no raise
    assert result.error is None


async def test_env_interpolation_resolved_at_exec_only(
    make_session: t.Callable[..., ExploreSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "s3cr3t-" + uuid.uuid4().hex
    monkeypatch.setenv("GRACY_EXPLORE_TOKEN", secret)
    session = make_session()
    result = await session.execute("get", "/echo/env", query={"tok": "$GRACY_EXPLORE_TOKEN"})
    assert result.body_preview["query"]["tok"] == secret  # server saw the RESOLVED value
    stored = json.loads(session.session_path.read_text())
    assert stored["steps"][0]["query"]["tok"] == "$GRACY_EXPLORE_TOKEN"  # persisted UNRESOLVED


async def test_env_secret_never_persisted(
    make_session: t.Callable[..., ExploreSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sup3r-" + uuid.uuid4().hex
    monkeypatch.setenv("GRACY_EXPLORE_SECRET", secret)
    session = make_session()
    # /status does not echo headers/query back, so the file must be fully clean
    await session.execute(
        "post",
        "/status/200",
        headers={"X-Token": "${GRACY_EXPLORE_SECRET}"},
        query={"k": "$GRACY_EXPLORE_SECRET"},
        body="tok=$GRACY_EXPLORE_SECRET",
    )
    text = session.session_path.read_text()
    assert secret not in text
    assert "GRACY_EXPLORE_SECRET" in text  # placeholders survive


async def test_scrub_default_headers_in_session_file(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("post", "/status/200", headers={"Authorization": "Bearer abc123xyz"})
    stored = json.loads(session.session_path.read_text())
    assert stored["steps"][0]["headers"]["Authorization"] == "***"
    assert "abc123xyz" not in session.session_path.read_text()


async def test_undo_drops_last_step(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/one")
    await session.execute("get", "/echo/two")
    message = session.undo()
    assert "step 2" in message
    assert len(session.history()) == 1
    stored = json.loads(session.session_path.read_text())
    assert len(stored["steps"]) == 1


async def test_undo_drops_last_mutation(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/one")
    session.name_endpoint("get_echo")
    assert session.endpoints()
    session.undo()
    assert session.endpoints() == {}
    empty = make_session("other.json")
    assert empty.undo() == "nothing to undo"


async def test_matched_endpoint_and_model_drift(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew", query={"a": "1"})
    session.name_endpoint("echo_one")
    result = await session.execute("get", "/echo/mew", query={"b": "2"})
    assert result.matched_endpoint == "echo_one"
    assert result.template_proposal is None
    assert any("field 'query.b' seen for the first time" in line for line in result.model_drift)
    assert all(line.startswith("EchoOneResponse:") for line in result.model_drift)


async def test_template_proposal_on_one_segment_diff(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/echo/mew")
    session.name_endpoint("echo_one")
    result = await session.execute("get", "/echo/ditto")
    assert result.matched_endpoint is None
    assert result.template_proposal == "/echo/{echo}"


async def test_set_on_grammar_and_endpoint_summary(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("get", "/status/404")
    session.name_endpoint("missing_thing")
    session.set_on("missing_thing", 404, "none")
    session.set_on(None, 500, "raise:ServerBoom")  # None targets the last endpoint
    session.set_on("missing_thing", 418, "{}")
    assert session.endpoints()["missing_thing"]["on"] == {"404": "none", "500": "raise:ServerBoom", "418": "{}"}
    with pytest.raises(ValueError):
        session.set_on("missing_thing", 400, "not a literal !!")
    with pytest.raises(ValueError):
        session.set_on("nope", 404, "none")


async def test_set_policy_grammars_and_confirmations(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    line = session.set_policy(retry="3 on 429,503 wait 0.5x2")
    assert "retry: 3 retries on 429,503" in line and "backoff 0.5s x2" in line
    assert "throttle: 5 req / 1s" in session.set_policy(throttle="5/1s")
    assert "timeout: 10s" in session.set_policy(timeout=10)
    assert "auth: bearer" in session.set_policy(auth="bearer $TOK")
    assert "header: X-Env: prod" in session.set_policy(header=("X-Env", "prod"))
    new_base = "https://api.example.com/"
    assert "base_url: https://api.example.com" in session.set_policy(base_url=new_base)
    assert session.base_url == "https://api.example.com"
    for bad in (
        lambda: session.set_policy(retry="whenever"),
        lambda: session.set_policy(throttle="fast"),
        lambda: session.set_policy(auth="token abc"),
    ):
        with pytest.raises(ValueError):
            bad()
    stored = json.loads(session.session_path.read_text())
    assert stored["policies"]["retry"]["codes"] == [429, 503]
    assert stored["policies"]["auth"] == {"scheme": "bearer", "token": "$TOK"}


async def test_model_preview_and_rename(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    await session.execute("post", "/status/200", body_json={"user": "gui", "count": 1})
    session.name_endpoint("create_thing")
    preview = session.model_preview()
    assert "class CreateThingResponse(BaseModel):" in preview
    assert "class CreateThingRequest(BaseModel):" in preview
    assert "status: int" in preview and "user: str" in preview
    session.set_model_name("Thing", "create_thing")
    session.set_model_name("ThingInput!request", "create_thing")
    preview = session.model_preview()
    assert "class Thing(BaseModel):" in preview and "class ThingInput(BaseModel):" in preview
    assert "class Thing(BaseModel):" in session.model_preview("Thing")


async def test_class_preview(make_session: t.Callable[..., ExploreSession]) -> None:
    session = make_session()
    session.set_policy(retry="2 on 503 wait 0.1")
    await session.execute("get", "/echo/mew")
    await session.execute("get", "/echo/ditto")
    session.name_endpoint("get_echo", 1)
    session.name_endpoint("get_echo", 2)
    preview = session.class_preview()
    assert '@get("/echo/{echo}")' in preview
    assert "async def get_echo(self, echo: t.Annotated[str, Path]) -> GetEchoResponse: ..." in preview
    assert "retry=Retry(on=status(503), attempts=2, wait=0.1)" in preview
    assert "class Session(Gracy):" in preview  # class name derives from the session file stem


# ===========================================================================
# save_code — generated module, models, and replay tests
# ===========================================================================


def _import_generated(path: Path) -> t.Any:
    name = f"gracy_gen_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def _explored_session(make_session: t.Callable[..., ExploreSession]) -> ExploreSession:
    session = make_session("explored.json")
    await session.execute("get", "/echo/mew")
    await session.execute("get", "/echo/ditto", query={"verbose": "1"})
    session.name_endpoint("get_echo", 1)
    session.name_endpoint("get_echo", 2)
    await session.execute("post", "/status/201", body_json={"user": "gui", "tags": ["a", "b"]})
    session.name_endpoint("create_thing")
    await session.execute("get", "/status/404")
    session.name_endpoint("missing_thing")
    session.set_on("missing_thing", 404, "none")
    return session


async def test_save_code_generates_importable_typed_client(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    session = await _explored_session(make_session)
    out_dir = tmp_path / "gen_import"
    files = session.save_code(out_dir / "my_api.py")
    assert files == [out_dir / "my_api.py"]

    module = _import_generated(files[0])
    from gracy.endpoints import EndpointMethod

    api_cls = module.MyApi
    assert api_cls.base_url == session.base_url

    get_echo = api_cls.__dict__["get_echo"]
    assert isinstance(get_echo, EndpointMethod)
    assert get_echo.method == "GET" and get_echo.path == "/echo/{echo}"
    spec = get_echo.make_spec("get_echo")
    kinds = {p.name: p.kind for p in spec.params}
    assert kinds == {"echo": "path", "verbose": "query"}

    missing = api_cls.__dict__["missing_thing"]
    assert missing.config is not None and missing.config.on == {404: None}

    create = api_cls.__dict__["create_thing"]
    body_spec = create.make_spec("create_thing")
    assert {p.name: p.kind for p in body_spec.params} == {"body": "body"}


async def test_generated_model_validates_recorded_sample(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    session = await _explored_session(make_session)
    files = session.save_code(tmp_path / "gen_models" / "my_api.py")
    module = _import_generated(files[0])
    # validate an ACTUAL recorded sample against the generated model
    import base64 as b64

    stored = json.loads(session.session_path.read_text())
    step = next(s for s in stored["steps"] if s.get("endpoint") == "get_echo")
    # JSON responses are stored parsed (never truncated); non-JSON fall back to b64.
    sample = step["response_json"] if "response_json" in step else json.loads(b64.b64decode(step["response_body_b64"]))
    parsed = module.GetEchoResponse.model_validate(sample)
    assert parsed.path == "/echo/mew"
    request = module.CreateThingRequest.model_validate({"user": "gui", "tags": ["a"]})
    assert request.user == "gui"


async def test_save_code_is_deterministic(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    session = await _explored_session(make_session)
    first = session.save_code(tmp_path / "d1" / "my_api.py")[0].read_text()
    second = session.save_code(tmp_path / "d2" / "my_api.py")[0].read_text()
    assert first == second


async def test_save_code_raise_action_and_literal(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    session = make_session()
    await session.execute("get", "/status/404")
    session.name_endpoint("fetch_thing")
    session.set_on("fetch_thing", 404, "raise:ThingNotFound")
    session.set_on("fetch_thing", 418, "{}")
    source = session.save_code(tmp_path / "gen_raise" / "my_api.py")[0].read_text()
    assert "class ThingNotFound(GracyUserDefinedException):" in source
    assert "on={404: raises(ThingNotFound), 418: {}}" in source
    module = _import_generated(tmp_path / "gen_raise" / "my_api.py")
    assert issubclass(module.ThingNotFound, Exception)


async def test_save_code_env_headers_become_transport_config(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    session = make_session()
    session.set_policy(auth="bearer $MY_TOKEN")
    session.set_policy(header=("X-Env", "prod"))
    await session.execute("get", "/echo/mew")
    session.name_endpoint("get_echo")
    source = session.save_code(tmp_path / "gen_env" / "my_api.py")[0].read_text()
    assert 'os.environ.get("MY_TOKEN", "")' in source  # placeholder preserved, secret never inlined
    assert '"Bearer " + os.environ.get("MY_TOKEN", "")' in source
    assert '"X-Env": "prod",' in source
    assert "TRANSPORT_CONFIG = TransportConfig(" in source
    assert "def build_client(" in source
    module = _import_generated(tmp_path / "gen_env" / "my_api.py")
    assert dict(module.TRANSPORT_CONFIG.base_headers)["X-Env"] == "prod"


async def test_save_code_with_tests_runs_green_offline(
    make_session: t.Callable[..., ExploreSession], tmp_path: Path
) -> None:
    session = await _explored_session(make_session)
    out_dir = tmp_path / "gen_tests"
    files = session.save_code(out_dir / "my_api.py", tests=True)
    assert [f.name for f in files] == ["my_api.py", "test_my_api.py", "my_api.cassette.db"]
    assert all(f.exists() for f in files)

    test_source = files[1].read_text()
    assert 'Replay(mode="replay"' in test_source
    assert "SqliteStorage(CASSETTE)" in test_source
    for name in ("test_get_echo", "test_create_thing", "test_missing_thing"):
        assert f"def {name}() -> None:" in test_source

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", files[1].name],
        cwd=out_dir,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"generated tests failed:\n{proc.stdout}\n{proc.stderr}"
    assert "3 passed" in proc.stdout
