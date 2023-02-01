"""Gracefully manage your API interactions"""

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

__version__ = "1.2.0"

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
]
