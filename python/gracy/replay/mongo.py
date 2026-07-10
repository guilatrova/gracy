"""MongoDB cassette storage (schema v2, no pickle). pymongo is imported lazily
in ``prepare()`` — install with ``pip install gracy[mongo]``.

Documents mirror the SQLite v2 columns (bytes become BSON Binary automatically):
match_hash, method, url, request_headers_json, request_body, status,
response_headers_json, response_body, http_version, elapsed_ms, recorded_at,
schema_version.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import typing as t

from gracy._types import RequestSpec, Response
from gracy.exceptions import GracyConfigError
from gracy.replay.storages import SCHEMA_VERSION

_INSTALL_HINT = (
    "MongoReplayStorage requires pymongo, which is not installed. "
    "Install it with: pip install gracy[mongo] (or: pip install pymongo)"
)


def _default_key(spec: RequestSpec) -> str:
    from gracy.replay import DEFAULT_MATCH_ON, match_hash  # lazy: avoids import cycle

    return match_hash(spec, DEFAULT_MATCH_ON, None)


class MongoReplayStorage:
    """Cassette storage on MongoDB. All pymongo calls run in ``asyncio.to_thread``.

    ``batch_size > 0`` buffers writes as ReplaceOne upserts (under a lock) and
    flushes via ``bulk_write`` once the buffer reaches ``batch_size`` — and
    always on ``flush()``, which ``Gracy.aclose()`` guarantees to await, so
    batched recordings are never lost (v1 bug #12).
    """

    def __init__(
        self,
        uri: str | None = None,
        *,
        host: str | None = None,
        port: int = 27017,
        username: str | None = None,
        password: str | None = None,
        database: str = "gracy",
        collection: str = "recordings",
        batch_size: int = 0,
    ) -> None:
        if uri is not None and host is not None:
            raise GracyConfigError("MongoReplayStorage: pass either uri= OR host/port/username/password, not both")
        self.uri = uri
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.collection = collection
        self.batch_size = batch_size

        self._pymongo: t.Any = None
        self._client: t.Any = None
        self._coll: t.Any = None
        self._lock = threading.Lock()
        self._ops: list[t.Any] = []  # buffered pymongo.ReplaceOne
        self._pending: dict[str, dict[str, t.Any]] = {}  # buffered docs, findable pre-flush

    # ------------------------------------------------------------- lifecycle

    async def prepare(self) -> None:
        await asyncio.to_thread(self._prepare_sync)

    def _prepare_sync(self) -> None:
        try:
            import pymongo  # pyright: ignore[reportMissingImports]
        except ImportError as e:
            raise ImportError(_INSTALL_HINT) from e

        with self._lock:
            if self._coll is not None:
                return
            self._pymongo = pymongo
            if self.uri is not None:
                client = pymongo.MongoClient(self.uri)
            else:
                client = pymongo.MongoClient(
                    host=self.host, port=self.port, username=self.username, password=self.password
                )
            coll = client[self.database][self.collection]
            coll.create_index("match_hash", unique=True, background=True)
            self._client = client
            self._coll = coll

    def _require_coll(self) -> t.Any:
        if self._coll is None:
            raise RuntimeError("MongoReplayStorage used before prepare() was awaited")
        return self._coll

    async def flush(self) -> None:
        await asyncio.to_thread(self._flush_sync)

    def _flush_sync(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if self._ops:
            self._require_coll().bulk_write(self._ops)
            self._ops = []
            self._pending.clear()

    # ------------------------------------------------------------- keyed API

    async def record_key(
        self, key: str, spec: RequestSpec, response: Response, *, recorded_at_ms: int | None = None
    ) -> None:
        await asyncio.to_thread(self._record_sync, key, spec, response, recorded_at_ms)

    def _record_sync(self, key: str, spec: RequestSpec, response: Response, recorded_at_ms: int | None) -> None:
        doc: dict[str, t.Any] = {
            "match_hash": key,
            "method": spec.method.upper(),
            "url": spec.url,
            "request_headers_json": json.dumps([list(kv) for kv in spec.headers]),
            "request_body": spec.content,
            "status": response.status,
            "response_headers_json": json.dumps([list(kv) for kv in response.headers]),
            "response_body": response.body,
            "http_version": response.http_version,
            "elapsed_ms": response.elapsed * 1000.0,
            "recorded_at": recorded_at_ms if recorded_at_ms is not None else int(time.time() * 1000),
            "schema_version": SCHEMA_VERSION,
        }
        coll = self._require_coll()
        if self.batch_size > 0:
            with self._lock:
                self._ops.append(self._pymongo.ReplaceOne({"match_hash": key}, doc, upsert=True))
                self._pending[key] = doc
                if len(self._ops) >= self.batch_size:
                    self._flush_locked()
        else:
            coll.replace_one({"match_hash": key}, doc, upsert=True)

    async def find_by_key(self, key: str, discard_before: float | None) -> Response | None:
        return await asyncio.to_thread(self._find_sync, key, discard_before)

    def _find_sync(self, key: str, discard_before: float | None) -> Response | None:
        with self._lock:
            doc = self._pending.get(key)
        if doc is None:
            doc = self._require_coll().find_one({"match_hash": key})
        if doc is None:
            return None
        if discard_before is not None and doc["recorded_at"] < discard_before * 1000.0:
            return None
        return Response(
            status=int(doc["status"]),
            headers=tuple((str(k), str(v)) for k, v in json.loads(doc["response_headers_json"])),
            body=bytes(doc["response_body"]),
            url=str(doc["url"]),
            elapsed=float(doc["elapsed_ms"]) / 1000.0,
            http_version=str(doc["http_version"]),
            is_replay=True,
        )

    # ------------------------------------------- ReplayStorage protocol face

    async def record(self, spec: RequestSpec, response: Response) -> None:
        await self.record_key(_default_key(spec), spec, response)

    async def find(self, spec: RequestSpec, discard_before: float | None) -> Response | None:
        return await self.find_by_key(_default_key(spec), discard_before)
