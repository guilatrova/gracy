"""gracy.docs: static introspection, OpenAPI 3.1 emitter, YAML serializer, CLI.

Runs against a purpose-built ZooAPI (mirroring the typing showcase, plus a
non-pydantic endpoint and endpoint-level overrides) AND the real
examples/v2_typing_showcase.py PokeAPI imported directly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import typing as t
from pathlib import Path
from typing import Annotated

import pytest
import yaml
from pydantic import BaseModel

import gracy
from gracy import (
    Backoff,
    Body,
    Gracy,
    GracyConfig,
    GracyNamespace,
    Header,
    Path as PathParam,
    PydanticDecoder,
    Query,
    Rate,
    Retry,
    Throttle,
    get,
    post,
    raises,
    status,
    strict,
)
from gracy.docs import inspect_api, to_json, to_openapi, to_yaml
from gracy.docs._yaml import dumps as yaml_dumps

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.v2_typing_showcase import PokeAPI, PokemonNotFound  # noqa: E402


# --------------------------------------------------------------------------- test API


class Animal(BaseModel):
    id: int
    name: str
    legs: int


class AnimalPage(BaseModel):
    count: int
    next: str | None
    results: list[dict]


class AnimalNotFound(gracy.GracyUserDefinedException):
    BASE_MESSAGE = "No animal named [{NAME}]: got {STATUS} from {URL}"


class HabitatNamespace(GracyNamespace):
    path_prefix = "/habitat"

    @get("/{name}")
    async def get_one(self, name: Annotated[str, PathParam]) -> dict:
        """Fetch one habitat."""
        ...


class ZooAPI(Gracy):
    """Zoo API client.

    Talks to the Zoo service: animals, habitats, and more.
    Multi-line description with a colon: yes, and {braces} too.
    """

    base_url = "https://zoo.example.com/api"

    config = GracyConfig(
        decoder=PydanticDecoder(),
        retry=Retry(on=(status(429, 502, 503), TimeoutError), attempts=3, wait=Backoff(0.5, multiplier=2)),
        throttle=Throttle(rules=[Rate(5, per="1s")]),
    )

    habitat = HabitatNamespace()

    @get("/animal/{name}", on={404: None})
    async def get_animal(self, name: Annotated[str, PathParam]) -> Animal | None:
        """Fetch one animal.

        Returns None when the animal does not exist."""
        ...

    @get(
        "/animal/{name}/strict",
        on={404: raises(AnimalNotFound)},
        status_policy=strict(200),
        retry=Retry(on=status(500), attempts=5, wait=1.0),
    )
    async def get_animal_strict(self, name: Annotated[str, PathParam]) -> Animal: ...

    @get("/animal")
    async def list_animals(
        self,
        offset: Annotated[int, Query] = 0,
        limit: Annotated[int, Query] = 20,
        token: Annotated[str, Header] = "anon",
    ) -> AnimalPage: ...

    # NON-pydantic endpoint: returns a plain dict.
    @get("/stats/{name}")
    async def get_stats(self, name: Annotated[str, PathParam]) -> dict: ...

    @post("/animal", concurrency=2)
    async def create_animal(self, payload: Annotated[dict, Body]) -> Animal: ...


def _group(doc: t.Any, name: str) -> t.Any:
    return next(g for g in doc.groups if g.name == name)


def _endpoint(doc: t.Any, group: str, name: str) -> t.Any:
    return next(e for e in _group(doc, group).endpoints if e.name == name)


# =========================================================================== inspect_api


class TestInspectApi:
    def test_groups_and_paths(self):
        doc = inspect_api(ZooAPI)
        assert doc.name == "ZooAPI"
        assert doc.base_url == "https://zoo.example.com/api"
        assert doc.version == gracy.__version__
        assert [g.name for g in doc.groups] == ["", "habitat"]

        root = _group(doc, "")
        assert root.path_prefix == ""
        assert {e.name for e in root.endpoints} == {
            "get_animal",
            "get_animal_strict",
            "list_animals",
            "get_stats",
            "create_animal",
        }
        assert _endpoint(doc, "", "get_animal").path == "/animal/{name}"
        assert _endpoint(doc, "", "get_animal").http_method == "GET"
        assert _endpoint(doc, "", "create_animal").http_method == "POST"

        habitat = _group(doc, "habitat")
        assert habitat.path_prefix == "/habitat"
        assert [e.name for e in habitat.endpoints] == ["get_one"]
        assert habitat.endpoints[0].path == "/habitat/{name}"  # prefix included

    def test_param_kinds_required_defaults(self):
        doc = inspect_api(ZooAPI)
        listing = _endpoint(doc, "", "list_animals")
        by_name = {p.name: p for p in listing.params}
        assert by_name["offset"].kind == "query"
        assert by_name["offset"].type_repr == "int"
        assert by_name["offset"].required is False
        assert by_name["offset"].default_repr == "0"
        assert by_name["limit"].default_repr == "20"
        assert by_name["token"].kind == "header"
        assert by_name["token"].default_repr == "'anon'"

        path_param = _endpoint(doc, "", "get_animal").params[0]
        assert path_param.kind == "path"
        assert path_param.type_repr == "str"
        assert path_param.required is True
        assert path_param.default_repr is None

        body_param = _endpoint(doc, "", "create_animal").params[0]
        assert body_param.kind == "body"
        assert body_param.type_repr == "dict"
        assert body_param.required is True

    def test_return_doc(self):
        doc = inspect_api(ZooAPI)
        optional = _endpoint(doc, "", "get_animal").returns
        assert optional.type_repr == "Animal | None"
        assert optional.is_optional is True
        assert optional.json_schema is not None
        assert optional.json_schema["properties"]["legs"]["type"] == "integer"

        required = _endpoint(doc, "", "get_animal_strict").returns
        assert required.type_repr == "Animal"
        assert required.is_optional is False

        raw = _endpoint(doc, "", "get_stats").returns
        assert raw.type_repr == "dict"
        assert raw.is_optional is False

    def test_status_map(self):
        doc = inspect_api(ZooAPI)
        none_map = _endpoint(doc, "", "get_animal").status_map
        assert len(none_map) == 1
        assert none_map[0].status == 404
        assert none_map[0].outcome == "returns None"

        raise_map = _endpoint(doc, "", "get_animal_strict").status_map
        assert raise_map[0].status == 404
        assert raise_map[0].outcome == "raises AnimalNotFound"
        assert raise_map[0].detail == AnimalNotFound.BASE_MESSAGE

        assert _endpoint(doc, "", "list_animals").status_map == []

    def test_config_summary(self):
        summary = inspect_api(ZooAPI).config_summary
        assert summary.retry == "3 attempts on 429/502/503, TimeoutError, backoff 0.5s x2"
        assert summary.throttle == ["5 req / 1s on .*"]
        assert summary.decoder == "PydanticDecoder"
        assert summary.concurrency is None
        assert summary.queue is None

    def test_endpoint_config_notes(self):
        doc = inspect_api(ZooAPI)
        strict_notes = _endpoint(doc, "", "get_animal_strict").config_notes
        assert "retry: 5 attempts on 500, wait 1s" in strict_notes
        assert "only 200 accepted (strict)" in strict_notes

        create_notes = _endpoint(doc, "", "create_animal").config_notes
        assert create_notes == ["concurrency: max 2 concurrent"]

        assert _endpoint(doc, "", "list_animals").config_notes == []

    def test_docstring_propagation(self):
        doc = inspect_api(ZooAPI)
        assert doc.title == "Zoo API client."
        assert "Talks to the Zoo service" in doc.description
        assert "{braces}" in doc.description

        ep = _endpoint(doc, "", "get_animal")
        assert ep.summary == "Fetch one animal."
        assert ep.description == "Returns None when the animal does not exist."
        assert _endpoint(doc, "", "list_animals").summary == ""

    def test_static_no_instantiation_or_side_effects(self):
        """Docs generation must work from the CLASS alone: no __init__, no build()."""

        class BoobyTrapped(ZooAPI):
            def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
                raise AssertionError("docs generation must NOT instantiate the client")

            async def build(self) -> t.Any:
                raise AssertionError("docs generation must NOT build the client")

        doc = inspect_api(BoobyTrapped)
        assert {e.name for e in _group(doc, "").endpoints} >= {"get_animal", "create_animal"}
        spec = to_openapi(BoobyTrapped)
        assert spec["openapi"] == "3.1.0"

    def test_showcase_pokeapi(self):
        doc = inspect_api(PokeAPI)
        assert doc.name == "PokeAPI"
        assert doc.base_url == "https://pokeapi.co/api/v2"
        assert [g.name for g in doc.groups] == ["", "berry"]

        mew = _endpoint(doc, "", "get_pokemon")
        assert mew.returns.type_repr == "Pokemon | None"
        assert mew.returns.is_optional is True
        assert mew.status_map[0].status == 404
        assert mew.status_map[0].outcome == "returns None"

        strict_ep = _endpoint(doc, "", "get_pokemon_strict")
        assert strict_ep.status_map[0].outcome == "raises PokemonNotFound"
        assert strict_ep.status_map[0].detail == PokemonNotFound.BASE_MESSAGE

        berry = _endpoint(doc, "berry", "get_one")
        assert berry.path == "/berry/{name}"
        assert berry.returns.type_repr == "dict"


# =========================================================================== to_openapi


@pytest.fixture(scope="module")
def spec() -> dict[str, t.Any]:
    return to_openapi(ZooAPI)


class TestOpenApi:
    def test_document_skeleton(self, spec: dict[str, t.Any]):
        assert spec["openapi"] == "3.1.0"
        assert spec["info"]["title"] == "Zoo API client."
        assert spec["info"]["version"] == gracy.__version__
        assert "Talks to the Zoo service" in spec["info"]["description"]
        assert spec["servers"] == [{"url": "https://zoo.example.com/api"}]

    def test_paths_keyed_by_template(self, spec: dict[str, t.Any]):
        assert "/animal/{name}" in spec["paths"]
        assert "/habitat/{name}" in spec["paths"]
        op = spec["paths"]["/animal/{name}"]["get"]
        assert op["operationId"] == "get_animal"
        assert spec["paths"]["/habitat/{name}"]["get"]["operationId"] == "habitat.get_one"
        assert spec["paths"]["/animal"]["post"]["operationId"] == "create_animal"

    def test_parameters_in_correct_location(self, spec: dict[str, t.Any]):
        params = {p["name"]: p for p in spec["paths"]["/animal"]["get"]["parameters"]}
        assert params["offset"]["in"] == "query"
        assert params["offset"]["required"] is False
        assert params["offset"]["schema"] == {"type": "integer", "default": 0}
        assert params["token"]["in"] == "header"
        assert params["token"]["schema"] == {"type": "string", "default": "anon"}

        path_params = spec["paths"]["/animal/{name}"]["get"]["parameters"]
        assert path_params == [
            {"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}
        ]

    def test_request_body(self, spec: dict[str, t.Any]):
        op = spec["paths"]["/animal"]["post"]
        body = op["requestBody"]
        assert body["required"] is True
        assert body["content"]["application/json"]["schema"]["type"] == "object"
        assert "parameters" not in op  # body param must not leak into parameters

    def test_responses_per_status(self, spec: dict[str, t.Any]):
        get_animal = spec["paths"]["/animal/{name}"]["get"]["responses"]
        assert get_animal["404"] == {"description": "returns null"}
        schema_200 = get_animal["200"]["content"]["application/json"]["schema"]
        assert schema_200 == {
            "anyOf": [{"$ref": "#/components/schemas/Animal"}, {"type": "null"}]
        }

        strict_responses = spec["paths"]["/animal/{name}/strict"]["get"]["responses"]
        assert strict_responses["404"]["description"] == (
            f"raises AnimalNotFound: {AnimalNotFound.BASE_MESSAGE}"
        )
        assert strict_responses["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Animal"
        }

        stats_schema = spec["paths"]["/stats/{name}"]["get"]["responses"]["200"]
        assert stats_schema["content"]["application/json"]["schema"]["type"] == "object"

    def test_components_schemas(self, spec: dict[str, t.Any]):
        schemas = spec["components"]["schemas"]
        assert "Animal" in schemas and "AnimalPage" in schemas
        assert schemas["Animal"]["properties"]["name"]["type"] == "string"
        assert schemas["Animal"]["required"] == ["id", "name", "legs"]

    def test_x_gracy_blocks(self, spec: dict[str, t.Any]):
        assert spec["x-gracy"]["retry"] == "3 attempts on 429/502/503, TimeoutError, backoff 0.5s x2"
        assert spec["x-gracy"]["throttle"] == ["5 req / 1s on .*"]
        assert spec["x-gracy"]["decoder"] == "PydanticDecoder"

        overrides = spec["paths"]["/animal/{name}/strict"]["get"]["x-gracy"]["overrides"]
        assert "retry: 5 attempts on 500, wait 1s" in overrides
        assert "only 200 accepted (strict)" in overrides
        assert "x-gracy" not in spec["paths"]["/animal"]["get"]

    def test_json_serializable(self, spec: dict[str, t.Any]):
        assert json.loads(to_json(ZooAPI)) == spec

    def test_showcase_pokeapi_openapi(self):
        spec = to_openapi(PokeAPI)
        assert spec["openapi"] == "3.1.0"
        assert spec["servers"] == [{"url": "https://pokeapi.co/api/v2"}]
        assert "/pokemon/{name}" in spec["paths"]
        assert "/berry/{name}" in spec["paths"]
        pokemon = spec["components"]["schemas"]["Pokemon"]
        assert set(pokemon["properties"]) == {"id", "name", "height", "weight"}
        assert spec["x-gracy"]["throttle"] == ["5 req / 1s on .*"]


# =========================================================================== YAML


class TestYaml:
    def test_roundtrip_zoo_exact(self):
        assert yaml.safe_load(to_yaml(ZooAPI)) == to_openapi(ZooAPI)

    def test_roundtrip_showcase_exact(self):
        assert yaml.safe_load(to_yaml(PokeAPI)) == to_openapi(PokeAPI)

    @pytest.mark.parametrize(
        "value",
        [
            "No animal named [{NAME}]: got {STATUS} from {URL}",  # colons + braces
            "/pokemon/{name}",  # path template braces
            "line one\nline two\n\nline four",  # multiline -> literal block
            "trailing newline\nkept\n",
            "ends with colon:",
            "3 attempts on 429/502/503, backoff 0.5s x2",
            "404",  # digits-only string must stay a string
            "",  # empty string
            "yes",  # YAML bool keyword must stay a string
            "null",
            "  leading and trailing  ",
            "with trailing spaces on a line   \nnext",
            'quotes "inside" and \'single\'',
            "unicode: café ☕",
        ],
    )
    def test_tricky_strings_roundtrip(self, value: str):
        data = {"key": value, "items": [value], "nested": {"deep": [{"v": value}]}}
        assert yaml.safe_load(yaml_dumps(data)) == data

    def test_tricky_keys_and_scalars(self):
        data = {
            "/pokemon/{name}": {"200": "ok", "default": True},
            "$ref": "#/components/schemas/X",
            "x-gracy": [1, 2.5, None, False, "on"],
            "empty-map": {},
            "empty-list": [],
        }
        assert yaml.safe_load(yaml_dumps(data)) == data

    def test_non_string_keys_rejected(self):
        with pytest.raises(TypeError, match="keys must be str"):
            yaml_dumps({404: "not-json-compatible"})


# =========================================================================== CLI


CLI_TARGET = "examples.v2_typing_showcase:PokeAPI"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gracy.docs", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )


class TestCli:
    def test_yaml_output_file(self, tmp_path: Path):
        out = tmp_path / "pokeapi.yaml"
        result = _run_cli(CLI_TARGET, "--format", "yaml", "-o", str(out))
        assert result.returncode == 0, result.stderr
        loaded = yaml.safe_load(out.read_text())
        assert loaded["openapi"] == "3.1.0"
        assert loaded["info"]["title"] == "PokeAPI"
        assert "/pokemon/{name}" in loaded["paths"]

    def test_json_stdout_parses(self):
        result = _run_cli(CLI_TARGET, "--format", "json")
        assert result.returncode == 0, result.stderr
        loaded = json.loads(result.stdout)
        assert loaded["openapi"] == "3.1.0"
        assert loaded["components"]["schemas"]["Pokemon"]["properties"]["name"]["type"] == "string"

    def test_default_format_is_yaml(self):
        result = _run_cli(CLI_TARGET)
        assert result.returncode == 0, result.stderr
        assert yaml.safe_load(result.stdout)["openapi"] == "3.1.0"

    def test_bad_target_module(self):
        result = _run_cli("no.such.module:Nope")
        assert result.returncode != 0
        assert "cannot load" in result.stderr
        assert "module.path:ClassName" in result.stderr

    def test_bad_target_shape(self):
        result = _run_cli("missing-colon")
        assert result.returncode != 0
        assert "module.path:ClassName" in result.stderr

    def test_target_not_a_gracy_class(self):
        result = _run_cli("examples.v2_typing_showcase:Pokemon")
        assert result.returncode != 0
        assert "not a Gracy subclass" in result.stderr

    def test_serve_requires_html(self):
        result = _run_cli(CLI_TARGET, "--format", "yaml", "--serve", "0")
        assert result.returncode != 0
        assert "--serve requires --format html" in result.stderr
