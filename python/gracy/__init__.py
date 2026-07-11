"""Gracy 2.0 - Python's most graceful API Client Framework, Rust-powered."""

from __future__ import annotations

import logging

from gracy._core import engine_version
from gracy._protocols import Decoder, Hook, Validator
from gracy._types import (
    UNSET,
    RequestContext,
    RequestSpec,
    Response,
    RetryState,
    Unset,
)
from gracy.config import (
    Backoff,
    Concurrency,
    GracyConfig,
    LogEvent,
    LogLevel,
    Queue,
    Rate,
    Retry,
    Throttle,
    allow,
    raises,
    status,
    strict,
)
from gracy.endpoints import (
    BaseEndpoint,
    Body,
    Header,
    Path,
    Query,
    delete,
    get,
    head,
    options,
    patch,
    post,
    put,
)
from gracy.exceptions import (
    GracyClientClosedError,
    GracyConfigError,
    GracyException,
    GracyForkedClientError,
    GracyParseFailed,
    GracyQueueFull,
    GracyReplayRequestNotFound,
    GracyRequestFailed,
    GracyResponseError,
    GracyUserDefinedException,
    GracyWrongLoopError,
    NonOkResponse,
    UnexpectedResponse,
)
from gracy.client import Gracy, GracyNamespace, SyncGracy
from gracy.hooks import HookResult, RateLimitBackoff, RetryAfterBackoff
from gracy.paginator import GracyOffsetPaginator, GracyPaginator
from gracy.parsing import JsonDecoder, MsgspecDecoder, PydanticDecoder
from gracy.pipeline import in_hook_context
from gracy.replay import MemoryStorage, Replay, Scrub, SqliteStorage

# MongoReplayStorage deliberately NOT re-exported here: import it from gracy.replay.mongo.
from gracy.reports import GracyReport
from gracy.transports import HttpxTransport, MockTransport, RustTransport, TransportConfig
from gracy import testing  # noqa: F401  # makes `gracy.testing.retries_off()` work after `import gracy`

__version__ = "2.0.0a0"

logging.getLogger("gracy").addHandler(logging.NullHandler())

__all__ = [
    # version / engine
    "__version__",
    "engine_version",
    # client surface
    "Gracy",
    "GracyNamespace",
    "SyncGracy",
    # endpoints
    "BaseEndpoint",
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "Path",
    "Query",
    "Header",
    "Body",
    # config
    "GracyConfig",
    "Retry",
    "Backoff",
    "Throttle",
    "Rate",
    "Concurrency",
    "Queue",
    "LogEvent",
    "LogLevel",
    "strict",
    "allow",
    "status",
    "raises",
    "UNSET",
    "Unset",
    # core types
    "Response",
    "RequestContext",
    "RequestSpec",
    "RetryState",
    # protocols / hooks / validators / decoders
    "Hook",
    "HookResult",
    "RetryAfterBackoff",
    "RateLimitBackoff",
    "Validator",
    "Decoder",
    "JsonDecoder",
    "PydanticDecoder",
    "MsgspecDecoder",
    "in_hook_context",
    # exceptions
    "GracyException",
    "GracyConfigError",
    "GracyWrongLoopError",
    "GracyForkedClientError",
    "GracyClientClosedError",
    "GracyQueueFull",
    "GracyRequestFailed",
    "GracyResponseError",
    "NonOkResponse",
    "UnexpectedResponse",
    "GracyParseFailed",
    "GracyReplayRequestNotFound",
    "GracyUserDefinedException",
    # replay
    "Replay",
    "Scrub",
    "SqliteStorage",
    "MemoryStorage",
    # pagination / reports / transports
    "GracyPaginator",
    "GracyOffsetPaginator",
    "GracyReport",
    "TransportConfig",
    "HttpxTransport",
    "MockTransport",
    "RustTransport",
]
