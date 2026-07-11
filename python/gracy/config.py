"""Declarative configuration surface. Frozen dataclasses; UNSET = inherit, None = disable.

ONE precedence chain, resolved once at build():
    api.options() > endpoint decorator kwargs > URL-glob overrides > namespace > client > library defaults
"""

from __future__ import annotations

import dataclasses
import fnmatch
import random
import re
import typing as t
from dataclasses import dataclass, field
from enum import IntEnum

from gracy._types import UNSET, Unset, pick
from gracy.exceptions import GracyConfigError

# --------------------------------------------------------------------------- logging


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


@dataclass(frozen=True, slots=True)
class LogEvent:
    level: LogLevel
    custom_message: str | None = None
    """Placeholders: {URL} {UURL} {METHOD} {STATUS} {ELAPSED} {REPLAY} {IS_REPLAY}
    {RETRY_ATTEMPT} {RETRY_DELAY} {RETRY_CAUSE} {MAX_ATTEMPTS} {THROTTLE_LIMIT}
    {THROTTLE_WAIT} + UPPERCASE endpoint args."""


# --------------------------------------------------------------------------- durations


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)\s*$")
_DURATION_FACTOR = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str | float | int) -> float:
    """'500ms' | '1s' | '2m' | '1h' | number-of-seconds -> seconds (float)."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION_RE.match(value)
    if not m:
        raise GracyConfigError(f"Invalid duration {value!r}; use e.g. '500ms', '1s', '2m', '1h' or seconds as number")
    return float(m.group(1)) * _DURATION_FACTOR[m.group(2)]


# --------------------------------------------------------------------------- retry


@dataclass(frozen=True, slots=True)
class Backoff:
    initial: float = 1.0
    multiplier: float = 1.0
    max: float | None = None
    jitter: bool = False

    def compute(self, attempt: int) -> float:
        """Delay before retry attempt N (1-based)."""
        delay = self.initial * (self.multiplier ** (attempt - 1))
        if self.max is not None:
            delay = min(delay, self.max)
        if self.jitter:
            delay *= random.uniform(0.5, 1.5)
        return delay


@dataclass(frozen=True, slots=True)
class StatusSet:
    codes: tuple[int, ...]


def status(*codes: int) -> StatusSet:
    """Match helper for Retry(on=...): gracy.status(429, 503)."""
    return StatusSet(tuple(int(c) for c in codes))


RetryOn = t.Union[StatusSet, type[BaseException], t.Tuple[t.Union[StatusSet, type[BaseException]], ...]]


@dataclass(frozen=True, slots=True)
class Retry:
    on: RetryOn
    attempts: int = 3
    wait: float | Backoff = 1.0
    overrides: t.Mapping[int, float] | None = None  # per-status delay override (v1 OverrideRetryOn)
    respect_retry_after: bool = True
    on_exhausted: t.Literal["raise", "return"] = "raise"
    suppress: bool = False  # never raise, even mid-retry failures (v1 behavior="pass")
    log_before: LogEvent | None = None
    log_after: LogEvent | None = None
    log_exhausted: LogEvent | None = None

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise GracyConfigError("Retry.attempts must be >= 1")

    def _matchers(self) -> tuple[tuple[t.Union[StatusSet, type[BaseException]], ...], ...]:
        on = self.on if isinstance(self.on, tuple) else (self.on,)
        return (on,)

    def matches(self, status_code: int | None, exc: BaseException | None) -> bool:
        """Should this (status, exception) trigger a retry?"""
        from gracy.exceptions import GracyRequestFailed

        on = self.on if isinstance(self.on, tuple) else (self.on,)
        original = exc.original_exc if isinstance(exc, GracyRequestFailed) else exc
        for item in on:
            if isinstance(item, StatusSet):
                if status_code is not None and status_code in item.codes:
                    return True
            elif isinstance(item, type) and issubclass(item, BaseException):
                for candidate in (exc, original):
                    if candidate is not None and isinstance(candidate, item):
                        return True
            else:  # pragma: no cover - guarded by config validation
                raise GracyConfigError(f"Invalid Retry.on item: {item!r}")
        return False

    def delay_for(self, attempt: int, status_code: int | None) -> float:
        if self.overrides and status_code is not None and status_code in self.overrides:
            return float(self.overrides[status_code])
        if isinstance(self.wait, Backoff):
            return self.wait.compute(attempt)
        return float(self.wait)


# --------------------------------------------------------------------------- throttle / queue / concurrency


@dataclass(frozen=True, slots=True)
class Rate:
    limit: int
    per: str | float = "1s"
    match: str = r".*"  # regex, matched against the FORMATTED url

    @property
    def per_seconds(self) -> float:
        return parse_duration(self.per)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise GracyConfigError("Rate.limit must be >= 1")
        try:
            re.compile(self.match)
        except re.error as e:
            raise GracyConfigError(f"Invalid Rate.match regex {self.match!r}: {e}") from e
        self.per_seconds  # validate eagerly


@dataclass(frozen=True, slots=True)
class Throttle:
    rules: tuple[Rate, ...] = ()
    mode: t.Literal["exact", "smooth"] = "exact"
    log_limit_reached: LogEvent | None = None
    log_wait_over: LogEvent | None = None

    def __init__(
        self,
        rules: t.Iterable[Rate] = (),
        mode: t.Literal["exact", "smooth"] = "exact",
        log_limit_reached: LogEvent | None = None,
        log_wait_over: LogEvent | None = None,
    ) -> None:
        object.__setattr__(self, "rules", tuple(rules))
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "log_limit_reached", log_limit_reached)
        object.__setattr__(self, "log_wait_over", log_wait_over)


@dataclass(frozen=True, slots=True)
class Concurrency:
    limit: int
    match: str | None = None  # regex vs the UNFORMATTED url (uurl); None = all requests
    per_uurl: bool = False  # separate semaphore per endpoint template
    key_by: tuple[str, ...] = ()  # endpoint arg names partitioning the semaphore (v1 blocking_args)
    log_limit_reached: LogEvent | None = None
    log_limit_freed: LogEvent | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise GracyConfigError("Concurrency.limit must be >= 1")
        if self.match is not None:
            try:
                re.compile(self.match)
            except re.error as e:
                raise GracyConfigError(f"Invalid Concurrency.match regex {self.match!r}: {e}") from e


@dataclass(frozen=True, slots=True)
class Queue:
    """Queue admission knobs.

    There is NO cap on how many requests a caller may have outstanding: with
    the default ``on_full="wait"`` a submit never fails - gracy manages the
    backlog. ``max_pending`` only bounds how many submits actively contend on
    the throttle/concurrency machinery at once; everything beyond it parks as
    a tiny priority-heap entry (no timers, no polling) and is admitted in
    priority order as capacity frees. Set ``on_full="raise"`` ONLY when you
    explicitly want load shedding (``GracyQueueFull``).
    """

    max_at_once: int | None = None  # global in-flight cap (implemented as a global concurrency rule)
    max_pending: int = 10_000
    on_full: t.Literal["wait", "raise"] = "wait"
    pause_on_status: t.Mapping[int, t.Literal["endpoint", "client"]] | None = None
    throttle_in_hooks: bool = False


# --------------------------------------------------------------------------- status policy


@dataclass(frozen=True, slots=True)
class StatusPolicy:
    kind: t.Literal["strict", "allow", "default"]
    codes: tuple[int, ...] = ()


def strict(*codes: int) -> StatusPolicy:
    """ONLY these codes pass validation (even 200 fails if absent)."""
    if not codes:
        raise GracyConfigError("gracy.strict() needs at least one status code")
    return StatusPolicy("strict", tuple(int(c) for c in codes))


def allow(*codes: int) -> StatusPolicy:
    """2xx OR these codes pass validation."""
    if not codes:
        raise GracyConfigError("gracy.allow() needs at least one status code")
    return StatusPolicy("allow", tuple(int(c) for c in codes))


DEFAULT_STATUS_POLICY = StatusPolicy("default")


# --------------------------------------------------------------------------- parser actions


@dataclass(frozen=True, slots=True)
class Raises:
    exc: type[BaseException]


def raises(exc: type[BaseException]) -> Raises:
    """on={404: raises(NotFoundError)} - raise this exception for the status."""
    if not (isinstance(exc, type) and issubclass(exc, BaseException)):
        raise GracyConfigError(f"raises() expects an exception class, got {exc!r}")
    return Raises(exc)


# on= values: Raises | callable(response) -> Any | any literal (returned as-is, e.g. None)
OnAction = t.Any
OnMap = t.Mapping[t.Union[int, str], OnAction]  # keys: status int or "default"


def validate_on_map(on: OnMap) -> None:
    for key, action in on.items():
        if not (key == "default" or isinstance(key, int)):
            raise GracyConfigError(f"on= keys must be int status codes or 'default', got {key!r}")
        if isinstance(action, type) and issubclass(action, BaseException):
            raise GracyConfigError(
                f"on={{{key}: {action.__name__}}} - bare exception classes are not allowed in v2; "
                f"use raises({action.__name__})"
            )


# --------------------------------------------------------------------------- the aggregate config


@dataclass(frozen=True)
class GracyConfig:
    """All knobs. UNSET = inherit from outer layer; None = explicitly disabled."""

    decoder: t.Any = UNSET  # Decoder protocol
    log_request: LogEvent | None | Unset = UNSET
    log_response: LogEvent | None | Unset = UNSET
    log_errors: LogEvent | None | Unset = UNSET
    retry: Retry | None | Unset = UNSET
    status_policy: StatusPolicy | None | Unset = UNSET
    validators: t.Any = UNSET  # Validator | Sequence[Validator] | None
    on: OnMap | None | Unset = UNSET
    throttle: Throttle | None | Unset = UNSET
    concurrency: Concurrency | int | None | Unset = UNSET
    queue: Queue | Unset = UNSET
    overrides: t.Mapping[str, GracyConfig] | Unset = UNSET  # URL-glob -> partial config
    timeout: float | None | Unset = UNSET

    def __post_init__(self) -> None:
        if self.on not in (None, UNSET) and self.on is not None:
            validate_on_map(t.cast(OnMap, self.on))
        if isinstance(self.concurrency, int) and not isinstance(self.concurrency, bool):
            object.__setattr__(self, "concurrency", Concurrency(limit=self.concurrency))

    def merged_under(self, outer: GracyConfig) -> GracyConfig:
        """Return self layered on top of `outer` (self wins for every set field)."""
        kwargs: dict[str, t.Any] = {}
        for f in dataclasses.fields(self):
            mine = getattr(self, f.name)
            theirs = getattr(outer, f.name)
            kwargs[f.name] = theirs if mine is UNSET else mine
        return GracyConfig(**kwargs)


LIBRARY_DEFAULTS = GracyConfig(
    decoder=None,
    log_request=None,
    log_response=None,
    log_errors=LogEvent(LogLevel.ERROR),
    retry=None,
    status_policy=DEFAULT_STATUS_POLICY,
    validators=None,
    on=None,
    throttle=None,
    concurrency=None,
    queue=Queue(),
    overrides={},
    timeout=30.0,
)


def resolve_chain(*layers: GracyConfig | None) -> GracyConfig:
    """Innermost-last: resolve_chain(client, namespace, endpoint, options).
    Everything merges UNDER library defaults (partial configs keep default error logging)."""
    resolved = LIBRARY_DEFAULTS
    for layer in layers:
        if layer is not None:
            resolved = layer.merged_under(resolved)
    return resolved


def apply_url_overrides(config: GracyConfig, url: str) -> GracyConfig:
    """Apply URL-glob overrides= entries whose pattern matches the formatted URL."""
    overrides = config.overrides
    if not overrides or isinstance(overrides, Unset):
        return config
    resolved = config
    for pattern, partial in overrides.items():
        if fnmatch.fnmatch(url, pattern):
            resolved = partial.merged_under(resolved)
    return resolved
