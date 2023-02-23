"""Gracefully manage your API interactions"""
import logging

from . import exceptions
from ._core import Gracy, graceful
from ._models import (
    DEFAULT_CONFIG,
    BaseEndpoint,
    GracefulRetry,
    GracefulThrottle,
    GracefulValidator,
    GracyConfig,
    GracyRequestContext,
    LogEvent,
    LogLevel,
    ThrottleRule,
)
from ._replay._models import GracyRecording
from ._replay._storages import GracyReplay, GracyReplayStorage, SQLiteReplayStorage
from ._reports._models import GracyAggregatedRequest, GracyAggregatedTotal, GracyReport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

__version__ = "1.11.0"

__all__ = [
    "exceptions",
    # Core
    "Gracy",
    "graceful",
    # Models
    "BaseEndpoint",
    "GracefulRetry",
    "GracefulValidator",
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
    "GracyReplayStorage",
    "SQLiteReplayStorage",
    # Reports
    "GracyReport",
    "GracyAggregatedTotal",
    "GracyAggregatedRequest",
]
