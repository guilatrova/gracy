"""One-shot migration: Gracy v1 pickle-based replay SQLite DB -> schema v2.

    python -m gracy.replay.migrate OLD.sqlite3 NEW.db --yes-i-trust-this-file

v1 rows store a PICKLED httpx.Response — unpickling EXECUTES code embedded in
the file, so this tool refuses to run without the explicit trust flag, needs
httpx importable, and is the ONLY place in gracy v2 that touches pickle.
"""

from __future__ import annotations

import argparse
import asyncio
import pickle
import sqlite3
import sys
from datetime import datetime

from gracy._types import RequestSpec, Response
from gracy.replay import DEFAULT_MATCH_ON, Scrub, match_hash
from gracy.replay.storages import SqliteStorage

V1_TABLE = "gracy_recordings"

_TRUST_WARNING = f"""
{"!" * 78}
!!  REFUSING TO MIGRATE.
!!
!!  Gracy v1 replay databases store PICKLED responses. Unpickling EXECUTES
!!  ARBITRARY CODE embedded in the file — a malicious .sqlite3 file can take
!!  over this machine the moment it is loaded.
!!
!!  Only migrate databases YOU recorded or fully trust. If you do, re-run with:
!!
!!      python -m gracy.replay.migrate OLD.sqlite3 NEW.db --yes-i-trust-this-file
{"!" * 78}
"""


def _to_ms(updated_at: object) -> int | None:
    """v1 stored datetime.now() as an ISO-ish string; preserve it as unix-ms."""
    if isinstance(updated_at, datetime):
        return int(updated_at.timestamp() * 1000)
    if isinstance(updated_at, str):
        try:
            return int(datetime.fromisoformat(updated_at).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _response_fields(resp: object) -> tuple[int, tuple[tuple[str, str], ...], bytes, str, float]:
    headers_obj = getattr(resp, "headers", None)
    if headers_obj is not None and hasattr(headers_obj, "multi_items"):
        headers = tuple((str(k).lower(), str(v)) for k, v in headers_obj.multi_items())
    elif headers_obj is not None:
        headers = tuple((str(k).lower(), str(v)) for k, v in headers_obj.items())
    else:
        headers = ()
    try:
        elapsed = resp.elapsed.total_seconds()  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        elapsed = 0.0
    try:
        http_version = str(getattr(resp, "http_version", "HTTP/1.1"))
    except (KeyError, RuntimeError):
        http_version = "HTTP/1.1"
    return int(resp.status_code), headers, bytes(resp.content), http_version, float(elapsed)  # type: ignore[attr-defined]


async def _migrate(old_path: str, new_path: str) -> tuple[int, int]:
    con = sqlite3.connect(old_path)
    try:
        rows = con.execute(f"SELECT url, method, request_body, response, updated_at FROM {V1_TABLE}").fetchall()
    finally:
        con.close()

    storage = SqliteStorage(new_path)
    await storage.prepare()
    scrub = Scrub()
    migrated = skipped = 0

    for url, method, request_body, response_blob, updated_at in rows:
        try:
            v1_response = pickle.loads(response_blob)
            status, headers, body, http_version, elapsed = _response_fields(v1_response)
        except Exception as exc:  # noqa: BLE001 - report and continue, one bad row must not kill the run
            skipped += 1
            print(f"  SKIP {method} {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        content = bytes(request_body) if request_body else None
        spec = RequestSpec(method=str(method), url=str(url), uurl=str(url), headers=(), content=content)
        key = match_hash(spec, DEFAULT_MATCH_ON, scrub)

        v2_response = Response(
            status=status, headers=headers, body=body, url=str(url), elapsed=elapsed, http_version=http_version
        )
        await storage.record_key(key, scrub.apply(spec), v2_response, recorded_at_ms=_to_ms(updated_at))
        migrated += 1

    await storage.flush()
    return migrated, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gracy.replay.migrate",
        description="Migrate a Gracy v1 (pickle) replay SQLite DB to the v2 pickle-free schema.",
    )
    parser.add_argument("old", help="path to the v1 .sqlite3 replay database")
    parser.add_argument("new", help="path for the new v2 database (created if missing)")
    parser.add_argument(
        "--yes-i-trust-this-file",
        action="store_true",
        help="acknowledge that unpickling the v1 file EXECUTES code embedded in it",
    )
    args = parser.parse_args(argv)

    if not args.yes_i_trust_this_file:
        print(_TRUST_WARNING, file=sys.stderr)
        return 2

    try:
        import httpx  # noqa: F401 - required so the pickled httpx.Response can be reconstructed
    except ImportError:
        print(
            "gracy.replay.migrate needs httpx installed to unpickle v1 recordings "
            "(they are pickled httpx.Response objects). Install it with: pip install httpx",
            file=sys.stderr,
        )
        return 1

    migrated, skipped = asyncio.run(_migrate(args.old, args.new))
    print(f"Migrated {migrated} recording(s) ({skipped} skipped) -> {args.new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
