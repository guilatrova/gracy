"""CLI: generate docs for a Gracy subclass.

    python -m gracy.docs 'module.path:ClassName' [--format yaml|json|html]
                         [-o out.file] [--serve PORT]

The target module is imported with the current working directory on sys.path,
so 'myapp.api:MyClient' works from your project root. --serve (html only)
writes the page to a temp dir and serves it with http.server.
"""

from __future__ import annotations

import argparse
import http.server
import importlib
import os
import sys
import tempfile
import typing as t
from functools import partial
from pathlib import Path

__all__ = ["main"]


def _load_target(target: str) -> type:
    module_name, sep, class_name = target.partition(":")
    if not sep or not module_name or not class_name:
        raise ValueError("expected 'module.path:ClassName', e.g. 'myapp.api:MyClient'")
    module = importlib.import_module(module_name)
    try:
        obj = getattr(module, class_name)
    except AttributeError:
        raise ValueError(f"module {module_name!r} has no attribute {class_name!r}") from None
    from gracy.client import Gracy

    if not (isinstance(obj, type) and issubclass(obj, Gracy)):
        raise ValueError(f"{target!r} is not a Gracy subclass (got {obj!r})")
    return obj


def _serve(html: str, port: int) -> t.NoReturn:
    tmp_dir = tempfile.mkdtemp(prefix="gracy-docs-")
    (Path(tmp_dir) / "index.html").write_text(html, encoding="utf-8")
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=tmp_dir)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Serving docs at http://127.0.0.1:{server.server_address[1]}/ (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    raise SystemExit(0)


def main(argv: t.Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gracy-docs",
        description="Generate OpenAPI/YAML/JSON/HTML docs from a Gracy client class - statically.",
    )
    parser.add_argument("target", help="import target as 'module.path:ClassName'")
    parser.add_argument(
        "--format",
        choices=("yaml", "json", "html"),
        default="yaml",
        dest="format",
        help="output format (default: yaml)",
    )
    parser.add_argument("-o", "--output", help="write to this file instead of stdout")
    parser.add_argument(
        "--serve",
        type=int,
        metavar="PORT",
        help="html only: write to a temp dir and serve it with http.server",
    )
    args = parser.parse_args(argv)

    if args.serve is not None and args.format != "html":
        print("gracy-docs: --serve requires --format html", file=sys.stderr)
        return 2

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        cls = _load_target(args.target)
    except Exception as exc:  # noqa: BLE001 - import errors become CLI errors
        print(f"gracy-docs: cannot load {args.target!r}: {exc}", file=sys.stderr)
        print("gracy-docs: expected 'module.path:ClassName', e.g. 'myapp.api:MyClient'", file=sys.stderr)
        return 1

    from gracy import docs

    try:
        if args.format == "yaml":
            rendered = docs.to_yaml(cls)
        elif args.format == "json":
            rendered = docs.to_json(cls)
        else:
            rendered = docs.to_html(cls)
    except Exception as exc:  # noqa: BLE001 - rendering errors become CLI errors
        print(f"gracy-docs: failed to render {args.format} docs: {exc}", file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.format} docs to {args.output}", file=sys.stderr)
    elif args.serve is None:
        sys.stdout.write(rendered)

    if args.serve is not None:
        _serve(rendered, args.serve)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
