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
from ._reports._models import GracyAggregatedRequest, GracyAggregatedTotal, GracyReport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

__version__ = "1.4.0"

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
    # Reports
    "GracyReport",
    "GracyAggregatedTotal",
    "GracyAggregatedRequest",
]
