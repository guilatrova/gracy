"""Gracefully manage your API interactions"""
import logging

from . import exceptions
from ._core import Gracy, graceful
from ._models import (
    DEFAULT_CONFIG,
    BaseEndpoint,
    GracefulRetry,
    GracefulThrottle,
    GracyConfig,
    GracyRequestContext,
    LogEvent,
    LogLevel,
    ThrottleRule,
)
from ._replay._models import GracyRecording
from ._replay._storages._base import GracyReplay, GracyReplayStorage
from ._replay._storages._pymongo_storage import MongoReplayStorage
from ._replay._storages._sqlite import SQLiteReplayStorage
from ._reports._models import GracyAggregatedRequest, GracyAggregatedTotal, GracyReport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

__version__ = "1.7.0"

__all__ = [
    "exceptions",
    # Core
    "Gracy",
    "graceful",
    # Models
    "BaseEndpoint",
    "GracefulRetry",
    "GracyRequestContext",
    "LogEvent",
    "LogLevel",
    "GracefulThrottle",
    "ThrottleRule",
    "GracyConfig",
    "DEFAULT_CONFIG",
    # Replay
    "GracyRecording",
    "GracyReplay",
    "MongoReplayStorage",
    "GracyReplayStorage",
    "SQLiteReplayStorage",
    # Reports
    "GracyReport",
    "GracyAggregatedTotal",
    "GracyAggregatedRequest",
]
