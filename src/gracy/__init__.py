"""Gracefully manage your API interactions"""
import logging

from . import exceptions, replays
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
from ._reports._models import GracyAggregatedRequest, GracyAggregatedTotal, GracyReport
from .replays.storages._base import GracyReplay, GracyReplayStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

__version__ = "1.11.3"

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
    # Replays
    "replays",
    "GracyReplay",
    "GracyReplayStorage",
    # Reports
    "GracyReport",
    "GracyAggregatedTotal",
    "GracyAggregatedRequest",
]
