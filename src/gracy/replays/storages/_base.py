from __future__ import annotations

import logging
import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import httpx

from gracy.exceptions import GracyReplayRequestNotFound

from ..._loggers import DefaultLogMessage, do_log
from ..._models import LogEvent

logger = logging.getLogger(__name__)


REPLAY_FLAG: t.Final = "_gracy_replayed"


def is_replay(resp: httpx.Response) -> bool:
    return getattr(resp, REPLAY_FLAG, False)


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
    async def _load(self, request: httpx.Request, discard_before: datetime | None) -> httpx.Response:
        """Logic to load a response object based on the request. Raises `GracyReplayRequestNotFound` if missing"""
        pass

    async def load(
        self, request: httpx.Request, discard_before: datetime | None, discard_bad_responses: bool = False
    ) -> httpx.Response:
        """Logic to load a response object based on the request. Raises `GracyReplayRequestNotFound` if missing"""
        resp = await self._load(request, discard_before)
        setattr(resp, REPLAY_FLAG, True)

        if discard_bad_responses and resp.is_success is False:
            raise GracyReplayRequestNotFound(request)

        return resp

    def flush(self) -> None:
        """(Optional) Executed during close (preferably once all requests were made)."""
        pass


@dataclass
class ReplayLogEvent(LogEvent):
    frequency: int = 1_000
    """Defines how often to log when request is recorded/replayed"""


@dataclass
class GracyReplay:
    mode: t.Literal["record", "replay", "smart-replay"]
    """
    `record`: Will record all requests made to the API
    `replay`: Will read all responses from the defined storage
    `smart-replay`: Will read all responses (like `replay`), but if it's missing it will `record` for future replays
    """

    storage: GracyReplayStorage
    """Where to read/write requests and responses"""

    discard_replays_older_than: datetime | None = None
    """If set, Gracy will treat all replays older than defined value as not found"""

    discard_bad_responses: bool = False
    """If set True, then Gracy will discard bad requests (e.g. non 2xx)"""

    disable_throttling: bool = False
    """Only applicable to `smart-replay` and `replay` modes. If a replay exists then don't throttle the request"""

    display_report: bool = True
    """Whether to display records made and replays made to the final report"""

    log_record: ReplayLogEvent | None = None
    """Whether to log and how often to upon recording requests. The only available placeholder is `RECORDED_COUNT`"""

    log_replay: ReplayLogEvent | None = None
    """Whether to log and how often to upon replaying requests. The only available placeholder is `REPLAYED_COUNT`"""

    records_made: int = 0
    replays_made: int = 0

    async def has_replay(self, request: httpx.Request) -> bool:
        replay = await self.storage.find_replay(request, self.discard_replays_older_than)
        return bool(replay)

    def inc_record(self):
        self.records_made += 1

        if log_ev := self.log_record:
            if self.records_made % log_ev.frequency == 0:
                args = dict(RECORDED_COUNT=f"{self.records_made:,}")
                do_log(log_ev, DefaultLogMessage.REPLAY_RECORDED, args)

    def inc_replay(self):
        self.replays_made += 1

        if log_ev := self.log_replay:
            if self.replays_made % log_ev.frequency == 0:
                args = dict(REPLAYED_COUNT=f"{self.replays_made:,}")
                do_log(log_ev, DefaultLogMessage.REPLAY_REPLAYED, args)
