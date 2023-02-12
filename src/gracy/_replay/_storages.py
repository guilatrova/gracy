import logging
import pickle
import sqlite3
import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from gracy.exceptions import GracyReplayRequestNotFound

from . import _sqlite_schema as schema
from ._models import GracyRecording

logger = logging.getLogger(__name__)


class GracyReplayStorage(ABC):
    def prepare(self) -> None:
        """(Optional) Executed upon API instance creation."""
        pass

    @abstractmethod
    async def record(self, response: httpx.Response) -> None:
        """Logic to store the response object. Note the httpx.Response has request data"""
        pass

    @abstractmethod
    async def load(self, request: httpx.Request) -> httpx.Response:
        """Logic to load a response object based on the request"""
        pass


@dataclass
class GracyReplay:
    mode: t.Literal["record", "replay"]
    strategy: GracyReplayStorage


class SQLiteReplayStorage(GracyReplayStorage):
    def __init__(self, db_name: str = "gracy-records.sqlite3", dir: str = ".gracy") -> None:
        self.db_dir = Path(dir)
        self.db_file = self.db_dir / db_name
        self._con: sqlite3.Connection = None  # type: ignore

    def _create_db(self) -> None:
        logger.info("Creating Gracy Replay sqlite database")
        con = sqlite3.connect(str(self.db_file))
        cur = con.cursor()

        cur.execute(schema.CREATE_RECORDINGS_TABLE)
        cur.execute(schema.INDEX_RECORDINGS_TABLE)
        cur.execute(schema.INDEX_RECORDINGS_TABLE_WITHOUT_REQUEST_BODY)

    def _insert_into_db(self, recording: GracyRecording) -> None:
        cur = self._con.cursor()

        params = (
            recording.url,
            recording.method,
            recording.request_body,
            recording.response,
            datetime.now(),
        )
        cur.execute(schema.INSERT_RECORDING_BASE, params)
        self._con.commit()

    def prepare(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        if self.db_file.exists() is False:
            self._create_db()

        self._con = sqlite3.connect(str(self.db_file))

    async def record(self, response: httpx.Response) -> None:
        response_serialized = pickle.dumps(response)

        recording = GracyRecording(
            str(response.url),
            response.request.method,
            response.request.content or None,
            response_serialized,
            datetime.now(),
        )

        self._insert_into_db(recording)

    async def load(self, request: httpx.Request) -> httpx.Response:
        cur = self._con.cursor()
        params: t.Iterable[str | bytes]

        if bool(request.content):
            params = (str(request.url), request.method, request.content)
            cur.execute(schema.FIND_REQUEST_WITH_REQ_BODY, params)
        else:
            params = (str(request.url), request.method)
            cur.execute(schema.FIND_REQUEST_WITHOUT_REQ_BODY, params)

        fetch_res = cur.fetchone()
        if fetch_res is None:
            raise GracyReplayRequestNotFound(request)

        serialized_response: bytes = fetch_res[0]
        response: httpx.Response = pickle.loads(serialized_response)

        return response
