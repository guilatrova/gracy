"""Gracefully manage your API interactions"""
from __future__ import annotations

import logging

from . import common_hooks, exceptions, replays
from ._core import Gracy, GracyNamespace, graceful, graceful_generator
from ._models import (
    DEFAULT_CONFIG,
    BaseEndpoint,
    ConcurrentRequestLimit,
    GracefulRetry,
    GracefulRetryState,
    GracefulThrottle,
    GracefulValidator,
    GracyConfig,
    GracyRequestContext,
    LogEvent,
    LogLevel,
    OverrideRetryOn,
    ThrottleRule,
)
from ._reports._models import GracyAggregatedRequest, GracyAggregatedTotal, GracyReport
from ._types import parsed_response
from .replays.storages._base import GracyReplay, GracyReplayStorage, ReplayLogEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

__version__ = "1.28.1"

__all__ = [
    "exceptions",
    # Core
    "Gracy",
    "GracyNamespace",
    "graceful",
    "graceful_generator",
    # Models
    "BaseEndpoint",
    "GracefulRetry",
    "OverrideRetryOn",
    "GracefulRetryState",
    "GracefulValidator",
    "GracyRequestContext",
    "LogEvent",
    "LogLevel",
    "GracefulThrottle",
    "ThrottleRule",
    "GracyConfig",
    "DEFAULT_CONFIG",
    "ConcurrentRequestLimit",
    # Replays
    "replays",
    "GracyReplay",
    "GracyReplayStorage",
    "ReplayLogEvent",
    # Reports
    "GracyReport",
    "GracyAggregatedTotal",
    "GracyAggregatedRequest",
    # Hooks
    "common_hooks",
    # Types
    "parsed_response",
]
