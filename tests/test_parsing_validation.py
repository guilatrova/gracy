"""on= parser map, status policies, custom validators, decode matrix, exception pickling."""

import asyncio
import pickle
import typing as t
from dataclasses import dataclass

import pydantic
import pytest

from gracy import (
    Gracy,
    GracyConfig,
    GracyConfigError,
    GracyParseFailed,
    GracyRequestFailed,
    GracyUserDefinedException,
    NonOkResponse,
    PydanticDecoder,
    RequestContext,
    Response,
    Retry,
    UnexpectedResponse,
    Validator,
    allow,
    get,
    raises,
    strict,
)
from gracy.parsing import decode_response
from gracy.testing import MockTransport

BASE = "https://parse.test"


# --------------------------------------------------------------------------- module-level types
# (pickle + pydantic need importable / stable classes)


class PokemonNotFound(GracyUserDefinedException):
    BASE_MESSAGE = "Pokemon {NAME} was not found ({STATUS})"


class Fruit(pydantic.BaseModel):
    name: str
    color: str


@dataclass
class Point:
    x: int
    y: int


class Exotic:
    """Not a dataclass, not pydantic, not json-like - undecodable without a Decoder."""


class TeapotError(Exception):
    pass


class TeapotValidator(Validator):
    def check(self, response: Response) -> None:
        if response.json().get("teapot"):
            raise TeapotError("short and stout")


def make_response(body: bytes = b"{}", status: int = 200, url: str = f"{BASE}/x") -> Response:
    return Response(
        status=status,
        headers=(("content-type", "application/json"),),
        body=body,
        url=url,
        elapsed=0.01,
    )


# --------------------------------------------------------------------------- on= parser map


async def test_on_map_status_literal_none_returns_none():
    class Api(Gracy):
        base_url = BASE

        @get("/thing", on={404: None})
        async def thing(self) -> dict: ...

    async with Api(transport=MockTransport({f"{BASE}/thing": 404})) as api:
        assert await api.thing() is None  # no NonOkResponse raised either


async def test_on_map_callable_parser():
    class Api(Gracy):
        base_url = BASE

        @get("/pokemon/{name}", on={200: lambda r: r.json()["name"]})
        async def get_pokemon(self, name) -> dict: ...

    transport = MockTransport({f"{BASE}/pokemon/*": {"name": "mew", "id": 151}})
    async with Api(transport=transport) as api:
        assert await api.get_pokemon("mew") == "mew"


async def test_on_map_raises_user_defined_exception_with_formatted_message():
    class Api(Gracy):
        base_url = BASE

        @get("/pokemon/{name}", on={404: raises(PokemonNotFound)})
        async def get_pokemon(self, name) -> dict: ...

    async with Api(transport=MockTransport({f"{BASE}/pokemon/*": 404})) as api:
        with pytest.raises(PokemonNotFound) as exc_info:
            await api.get_pokemon("missingno")

    exc = exc_info.value
    # {NAME} and {STATUS} are resolved in BASE_MESSAGE
    assert str(exc) == "Pokemon missingno was not found (404)"
    assert exc.response is not None and exc.response.status == 404
    assert isinstance(exc.context, RequestContext)
    assert exc.context.endpoint_args == {"name": "missingno"}


async def test_on_map_default_key_applies_to_unlisted_statuses():
    class Api(Gracy):
        base_url = BASE

        @get(
            "/d/{code}",
            on={200: lambda r: "listed", "default": lambda r: f"fallback-{r.status}"},
        )
        async def d(self, code) -> dict: ...

    transport = MockTransport({f"{BASE}/d/200": {"x": 1}, f"{BASE}/d/500": (500, {"err": 1})})
    async with Api(transport=transport) as api:
        assert await api.d(200) == "listed"  # exact status key wins
        assert await api.d(500) == "fallback-500"  # unlisted status -> "default" (and no raise)


async def test_parser_exception_becomes_parse_failed_with_cause():
    class Api(Gracy):
        base_url = BASE

        @get("/thing", on={200: lambda r: r.json()["missing-key"]})
        async def thing(self) -> dict: ...

    async with Api(transport=MockTransport({f"{BASE}/thing": {"other": 1}})) as api:
        with pytest.raises(GracyParseFailed) as exc_info:
            await api.thing()

    assert isinstance(exc_info.value.__cause__, KeyError)
    assert exc_info.value.response is not None and exc_info.value.response.status == 200


# --------------------------------------------------------------------------- status policies


async def test_strict_policy_fails_200_with_unexpected_response():
    class Api(Gracy):
        base_url = BASE

        @get("/create", status_policy=strict(201))
        async def create_ok(self) -> dict: ...

        @get("/create-wrong", status_policy=strict(201))
        async def create_wrong(self) -> dict: ...

    transport = MockTransport(
        {
            f"{BASE}/create-wrong": (200, {"ok": True}),  # 200 is NOT allowed under strict(201)
            f"{BASE}/create": (201, {"id": 7}),
        }
    )
    async with Api(transport=transport) as api:
        with pytest.raises(UnexpectedResponse) as exc_info:
            await api.create_wrong()
        assert exc_info.value.expected == (201,)
        assert exc_info.value.response is not None and exc_info.value.response.status == 200

        assert await api.create_ok() == {"id": 7}  # the listed code passes


async def test_allow_policy_passes_404_and_decodes_via_on_map():
    class Api(Gracy):
        base_url = BASE

        @get("/maybe", status_policy=allow(404), on={404: "gone"})
        async def maybe(self) -> dict: ...

        @get("/maybe-body", status_policy=allow(404))
        async def maybe_body(self) -> dict: ...

    transport = MockTransport(
        {
            f"{BASE}/maybe-body": (404, {"error": "not found"}),
            f"{BASE}/maybe": (404, {"error": "not found"}),
        }
    )
    async with Api(transport=transport) as api:
        # No NonOkResponse raised; the on= literal is returned.
        assert await api.maybe() == "gone"
        # Without an on= entry the 404 body decodes into the return annotation.
        assert await api.maybe_body() == {"error": "not found"}


# --------------------------------------------------------------------------- custom validators


async def test_custom_validator_domain_exception_propagates():
    class Api(Gracy):
        base_url = BASE

        @get("/tea", validators=TeapotValidator())
        async def tea(self) -> dict: ...

    async with Api(transport=MockTransport({f"{BASE}/tea": {"teapot": True}})) as api:
        with pytest.raises(TeapotError):
            await api.tea()


async def test_custom_validator_exception_matches_retry_on_and_retries():
    calls = {"n": 0}

    def teapot_then_ok(spec):
        calls["n"] += 1
        if calls["n"] <= 2:
            return {"teapot": True}
        return {"ok": True}

    class Api(Gracy):
        base_url = BASE

        @get(
            "/tea",
            validators=TeapotValidator(),
            retry=Retry(on=TeapotError, attempts=3, wait=0),
        )
        async def tea(self) -> dict: ...

    transport = MockTransport({f"{BASE}/tea": teapot_then_ok})
    async with Api(transport=transport) as api:
        result = await asyncio.wait_for(api.tea(), 15)

    assert result == {"ok": True}
    assert calls["n"] == 3  # initial attempt + 2 retries triggered by TeapotError


# --------------------------------------------------------------------------- decode matrix


def test_decode_dict():
    assert decode_response(make_response(b'{"a": 1}'), dict) == {"a": 1}


def test_decode_list():
    assert decode_response(make_response(b"[1, 2, 3]"), list) == [1, 2, 3]


def test_decode_str_returns_text():
    assert decode_response(make_response(b'{"a": 1}'), str) == '{"a": 1}'


def test_decode_bytes_returns_body():
    assert decode_response(make_response(b'{"a": 1}'), bytes) == b'{"a": 1}'


def test_decode_response_annotation_is_passthrough():
    response = make_response(b'{"a": 1}')
    assert decode_response(response, Response) is response
    assert decode_response(response, None) is response  # missing annotation too


def test_decode_optional_pydantic_model():
    response = make_response(b'{"name": "kiwi", "color": "green"}')
    fruit = decode_response(response, t.Optional[Fruit], PydanticDecoder())
    assert isinstance(fruit, Fruit)
    assert (fruit.name, fruit.color) == ("kiwi", "green")


def test_decode_list_of_pydantic_models_via_type_adapter():
    # Regression: list[Model] must reach the Decoder (TypeAdapter), not the
    # json-like fast path that returned raw dicts.
    body = b'[{"name": "apple", "color": "red"}, {"name": "pear", "color": "green"}]'
    fruits = decode_response(make_response(body), list[Fruit], PydanticDecoder())
    assert [type(f) for f in fruits] == [Fruit, Fruit]
    assert [f.name for f in fruits] == ["apple", "pear"]


def test_decode_list_of_dicts_stays_builtin_even_with_decoder():
    body = b'[{"name": "apple"}]'
    assert decode_response(make_response(body), list[dict], PydanticDecoder()) == [{"name": "apple"}]


def test_decode_plain_dataclass_without_decoder():
    point = decode_response(make_response(b'{"x": 1, "y": 2}'), Point)
    assert point == Point(x=1, y=2)


def test_decode_exotic_type_without_decoder_raises_config_error():
    with pytest.raises(GracyConfigError, match="decode"):
        decode_response(make_response(b"{}"), Exotic)


async def test_decode_pydantic_model_through_client_config():
    class Api(Gracy):
        base_url = BASE
        config = GracyConfig(decoder=PydanticDecoder())

        @get("/fruit/{name}")
        async def get_fruit(self, name) -> Fruit: ...

        @get("/fruits")
        async def list_fruits(self) -> list[Fruit]: ...

    transport = MockTransport(
        {
            f"{BASE}/fruits": [{"name": "apple", "color": "red"}],
            f"{BASE}/fruit/*": {"name": "kiwi", "color": "green"},
        }
    )
    async with Api(transport=transport) as api:
        fruit = await api.get_fruit("kiwi")
        assert isinstance(fruit, Fruit) and fruit.name == "kiwi"
        fruits = await api.list_fruits()
        assert [type(f) for f in fruits] == [Fruit] and fruits[0].color == "red"


# --------------------------------------------------------------------------- pickling


def test_pickle_gracy_request_failed_preserves_attrs():
    exc = GracyRequestFailed("https://x/y", ValueError("boom"))
    restored = pickle.loads(pickle.dumps(exc))
    assert type(restored) is GracyRequestFailed
    assert restored.url == "https://x/y"
    assert isinstance(restored.original_exc, ValueError)
    assert str(restored.original_exc) == "boom"
    assert str(restored) == str(exc)


def test_pickle_non_ok_response_preserves_response():
    response = make_response(b'{"err": 1}', status=500)
    exc = NonOkResponse("https://x returned 500", response)
    restored = pickle.loads(pickle.dumps(exc))
    assert type(restored) is NonOkResponse
    assert str(restored) == "https://x returned 500"
    assert restored.response is not None
    assert restored.response.status == 500
    assert restored.response.body == b'{"err": 1}'


def test_pickle_unexpected_response_preserves_expected_tuple():
    response = make_response(status=200)
    exc = UnexpectedResponse("expected 201", response, expected=(201, 202))
    restored = pickle.loads(pickle.dumps(exc))
    assert type(restored) is UnexpectedResponse
    assert restored.expected == (201, 202)
    assert restored.response is not None and restored.response.status == 200
    assert str(restored) == "expected 201"


def test_pickle_user_defined_subclass_preserves_identity_and_message():
    context = RequestContext(
        method="GET",
        url=f"{BASE}/pokemon/mew",
        uurl=f"{BASE}/pokemon/{{name}}",
        endpoint="/pokemon/{name}",
        endpoint_args={"name": "mew"},
    )
    exc = PokemonNotFound(context, make_response(status=404))
    assert str(exc) == "Pokemon mew was not found (404)"

    restored = pickle.loads(pickle.dumps(exc))
    assert type(restored) is PokemonNotFound  # v1 lost the subclass here
    assert isinstance(restored, GracyUserDefinedException)
    assert str(restored) == "Pokemon mew was not found (404)"
    assert restored.response is not None and restored.response.status == 404
    assert restored.context is not None and restored.context.endpoint_args == {"name": "mew"}
