"""Built-in cassette storages: SQLite (schema v2, no pickle) and in-memory.

Both expose the keyed API (``find_by_key``/``record_key``) that
:class:`gracy.replay.Replay` prefers, PLUS the spec-shaped
:class:`gracy._protocols.ReplayStorage` protocol methods (``find``/``record``)
so they remain drop-in valid protocol implementations. The protocol methods
hash with the DEFAULT match dimensions and no scrub (the caller — Replay —
already scrubbed the spec on that path); use the keyed API for custom
``match_on`` settings.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
import typing as t

from gracy._types import RequestSpec, Response

SCHEMA_VERSION: t.Final = 2

TABLE_NAME: t.Final = "gracy_recordings_v2"

_CREATE_TABLE: t.Final = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    match_hash            TEXT PRIMARY KEY,
    method                TEXT NOT NULL,
    url                   TEXT NOT NULL,
    request_headers_json  TEXT NOT NULL,
    request_body          BLOB,
    status                INTEGER NOT NULL,
    response_headers_json TEXT NOT NULL,
    response_body         BLOB NOT NULL,
    http_version          TEXT NOT NULL,
    elapsed_ms            REAL NOT NULL,
    recorded_at           INTEGER NOT NULL,
    schema_version        INTEGER NOT NULL
)
"""

_INSERT: t.Final = f"""
INSERT OR REPLACE INTO {TABLE_NAME}
(match_hash, method, url, request_headers_json, request_body, status,
 response_headers_json, response_body, http_version, elapsed_ms, recorded_at, schema_version)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_FIND: t.Final = f"""
SELECT url, status, response_headers_json, response_body, http_version, elapsed_ms, recorded_at
FROM {TABLE_NAME} WHERE match_hash = ?
"""


def _headers_from_json(raw: str) -> tuple[tuple[str, str], ...]:
    return tuple((str(k), str(v)) for k, v in json.loads(raw))


def _default_key(spec: RequestSpec) -> str:
    from gracy.replay import DEFAULT_MATCH_ON, match_hash  # lazy: avoids import cycle

    return match_hash(spec, DEFAULT_MATCH_ON, None)


class SqliteStorage:
    """stdlib-sqlite3 cassette store. One connection (WAL, check_same_thread=False);
    every DB operation runs in ``asyncio.to_thread`` serialized by a ``threading.Lock``.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- lifecycle

    async def prepare(self) -> None:
        await asyncio.to_thread(self._prepare_sync)

    def _prepare_sync(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_TABLE)
            conn.commit()
            self._conn = conn

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteStorage used before prepare() was awaited")
        return self._conn

    async def flush(self) -> None:
        await asyncio.to_thread(self._flush_sync)

    def _flush_sync(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.commit()

    # ------------------------------------------------------------- keyed API

    async def record_key(
        self, key: str, spec: RequestSpec, response: Response, *, recorded_at_ms: int | None = None
    ) -> None:
        await asyncio.to_thread(self._record_sync, key, spec, response, recorded_at_ms)

    def _record_sync(self, key: str, spec: RequestSpec, response: Response, recorded_at_ms: int | None) -> None:
        params = (
            key,
            spec.method.upper(),
            spec.url,
            json.dumps([list(kv) for kv in spec.headers]),
            spec.content,
            response.status,
            json.dumps([list(kv) for kv in response.headers]),
            response.body,
            response.http_version,
            response.elapsed * 1000.0,
            recorded_at_ms if recorded_at_ms is not None else int(time.time() * 1000),
            SCHEMA_VERSION,
        )
        with self._lock:
            conn = self._require_conn()
            conn.execute(_INSERT, params)
            conn.commit()

    async def find_by_key(self, key: str, discard_before: float | None) -> Response | None:
        return await asyncio.to_thread(self._find_sync, key, discard_before)

    def _find_sync(self, key: str, discard_before: float | None) -> Response | None:
        with self._lock:
            row = self._require_conn().execute(_FIND, (key,)).fetchone()
        if row is None:
            return None
        url, status, resp_headers_json, resp_body, http_version, elapsed_ms, recorded_at = row
        if discard_before is not None and recorded_at < discard_before * 1000.0:
            return None
        return Response(
            status=int(status),
            headers=_headers_from_json(resp_headers_json),
            body=bytes(resp_body),
            url=str(url),
            elapsed=float(elapsed_ms) / 1000.0,
            http_version=str(http_version),
            is_replay=True,
        )

    # ------------------------------------------- ReplayStorage protocol face

    async def record(self, spec: RequestSpec, response: Response) -> None:
        await self.record_key(_default_key(spec), spec, response)

    async def find(self, spec: RequestSpec, discard_before: float | None) -> Response | None:
        return await self.find_by_key(_default_key(spec), discard_before)


class MemoryStorage:
    """Dict-backed storage for tests. Same interface as SqliteStorage."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, dict[str, t.Any]]] = {}

    async def prepare(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    # ------------------------------------------------------------- keyed API

    async def record_key(
        self, key: str, spec: RequestSpec, response: Response, *, recorded_at_ms: int | None = None
    ) -> None:
        recorded = float(recorded_at_ms) if recorded_at_ms is not None else time.time() * 1000.0
        self._items[key] = (
            recorded,
            {
                "status": response.status,
                "headers": tuple(response.headers),
                "body": response.body,
                "url": response.url,
                "elapsed": response.elapsed,
                "http_version": response.http_version,
            },
        )

    async def find_by_key(self, key: str, discard_before: float | None) -> Response | None:
        item = self._items.get(key)
        if item is None:
            return None
        recorded_at_ms, fields = item
        if discard_before is not None and recorded_at_ms < discard_before * 1000.0:
            return None
        return Response(
            status=fields["status"],
            headers=fields["headers"],
            body=fields["body"],
            url=fields["url"],
            elapsed=fields["elapsed"],
            http_version=fields["http_version"],
            is_replay=True,
        )

    # ------------------------------------------- ReplayStorage protocol face

    async def record(self, spec: RequestSpec, response: Response) -> None:
        await self.record_key(_default_key(spec), spec, response)

    async def find(self, spec: RequestSpec, discard_before: float | None) -> Response | None:
        return await self.find_by_key(_default_key(spec), discard_before)

    def __len__(self) -> int:
        return len(self._items)
