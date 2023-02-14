import logging
import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass

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
    async def load(self, request: httpx.Request) -> httpx.Response:
        """Logic to load a response object based on the request. Raises `GracyReplayRequestNotFound` if missing"""
        pass

    def flush(self) -> None:
        """(Optional) Executed during close (preferably once all requests were made)."""
        pass


@dataclass
class GracyReplay:
    mode: t.Literal["record", "replay"]
    strategy: GracyReplayStorage
