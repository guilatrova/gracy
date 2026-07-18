"""Endpoint surface: @get/@post decorators, markers, URL helpers, BaseEndpoint,
return-annotation decoding, namespaces, and api.request() ad-hoc calls."""

import json
import typing as t

import pydantic
import pytest

from gracy import (
    BaseEndpoint,
    Body,
    Gracy,
    GracyConfig,
    GracyConfigError,
    GracyNamespace,
    Header,
    MockTransport,
    Path,
    PydanticDecoder,
    Query,
    Response,
    get,
    post,
)
from gracy.endpoints import append_query, format_url, join_url

# NOTE: no `from __future__ import annotations` here on purpose - gracy resolves
# endpoint annotations via typing.get_type_hints at build() time, and eager
# annotations let tests annotate with classes defined inside test functions.


class Item(pydantic.BaseModel):
    name: str
    count: int


# --------------------------------------------------------------------------- URL helpers


class TestJoinUrl:
    def test_base_and_path_slash_normalization(self):
        assert join_url("https://a.test/", "/b") == "https://a.test/b"
        assert join_url("https://a.test", "b") == "https://a.test/b"
        assert join_url("https://a.test//", "//b/c") == "https://a.test/b/c"

    def test_empty_path_returns_base(self):
        assert join_url("https://a.test", "") == "https://a.test"

    def test_empty_base_returns_path(self):
        assert join_url("", "/b") == "/b"

    def test_absolute_http_paths_pass_through(self):
        assert join_url("https://a.test", "https://other.test/x") == "https://other.test/x"
        assert join_url("https://a.test", "http://other.test/x") == "http://other.test/x"

    def test_absolute_pass_through_is_scheme_case_insensitive(self):
        assert join_url("https://a.test", "HTTP://other.test/x") == "HTTP://other.test/x"


class TestFormatUrl:
    def test_placeholders_match_args_case_insensitively(self):
        assert format_url("/pokemon/{NAME}", {"name": "mew"}) == "/pokemon/mew"
        assert format_url("/pokemon/{name}", {"NAME": "mew"}) == "/pokemon/mew"

    def test_values_are_stringified(self):
        assert format_url("/items/{ID}", {"id": 42}) == "/items/42"

    def test_missing_arg_raises(self):
        with pytest.raises(GracyConfigError, match=r"missing args.*NAME"):
            format_url("/pokemon/{NAME}", {})

    def test_unknown_arg_raises(self):
        with pytest.raises(GracyConfigError, match=r"unknown args.*bogus"):
            format_url("/pokemon/{NAME}", {"name": "mew", "bogus": 1})

    def test_missing_and_unknown_both_reported(self):
        with pytest.raises(GracyConfigError) as excinfo:
            format_url("/pokemon/{NAME}", {"other": "x"})
        assert "missing args" in str(excinfo.value)
        assert "unknown args" in str(excinfo.value)


class TestAppendQuery:
    def test_skips_none_values(self):
        assert append_query("/x", {"a": 1, "b": None}) == "/x?a=1"

    def test_all_none_returns_url_unchanged(self):
        assert append_query("/x", {"b": None}) == "/x"

    def test_uses_ampersand_when_query_already_present(self):
        assert append_query("/x?a=1", {"b": 2}) == "/x?a=1&b=2"

    def test_sequences_encode_with_doseq(self):
        assert append_query("/x", {"tag": ["a", "b"]}) == "/x?tag=a&tag=b"


# --------------------------------------------------------------------------- decorators


class TestDecoratorErrors:
    def test_unknown_config_kwarg_raises_at_decoration(self):
        with pytest.raises(GracyConfigError, match="Unknown @get"):

            class Bad(Gracy):
                @get("/x", bogus=1)
                async def x(self) -> dict: ...

    async def test_path_marker_without_placeholder_raises_at_build(self, make_client):
        class Bad(Gracy):
            base_url = "https://api.test"

            @get("/items")
            async def items(self, name: t.Annotated[str, Path]) -> dict: ...

        with pytest.raises(GracyConfigError, match="marked Path"):
            await make_client(Bad, transport=MockTransport())


class TestDecoratedEndpoints:
    async def test_path_placeholder_matches_param_case_insensitively(self, make_client):
        transport = MockTransport({"https://api.test/items/*": {"found": True}})

        class API(Gracy):
            base_url = "https://api.test"

            @get("/items/{ID}")
            async def item(self, id: str) -> dict: ...

        api = await make_client(API, transport=transport)
        assert await api.item("42") == {"found": True}
        assert transport.calls[0].url == "https://api.test/items/42"
        assert transport.calls[0].method == "GET"

    async def test_defaults_applied_and_none_query_skipped(self, test_server, make_client):
        class API(Gracy):
            base_url = test_server

            @get("/echo/{NAME}")
            async def echo(
                self,
                name: str,
                limit: int = 5,
                page: str | None = None,
            ) -> dict: ...

        api = await make_client(API)
        res = await api.echo("pika")
        assert res["path"] == "/echo/pika"
        assert res["query"] == {"limit": "5"}  # page=None skipped, default limit encoded

        res = await api.echo("pika", limit=9, page="two")
        assert res["query"] == {"limit": "9", "page": "two"}

    async def test_annotated_path_query_header_markers(self, test_server, make_client):
        class API(Gracy):
            base_url = test_server

            @get("/echo/{ID}")
            async def fetch(
                self,
                id: t.Annotated[str, Path],
                token: t.Annotated[str, Header],
                q: t.Annotated[str, Query] = "default-q",
            ) -> dict: ...

        api = await make_client(API)
        res = await api.fetch("abc", token="secret-123")
        assert res["path"] == "/echo/abc"
        assert res["query"] == {"q": "default-q"}
        assert res["headers"]["token"] == "secret-123"

    async def test_body_marker_posts_json(self, test_server, make_client):
        class API(Gracy):
            base_url = test_server

            @post("/status/{CODE}")
            async def push(
                self,
                code: t.Annotated[int, Path],
                payload: t.Annotated[dict, Body],
            ) -> dict: ...

        api = await make_client(API)
        res = await api.push(200, {"hello": "world", "n": 1})
        assert res["status"] == 200
        assert json.loads(res["body"]) == {"hello": "world", "n": 1}

    async def test_body_marker_sets_json_content_type(self, make_client):
        transport = MockTransport({"https://api.test/*": {"ok": True}})

        class API(Gracy):
            base_url = "https://api.test"

            @post("/things")
            async def create(self, payload: t.Annotated[dict, Body]) -> dict: ...

        api = await make_client(API, transport=transport)
        await api.create({"a": 1})
        spec = transport.calls[0]
        assert spec.method == "POST"
        assert spec.content == json.dumps({"a": 1}).encode()
        assert ("content-type", "application/json") in spec.headers

    async def test_missing_required_path_arg_raises(self, make_client):
        class API(Gracy):
            base_url = "https://api.test"

            @get("/items/{ID}")
            async def item(self, id: str) -> dict: ...

        api = await make_client(API, transport=MockTransport({"*": {}}))
        with pytest.raises(GracyConfigError, match="missing args"):
            await api.item()


# --------------------------------------------------------------------------- return-annotation decoding


class TestReturnAnnotationDecoding:
    @pytest.fixture
    async def api(self, make_client):
        transport = MockTransport({"https://api.test/*": {"ok": True}})

        class API(Gracy):
            base_url = "https://api.test"

            @get("/thing")
            async def as_dict(self) -> dict: ...

            @get("/thing")
            async def as_str(self) -> str: ...

            @get("/thing")
            async def as_bytes(self) -> bytes: ...

            @get("/thing")
            async def as_response(self) -> Response: ...

            @get("/thing")
            async def untyped(self): ...

        return await make_client(API, transport=transport)

    async def test_dict_annotation_decodes_json(self, api):
        assert await api.as_dict() == {"ok": True}

    async def test_str_annotation_returns_text(self, api):
        result = await api.as_str()
        assert isinstance(result, str)
        assert json.loads(result) == {"ok": True}

    async def test_bytes_annotation_returns_raw_body(self, api):
        result = await api.as_bytes()
        assert isinstance(result, bytes)
        assert json.loads(result) == {"ok": True}

    async def test_response_annotation_passes_through(self, api):
        result = await api.as_response()
        assert isinstance(result, Response)
        assert result.status == 200
        assert result.json() == {"ok": True}

    async def test_missing_annotation_returns_response(self, api):
        result = await api.untyped()
        assert isinstance(result, Response)

    async def test_pydantic_model_via_client_decoder(self, make_client):
        transport = MockTransport({"https://api.test/item": {"name": "potion", "count": 3}})

        class API(Gracy):
            base_url = "https://api.test"
            config = GracyConfig(decoder=PydanticDecoder())

            @get("/item")
            async def item(self) -> Item: ...

        api = await make_client(API, transport=transport)
        result = await api.item()
        assert isinstance(result, Item)
        assert result.name == "potion"
        assert result.count == 3

    async def test_unknown_type_without_decoder_raises_config_error(self, make_client):
        class NotDecodable:
            pass

        class API(Gracy):
            base_url = "https://api.test"

            @get("/item")
            async def item(self) -> NotDecodable: ...

        api = await make_client(API, transport=MockTransport({"*": {"a": 1}}))
        with pytest.raises(GracyConfigError, match="Plug a Decoder"):
            await api.item()


# --------------------------------------------------------------------------- namespaces


class BerryNamespace(GracyNamespace):
    path_prefix = "/berry"
    config = GracyConfig(on={200: lambda r: ("ns", r.json())})

    @get("/{name}")
    async def get_one(self, name: str) -> dict: ...


class PlainNamespace(GracyNamespace):
    path_prefix = "/plain"

    @get("/{name}")
    async def get_one(self, name: str) -> dict: ...


class NsAPI(Gracy):
    base_url = "https://ns.test"
    config = GracyConfig(on={200: lambda r: ("client", r.json())})

    berry = BerryNamespace()
    plain = PlainNamespace()

    @get("/top/{name}")
    async def top(self, name: str) -> dict: ...


class TestNamespaces:
    def _transport(self):
        return MockTransport({"https://ns.test/*": {"ok": True}})

    async def test_path_prefix_is_prepended(self, make_client):
        transport = self._transport()
        api = await make_client(NsAPI, transport=transport)
        await api.berry.get_one("cheri")
        assert transport.calls[0].url == "https://ns.test/berry/cheri"
        assert transport.calls[0].uurl == "https://ns.test/berry/{name}"

    async def test_namespace_config_wins_over_client_config(self, make_client):
        api = await make_client(NsAPI, transport=self._transport())
        tag, body = await api.berry.get_one("cheri")
        assert tag == "ns"
        assert body == {"ok": True}

    async def test_client_config_applies_when_namespace_has_none(self, make_client):
        api = await make_client(NsAPI, transport=self._transport())
        # plain namespace has no config -> client-level `on` action layers in
        tag, _ = await api.plain.get_one("cheri")
        assert tag == "client"
        # and a top-level endpoint uses the client config too
        tag, _ = await api.top("x")
        assert tag == "client"

    async def test_class_access_returns_the_namespace_itself(self):
        assert isinstance(NsAPI.berry, BerryNamespace)

    async def test_bound_namespace_cached_per_instance(self, make_client):
        api = await make_client(NsAPI, transport=self._transport())
        assert api.berry is api.berry

    async def test_two_clients_do_not_share_bound_state(self, make_client):
        t1, t2 = self._transport(), self._transport()
        api1 = await make_client(NsAPI, transport=t1)
        api2 = await make_client(NsAPI, transport=t2)

        assert api1.berry is not api2.berry

        await api1.berry.get_one("one")
        await api2.berry.get_one("two")

        assert [c.url for c in t1.calls] == ["https://ns.test/berry/one"]
        assert [c.url for c in t2.calls] == ["https://ns.test/berry/two"]

    async def test_namespace_placeholder_args_case_insensitive(self, make_client):
        transport = MockTransport({"https://ns.test/*": {"ok": True}})

        class UpperNS(GracyNamespace):
            path_prefix = "/upper"

            @get("/{THING}")
            async def get_thing(self, thing: str) -> dict: ...

        class API(Gracy):
            base_url = "https://ns.test"
            config = GracyConfig()
            upper = UpperNS()

        api = await make_client(API, transport=transport)
        await api.upper.get_thing("rock")
        assert transport.calls[0].url == "https://ns.test/upper/rock"


# --------------------------------------------------------------------------- BaseEndpoint + api.request


class PokeEndpoint(BaseEndpoint):
    ECHO = "/echo/{NAME}"
    STATUS = "/status/{CODE}"


class TestBaseEndpoint:
    def test_is_str_enum_rendering_its_value(self):
        assert isinstance(PokeEndpoint.ECHO, str)
        assert str(PokeEndpoint.ECHO) == "/echo/{NAME}"

    async def test_used_via_api_request(self, test_server, make_client):
        class API(Gracy):
            base_url = test_server

        api = await make_client(API)
        res = await api.request("GET", PokeEndpoint.ECHO, {"name": "mew"}, decode_as=dict)
        assert res["path"] == "/echo/mew"


class TestAdHocRequest:
    @pytest.fixture
    async def api(self, test_server, make_client):
        class API(Gracy):
            base_url = test_server

        return await make_client(API)

    async def test_path_formatting_params_and_decode_as(self, api):
        res = await api.request(
            "GET",
            "/echo/{THING}",
            {"thing": "abc"},
            params={"q": "1", "skip": None},
            decode_as=dict,
        )
        assert res["path"] == "/echo/abc"
        assert res["query"] == {"q": "1"}  # None param skipped

    async def test_decode_as_str_returns_text(self, api):
        res = await api.request("GET", "/echo/{X}", {"x": "y"}, decode_as=str)
        assert isinstance(res, str)
        assert json.loads(res)["path"] == "/echo/y"

    async def test_no_decode_as_returns_response(self, api):
        res = await api.request("GET", "/echo/{X}", {"x": "y"})
        assert isinstance(res, Response)
        assert res.status == 200

    async def test_json_body_posted(self, api):
        res = await api.request(
            "POST", "/status/{CODE}", {"code": 200}, json={"a": 1}, decode_as=dict
        )
        assert res["status"] == 200
        assert json.loads(res["body"]) == {"a": 1}

    async def test_headers_forwarded(self, api):
        res = await api.request(
            "GET", "/echo/{X}", {"x": "h"}, headers={"X-Custom": "v1"}, decode_as=dict
        )
        assert res["headers"]["x-custom"] == "v1"

    async def test_missing_path_arg_raises(self, api):
        with pytest.raises(GracyConfigError, match="missing args"):
            await api.request("GET", "/echo/{THING}")

    async def test_unknown_path_arg_raises(self, api):
        with pytest.raises(GracyConfigError, match="unknown args"):
            await api.request("GET", "/echo/{THING}", {"thing": "a", "extra": "b"})
