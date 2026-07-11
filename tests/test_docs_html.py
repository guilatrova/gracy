"""gracy.docs HTML renderer: self-contained static page + CLI --format html/--serve.

Renders the real examples/v2_typing_showcase.py PokeAPI plus a purpose-built
class exercising every HTTP method, HTML-escaping, and schema collapsibles.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel

import gracy
from gracy import Body, Gracy, GracyNamespace, Path as PathParam, Query, delete, get, patch, post, put, raises
from gracy.docs import inspect_api, to_html
from gracy.docs.html import render

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.v2_typing_showcase import PokeAPI, PokemonNotFound  # noqa: E402


@pytest.fixture(scope="module")
def html() -> str:
    return to_html(PokeAPI)


# --------------------------------------------------------------------------- escaping fixture API


class Gadget(BaseModel):
    id: int
    name: str


class SpicyError(gracy.GracyUserDefinedException):
    BASE_MESSAGE = "bad <thing> & {URL} broke"


class ToolsNamespace(GracyNamespace):
    path_prefix = "/tools"

    @get("/{name}")
    async def get_tool(self, name: Annotated[str, PathParam]) -> dict: ...


class GadgetAPI(Gracy):
    """Gadget <b>API</b> & friends.

    Handles <script>alert(1)</script> style descriptions safely."""

    base_url = "https://gadgets.example.com/api?a=1&b=2"

    tools = ToolsNamespace()

    @get("/gadget/{gid}", on={404: raises(SpicyError)})
    async def get_gadget(self, gid: Annotated[int, PathParam]) -> Gadget: ...

    @post("/gadget")
    async def create_gadget(self, payload: Annotated[dict, Body]) -> Gadget: ...

    @put("/gadget/{gid}")
    async def replace_gadget(self, gid: Annotated[int, PathParam], payload: Annotated[dict, Body]) -> Gadget: ...

    @patch("/gadget/{gid}")
    async def tweak_gadget(self, gid: Annotated[int, PathParam], payload: Annotated[dict, Body]) -> Gadget: ...

    @delete("/gadget/{gid}")
    async def drop_gadget(self, gid: Annotated[int, PathParam]) -> None: ...

    @get("/gadget")
    async def list_gadgets(self, limit: Annotated[int, Query] = 20, tag: Annotated[str, Query] = "all") -> list: ...


class Opaque:  # not a pydantic model, not TypeAdapter-able
    def __init__(self, raw: object) -> None:
        self.raw = raw


class OpaqueAPI(Gracy):
    base_url = "https://opaque.example.com"

    @get("/thing")
    async def get_thing(self) -> Opaque: ...


# =========================================================================== page structure


class TestPageStructure:
    def test_starts_with_doctype(self, html: str):
        assert html.startswith("<!doctype html>")

    def test_single_self_contained_page(self, html: str):
        assert "<style>" in html and "<script>" in html
        # no external assets: every src/href is a local anchor
        for url in re.findall(r'(?:src|href)="([^"]+)"', html):
            assert url.startswith("#"), f"external asset leaked: {url}"
        assert "http://cdn" not in html and "https://cdn" not in html

    def test_title_and_header(self, html: str):
        assert "<title>" in html
        assert "PokeAPI" in html  # class name (title falls back to docstring first line)
        assert f"v{gracy.__version__}" in html
        assert "https://pokeapi.co/api/v2" in html

    def test_every_endpoint_name_present(self, html: str):
        for name in ("get_pokemon", "get_pokemon_strict", "list_pokemon", "get_one"):
            assert name in html

    def test_full_paths_present_with_literal_braces(self, html: str):
        # literal template paths must survive rendering (guards f-string brace bugs)
        assert "/pokemon/{name}" in html
        assert "/berry/{name}" in html  # namespace prefix included
        assert 'data-copy="/pokemon"' in html

    def test_anchor_ids(self, html: str):
        assert 'id="op-get_pokemon"' in html
        assert 'id="op-berry-get_one"' in html
        assert 'href="#op-berry-get_one"' in html  # sidebar nav points at the card

    def test_client_policies_card(self, html: str):
        assert "Client policies" in html
        assert "3 attempts on 429/502/503, TimeoutError, backoff 0.5s x2" in html
        assert "5 req / 1s on .*" in html
        assert "PydanticDecoder" in html


# =========================================================================== method chips


class TestMethodChips:
    def test_get_chip_on_pokeapi(self, html: str):
        assert "method-get" in html

    def test_all_method_chip_classes(self):
        page = to_html(GadgetAPI)
        for cls in ("method-get", "method-post", "method-put", "method-patch", "method-delete"):
            assert cls in page


# =========================================================================== escaping


class TestEscaping:
    def test_base_message_detail_present(self, html: str):
        assert PokemonNotFound.BASE_MESSAGE in html  # "No pokemon named [{NAME}] ..."
        assert "raises PokemonNotFound" in html

    def test_html_in_user_strings_is_escaped(self):
        page = to_html(GadgetAPI)
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
        assert "Gadget &lt;b&gt;API&lt;/b&gt; &amp; friends." in page
        assert "bad &lt;thing&gt; &amp; {URL} broke" in page  # BASE_MESSAGE escaped, braces intact
        assert "https://gadgets.example.com/api?a=1&amp;b=2" in page


# =========================================================================== params table


class TestParamsTable:
    def test_param_cells(self, html: str):
        assert "<td><code>name</code></td>" in html
        assert "<td><code>limit</code></td>" in html
        assert "<td><code>offset</code></td>" in html
        # defaults column
        assert "<td><code>20</code></td>" in html
        assert "<td><code>0</code></td>" in html
        # kinds
        assert 'kind-path">path</span>' in html
        assert 'kind-query">query</span>' in html

    def test_required_dot_only_for_required(self, html: str):
        # name (path) is required, offset/limit have defaults
        assert 'class="req-dot"' in html


# =========================================================================== responses + schema


class TestResponsesAndSchema:
    def test_status_rows(self, html: str):
        assert 'class="status s4">404</span>' in html
        assert "returns None" in html
        assert "raises PokemonNotFound" in html
        assert 'class="status s2">200</span>' in html

    def test_collapsible_pokemon_schema(self, html: str):
        assert "<details" in html and "</details>" in html
        assert "JSON Schema" in html
        # Pokemon properties rendered inside the collapsible schema
        for prop in ("&quot;height&quot;", "&quot;weight&quot;", "&quot;name&quot;", "&quot;id&quot;"):
            assert prop in html
        assert "&quot;properties&quot;" in html

    def test_one_collapsible_per_derivable_schema(self, html: str):
        doc = inspect_api(PokeAPI)
        derivable = sum(e.returns.json_schema is not None for g in doc.groups for e in g.endpoints)
        assert html.count("<details") == derivable == 4  # Pokemon x2, ResourcePage, dict

    def test_no_collapsible_when_schema_underivable(self):
        page = to_html(OpaqueAPI)
        assert "<details" not in page
        assert "Returns <code>Opaque</code>" in page


# =========================================================================== usage snippet


class TestUsageSnippet:
    def test_async_with_and_method_calls(self, html: str):
        assert "async with PokeAPI() as api:" in html
        assert "await api.get_pokemon(name=&quot;example&quot;)" in html
        assert "await api.berry.get_one(name=&quot;example&quot;)" in html

    def test_optional_params_left_out(self, html: str):
        assert "await api.list_pokemon()" in html  # offset/limit have defaults

    def test_example_values_by_type(self):
        page = to_html(GadgetAPI)
        assert "await api.get_gadget(gid=0)" in page  # int -> 0
        assert "await api.create_gadget(payload={})" in page  # dict -> {}


# =========================================================================== theme + determinism


class TestThemeAndDeterminism:
    def test_dark_mode_css_marker(self, html: str):
        assert "prefers-color-scheme: dark" in html
        assert 'data-theme="dark"' in html  # manual override selector
        assert 'id="theme-toggle"' in html

    def test_deterministic_output(self):
        assert to_html(PokeAPI) == to_html(PokeAPI)

    def test_render_is_the_public_entrypoint(self):
        assert render(inspect_api(PokeAPI)) == to_html(PokeAPI)


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
    def test_html_output_file(self, tmp_path: Path):
        out = tmp_path / "docs.html"
        result = _run_cli(CLI_TARGET, "--format", "html", "-o", str(out))
        assert result.returncode == 0, result.stderr
        reread = subprocess.run(
            [sys.executable, "-c", f"print(open({str(out)!r}, encoding='utf-8').read())"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert reread.returncode == 0, reread.stderr
        page = reread.stdout
        assert page.startswith("<!doctype html>")
        assert "<title>PokeAPI — API docs</title>" in page
        assert "/pokemon/{name}" in page

    def test_html_stdout(self):
        result = _run_cli(CLI_TARGET, "--format", "html")
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("<!doctype html>")
        assert "get_pokemon_strict" in result.stdout

    def test_serve_end_to_end(self):
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "gracy.docs", CLI_TARGET, "--format", "html", "--serve", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=REPO_ROOT,
        )
        try:
            assert proc.stdout is not None
            deadline = time.monotonic() + 30
            line = ""
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if "Serving docs at" in line:
                    break
            match = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
            assert match, f"no serve banner, got: {line!r}"
            with urllib.request.urlopen(f"http://127.0.0.1:{match.group(1)}/", timeout=10) as resp:
                body = resp.read().decode("utf-8")
            assert body.startswith("<!doctype html>")
            assert "PokeAPI" in body
        finally:
            proc.terminate()
            proc.wait(timeout=10)
