from __future__ import annotations

import json
import pickle
import typing as t
from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Lock

import httpx

from gracy.exceptions import GracyReplayRequestNotFound

from ._base import GracyReplayStorage

try:
    import pymongo
except ModuleNotFoundError:
    pass


@dataclass
class MongoCredentials:
    host: str | None = None
    """Can be a full URI"""
    port: int = 27017
    username: str | None = None
    password: str | None = None


class MongoReplayDocument(t.TypedDict):
    url: str
    method: str
    request_body: bytes | None
    response: bytes
    response_content: dict[str, t.Any] | str | None
    """Useful for debugging since Mongo supports unstructured data"""
    updated_at: datetime


def get_unique_keys_from_doc(replay_doc: MongoReplayDocument) -> t.Dict[str, bytes | None | str]:
    return {"url": replay_doc["url"], "method": replay_doc["method"], "request_body": replay_doc["request_body"]}


def get_unique_keys_from_request(request: httpx.Request) -> t.Dict[str, bytes | None | str]:
    return {"url": str(request.url), "method": request.method, "request_body": request.content or None}


batch_lock = Lock()


class MongoReplayStorage(GracyReplayStorage):
    def __init__(
        self,
        creds: MongoCredentials,
        database_name: str = "gracy",
        collection_name: str = "gracy-replay",
        batch_size: int | None = None,
    ) -> None:
        creds_kwargs = asdict(creds)

        client = pymongo.MongoClient(**creds_kwargs, document_class=MongoReplayDocument)
        mongo_db = client[database_name]
        self._collection = mongo_db[collection_name]
        self._batch = batch_size
        self._batch_ops: t.List[pymongo.ReplaceOne[MongoReplayDocument]] = []

    def _flush_batch(self) -> None:
        if self._batch_ops:
            with batch_lock:
                self._collection.bulk_write(self._batch_ops)  # type: ignore
                self._batch_ops = []

    def _create_or_batch(self, doc: MongoReplayDocument) -> None:
        filter = get_unique_keys_from_doc(doc)
        if self._batch and self._batch > 1:
            with batch_lock:
                self._batch_ops.append(pymongo.ReplaceOne(filter, doc, upsert=True))

            if len(self._batch_ops) >= self._batch:
                self._flush_batch()

        else:
            self._collection.replace_one(filter, doc, upsert=True)

    def prepare(self) -> None:
        self._collection.create_index(
            [("url", 1), ("method", 1), ("request_body", 1)],
            background=True,
            unique=True,
        )

    async def record(self, response: httpx.Response) -> None:
        response_serialized = pickle.dumps(response)

        response_content = response.text or None
        content_type = response.headers.get("Content-Type")
        if content_type and "json" in content_type:
            try:
                jsonified_content = response.json()

            except json.decoder.JSONDecodeError:
                pass

            else:
                response_content = jsonified_content

        doc = MongoReplayDocument(
            url=str(response.url),
            method=response.request.method,
            request_body=response.request.content or None,
            response=response_serialized,
            response_content=response_content,
            updated_at=datetime.now(),
        )

        self._create_or_batch(doc)

    async def find_replay(self, request: httpx.Request, discard_before: datetime | None) -> MongoReplayDocument | None:
        filter = get_unique_keys_from_request(request)
        doc = self._collection.find_one(filter)

        if doc is None:
            return None

        if discard_before and doc["updated_at"] < discard_before:
            return None

        return doc

    async def _load(self, request: httpx.Request, discard_before: datetime | None) -> httpx.Response:
        doc = await self.find_replay(request, discard_before)

        if doc is None:
            raise GracyReplayRequestNotFound(request)

        serialized_response = doc["response"]
        response: httpx.Response = pickle.loads(serialized_response)

        return response

    def flush(self) -> None:
        self._flush_batch()
