"""Gracefully manage your API interactions"""
from __future__ import annotations

import logging

from . import common_hooks, exceptions, replays
from ._core import Gracy, graceful, graceful_generator
from ._models import (
    DEFAULT_CONFIG,
    BaseEndpoint,
    GracefulRetry,
    GracefulRetryState,
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

__version__ = "1.20.0"

__all__ = [
    "exceptions",
    # Core
    "Gracy",
    "graceful",
    "graceful_generator",
    # Models
    "BaseEndpoint",
    "GracefulRetry",
    "GracefulRetryState",
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
    # Hooks
    "common_hooks",
]
