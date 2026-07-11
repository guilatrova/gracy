"""``gracy`` - the umbrella CLI.

    gracy explore [BASE_URL] [--session f.json]  interactive API explorer
    gracy x '<command>' [--session f.json] [--base URL] [--json]
                                                 one-shot agent mode (exit 0 ok / 2 parse / 1 exec)
    gracy monitor ...                            live dashboard (delegates to gracy.monitor)
    gracy docs ...                               docs generator (delegates to gracy.docs)
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

  gracy explore [BASE_URL] [--session f.json]
                                           interactive API explorer
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
    if head == "explore":
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


# --------------------------------------------------------------------------- gracy explore


def _cmd_interactive(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gracy explore", description="interactive API explorer")
    parser.add_argument("base_url", nargs="?", default=None, help="API base URL (or set later with `base <url>`)")
    parser.add_argument("--session", default="gracy_explore.json", help="session file (default: gracy_explore.json)")
    parser.add_argument(
        "--stdio", action="store_true", help="persistent JSONL loop: one command per stdin line, one result per stdout line"
    )
    parser.add_argument(
        "--check", action="store_true", help="re-probe named endpoints against the live API and report shape drift"
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="machine-readable output (--check)")
    parser.add_argument("--base", default=None, help="override the session base URL (--stdio/--check)")
    ns = parser.parse_args(argv)

    import asyncio

    from gracy.explore._session import ExploreSession

    base = ns.base if ns.base is not None else ns.base_url

    if ns.stdio:
        return asyncio.run(_run_stdio(ExploreSession(ns.session, base_url=base)))
    if ns.check:
        return asyncio.run(_run_check(ExploreSession(ns.session, base_url=base), ns.as_json))

    try:
        import rich  # noqa: F401
    except ImportError:
        print(
            "gracy explore needs the optional 'rich' package - install it with: pip install 'gracy[rich]'",
            file=sys.stderr,
        )
        return 1

    from gracy.explore.repl import run_repl

    session = ExploreSession(ns.session, base_url=base)
    try:
        return asyncio.run(run_repl(session))
    except KeyboardInterrupt:
        return 130


async def _run_stdio(session) -> int:  # type: ignore[no-untyped-def]
    """One live process: read {"cmd": "<command>"} lines on stdin, emit one
    JSON result line per input on stdout. The session stays in memory across
    lines, so a long agent run pays no per-command startup or file re-read."""
    import asyncio

    from gracy.explore._parser import ParseError, parse_command
    from gracy.explore.repl import execute_command

    loop = asyncio.get_running_loop()

    def _emit(obj: dict) -> None:  # type: ignore[type-arg]
        sys.stdout.write(json.dumps(obj, default=str) + "\n")
        sys.stdout.flush()

    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":  # EOF
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                command = payload["cmd"] if isinstance(payload, dict) else payload
                if not isinstance(command, str):
                    raise ValueError("expected a JSON object with a string 'cmd', or a JSON string")
                cmd = parse_command(command)
            except (json.JSONDecodeError, KeyError, ValueError, ParseError) as exc:
                _emit({"error": str(exc)})
                continue
            try:
                outcome = await execute_command(session, cmd)
            except Exception as exc:  # noqa: BLE001 - one bad command must not kill the stream
                _emit({"error": f"{type(exc).__name__}: {exc}"})
                continue
            _emit(outcome.data)
    finally:
        await session.aclose()
    return 0


async def _run_check(session, as_json: bool) -> int:  # type: ignore[no-untyped-def]
    """Re-probe named endpoints live, diff shapes, exit 1 on any drift."""
    try:
        if not session.endpoints():
            msg = "no named endpoints in this session - name some with `gracy explore` first"
            print(json.dumps({"error": msg}) if as_json else f"gracy: {msg}", file=sys.stderr)
            return 0
        results = await session.check_all()
    finally:
        await session.aclose()

    drifted = [r for r in results if not r.ok]
    if as_json:
        print(json.dumps({"ok": not drifted, "endpoints": [r.as_dict() for r in results]}, default=str))
    else:
        for r in results:
            if r.ok:
                print(f"  ✓ {r.endpoint}  {r.method} {r.template}")
                continue
            print(f"  ✗ {r.endpoint}  {r.method} {r.template}")
            if r.error:
                print(f"      request failed: {r.error}")
            if r.note:
                print(f"      {r.note}")
            if r.status_recorded != r.status_live and r.status_live is not None:
                print(f"      status: {r.status_recorded} -> {r.status_live}")
            for path in r.shape.removed:
                print(f"      - removed: {path}")
            for path in r.shape.added:
                print(f"      + added:   {path}")
            for change in r.shape.type_changed:
                print(f"      ~ type:    {change}")
        summary = "drift detected" if drifted else "no drift - all endpoints match their recordings"
        print(f"\n{len(drifted)}/{len(results)} endpoints drifted" if drifted else f"\n{summary}")
    return 1 if drifted else 0


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
