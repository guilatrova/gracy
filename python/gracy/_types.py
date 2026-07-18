"""Core value types shared by every layer. No imports from other gracy modules."""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Final, Mapping


class Unset:
    """Sentinel: 'inherit from the outer config layer'. Distinct from None ('explicitly disabled')."""

    _instance: Unset | None = None

    def __new__(cls) -> Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = Unset()


def pick(*values: Any, default: Any = None) -> Any:
    """First value that is not UNSET; falls back to `default`."""
    for v in values:
        if v is not UNSET:
            return v
    return default


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """Everything needed to send AND replay-match one HTTP request.

    The SAME object drives the live request and the replay match hash -
    the v1 whitelist-divergence bug class cannot exist here.
    """

    method: str
    url: str  # final, absolute, formatted URL
    uurl: str  # unformatted template, e.g. "https://api/pokemon/{NAME}" - throttle/metrics key
    headers: tuple[tuple[str, str], ...] = ()
    content: bytes | None = None
    timeout: float | None = None  # seconds; None = no timeout (explicit opt-out)

    def with_headers(self, extra: Mapping[str, str]) -> RequestSpec:
        merged = {k.lower(): v for k, v in self.headers}
        merged.update({k.lower(): v for k, v in extra.items()})
        return RequestSpec(
            method=self.method,
            url=self.url,
            uurl=self.uurl,
            headers=tuple(sorted(merged.items())),
            content=self.content,
            timeout=self.timeout,
        )


@dataclass(slots=True)
class Response:
    """Transport-agnostic HTTP response. Body is fully buffered bytes."""

    status: int
    headers: tuple[tuple[str, str], ...]  # lowercase keys
    body: bytes
    url: str
    elapsed: float  # seconds
    http_version: str = "HTTP/1.1"
    is_replay: bool = False
    _json_cache: Any = field(default=None, repr=False, compare=False)
    _json_parsed: bool = field(default=False, repr=False, compare=False)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if not self._json_parsed:
            self._json_cache = _json.loads(self.body)
            self._json_parsed = True
        return self._json_cache

    def header(self, name: str, default: str | None = None) -> str | None:
        name = name.lower()
        for k, v in self.headers:
            if k == name:
                return v
        return default


@dataclass(slots=True)
class RetryState:
    """Exposed to after-hooks and log placeholders during retries."""

    attempt: int  # current attempt number, 1-based (1 = first RETRY)
    max_attempts: int
    delay: float  # seconds slept before this attempt
    cause: str  # human-readable reason for retrying
    last_status: int | None = None


@dataclass(slots=True)
class RequestContext:
    """Per-request context handed to hooks and log templating."""

    method: str
    url: str
    uurl: str
    endpoint: str  # endpoint template as declared (path only)
    endpoint_args: dict[str, str]
    priority: int = 0
    attempt: int = 0  # 0 = initial attempt
    state: dict[str, Any] = field(default_factory=dict)  # user scratch space, per request
    elapsed: float | None = None  # set after each attempt


REQUEST_ERROR_STATUS: Final = 0  # sentinel bucket for transport errors in metrics
