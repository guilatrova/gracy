"""``gracy`` — the umbrella CLI.

    gracy -i [BASE_URL] [--session f.json]     interactive API explorer (alias: gracy explore)
    gracy x '<command>' [--session f.json] [--base URL] [--json]
                                               one-shot agent mode (exit 0 ok / 2 parse / 1 exec)
    gracy monitor ...                          live dashboard (delegates to gracy.monitor)
    gracy docs ...                             docs generator (delegates to gracy.docs)
    gracy --version

Everything heavy (explore engine, rich, monitor, docs) is imported lazily so
``gracy --version`` stays instant and dependency-free.
"""

from __future__ import annotations

import argparse
import json
import sys
import typing as t

__all__ = ["main"]

_USAGE = """\
usage: gracy <command> [...]

  gracy -i [BASE_URL] [--session f.json]   interactive API explorer (alias: explore)
  gracy x '<command>' [--session f.json] [--base URL] [--json]
                                           run ONE explorer command (agent mode)
  gracy monitor [...]                      live terminal dashboard
  gracy docs [...]                         generate API docs from a Gracy class
  gracy --version                          print the version\
"""


def main(argv: t.Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(_USAGE, file=sys.stderr)
        return 2
    head, rest = args[0], args[1:]

    if head in ("--version", "-V", "version"):
        from gracy import __version__

        print(f"gracy {__version__}")
        return 0
    if head in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    if head in ("-i", "explore"):
        return _cmd_interactive(rest)
    if head == "x":
        return _cmd_one_shot(rest)
    if head == "monitor":
        from gracy.monitor.viewer import main as monitor_main

        return monitor_main(rest)
    if head == "docs":
        from gracy.docs.__main__ import main as docs_main

        return docs_main(rest)

    print(f"gracy: unknown command {head!r}", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    return 2


# --------------------------------------------------------------------------- gracy -i


def _cmd_interactive(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gracy -i", description="interactive API explorer")
    parser.add_argument("base_url", nargs="?", default=None, help="API base URL (or set later with `base <url>`)")
    parser.add_argument("--session", default="gracy_explore.json", help="session file (default: gracy_explore.json)")
    ns = parser.parse_args(argv)

    try:
        import rich  # noqa: F401
    except ImportError:
        print(
            "gracy -i needs the optional 'rich' package — install it with: pip install 'gracy[rich]'",
            file=sys.stderr,
        )
        return 1

    import asyncio

    from gracy.explore._session import ExploreSession
    from gracy.explore.repl import run_repl

    session = ExploreSession(ns.session, base_url=ns.base_url)
    try:
        return asyncio.run(run_repl(session))
    except KeyboardInterrupt:
        return 130


# --------------------------------------------------------------------------- gracy x


def _cmd_one_shot(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gracy x", description="run one explorer command against a session file")
    parser.add_argument("command", help="one explorer command, e.g. 'get /pokemon/mew limit==5'")
    parser.add_argument("--session", default="gracy_explore.json", help="session file (default: gracy_explore.json)")
    parser.add_argument("--base", default=None, help="set/override the session base URL")
    parser.add_argument("--json", dest="as_json", action="store_true", help="print a single machine-readable dict")
    ns = parser.parse_args(argv)

    from gracy.explore._parser import ParseError, parse_command

    try:
        cmd = parse_command(ns.command)
    except ParseError as exc:
        _emit_error(str(exc), ns.as_json)
        return 2

    import asyncio

    from gracy.explore._session import ExploreSession
    from gracy.explore.repl import Outcome, execute_command

    async def _run() -> Outcome:
        session = ExploreSession(ns.session, base_url=ns.base)
        try:
            return await execute_command(session, cmd)
        finally:
            await session.aclose()

    try:
        outcome = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - the agent contract is json/1, never a traceback
        _emit_error(f"{type(exc).__name__}: {exc}", ns.as_json)
        return 1

    if ns.as_json:
        print(json.dumps(outcome.data, default=str))
    else:
        print(outcome.human)
        for hint in outcome.hints:
            print(hint)
    failed = outcome.step is not None and outcome.step.error is not None
    return 1 if failed else 0


def _emit_error(message: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}))
    else:
        print(f"gracy: {message}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
