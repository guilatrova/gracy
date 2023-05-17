from __future__ import annotations

import logging
import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import httpx

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
    async def find_replay(self, request: httpx.Request, discard_before: datetime | None) -> t.Any | None:
        pass

    @abstractmethod
    async def load(self, request: httpx.Request, discard_before: datetime | None) -> httpx.Response:
        """Logic to load a response object based on the request. Raises `GracyReplayRequestNotFound` if missing"""
        pass

    def flush(self) -> None:
        """(Optional) Executed during close (preferably once all requests were made)."""
        pass


@dataclass
class GracyReplay:
    mode: t.Literal["record", "replay", "smart-replay"]
    """
    `record`: Will record all requests made to the API
    `replay`: Will read all responses from the defined storage
    `smart-replay`: Will read all responses (like `replay`), but if it's missing it will `record` for future replays
    """

    storage: GracyReplayStorage
    """
    Where to read/write requests and responses
    """

    discard_replays_older_than: datetime | None = None
    """If set, Gracy will treat all replays older than defined value as not found"""

    disable_throttling: bool = False
    """Only applicable to `smart-replay` and `replay` modes. If a replay exists then don't throttle the request"""

    display_report: bool = True
    """Whether to display records made and replays made to the final report"""
    records_made: int = 0
    replays_made: int = 0

    async def has_replay(self, request: httpx.Request) -> bool:
        replay = await self.storage.find_replay(request, self.discard_replays_older_than)
        return bool(replay)
