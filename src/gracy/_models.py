from __future__ import annotations

import copy
import inspect
import itertools
import logging
import re
import typing as t
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, IntEnum
from http import HTTPStatus
from threading import Lock

import httpx

from ._types import PARSER_TYPE, UNSET_VALUE, Unset


class LogLevel(IntEnum):
    CRITICAL = logging.CRITICAL
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    INFO = logging.INFO
    DEBUG = logging.DEBUG
    NOTSET = logging.NOTSET


@dataclass
class LogEvent:
    level: LogLevel
    custom_message: t.Callable[[httpx.Response | None], str] | str | None = None
    """You can add some placeholders to be injected in the log.

    e.g.
      - `{URL} executed`
      - `API replied {STATUS} and took {ELAPSED}`
      - `{METHOD} {URL} returned {STATUS}`
      - `Becareful because {URL} is flaky`

    Placeholders may change depending on the context. Check the docs to see all available placeholder.
    """


LOG_EVENT_TYPE = t.Union[None, Unset, LogEvent]


class GracefulRetryState:
    cur_attempt: int = 0
    success: bool = False

    last_exc: Exception | None = None
    last_response: httpx.Response | None

    def __init__(self, retry_config: GracefulRetry) -> None:
        self._retry_config = retry_config
        self._delay = retry_config.delay
        self._override_delay: float | None = None

    @property
    def delay(self) -> float:
        if self._override_delay is not None:
            return self._override_delay

        return self._delay

    @property
    def failed(self) -> bool:
        return not self.success

    @property
    def max_attempts(self):
        return self._retry_config.max_attempts

    @property
    def can_retry(self):
        return self.cur_attempt <= self.max_attempts

    @property
    def cant_retry(self):
        return not self.can_retry

    @property
    def cause(self) -> str:
        """Describes why the Retry was triggered"""

        # Importing here to avoid cyclic imports
        from gracy.exceptions import GracyRequestFailed, GracyUserDefinedException, NonOkResponse, UnexpectedResponse

        if self.success:
            return "SUCCESSFUL"

        exc = self.last_exc

        if self.last_response:
            if isinstance(exc, NonOkResponse) or isinstance(exc, UnexpectedResponse):
                return f"[Bad Status Code: {self.last_response.status_code}]"

            if isinstance(exc, GracyUserDefinedException):
                return f"[User Error: {type(exc).__name__}]"

        if exc:
            if isinstance(exc, GracyRequestFailed):
                return f"[Request Error: {type(exc.original_exc).__name__}]"

            return f"[{type(exc).__name__}]"

        # This final block is unlikely to ever happen
        resp = t.cast(httpx.Response, self.last_response)
        return f"[Bad Status Code: {resp.status_code}]"

    def increment(self, response: httpx.Response | None):
        self.cur_attempt += 1

        if self.cur_attempt > 1:
            self._delay *= self._retry_config.delay_modifier

        self._override_delay = None
        if response and self._retry_config.overrides and self._retry_config.overrides.get(response.status_code):
            self._override_delay = self._retry_config.overrides[response.status_code].delay


STATUS_OR_EXCEPTION = t.Union[int, t.Type[Exception]]


@dataclass
class OverrideRetryOn:
    delay: float


@dataclass
class GracefulRetry:
    delay: float
    max_attempts: int

    delay_modifier: float = 1
    retry_on: STATUS_OR_EXCEPTION | t.Iterable[STATUS_OR_EXCEPTION] | None = None
    log_before: LogEvent | None = None
    log_after: LogEvent | None = None
    log_exhausted: LogEvent | None = None
    behavior: t.Literal["break", "pass"] = "break"
    overrides: t.Union[t.Dict[int, OverrideRetryOn], None] = None

    def needs_retry(self, response_result: int) -> bool:
        if self.retry_on is None:
            return True

        retry_on_status = self.retry_on
        if not isinstance(retry_on_status, t.Iterable):
            retry_on_status = {retry_on_status}

        return response_result in retry_on_status

    def create_state(self, result: httpx.Response | None, exc: Exception | None) -> GracefulRetryState:
        state = GracefulRetryState(self)
        state.last_response = result
        state.last_exc = exc
        return state


class ThrottleRule:
    url_pattern: t.Pattern[str]
    """
    Which URLs do you want to account for this?
    e.g.
        Strict values:
        - `"https://myapi.com/endpoint"`

        Regex values:
        - `"https://.*"`
        - `"http(s)?://myapi.com/.*"`
    """

    max_requests: int
    """
    How many requests should be run `per_time_range`
    """

    per_time_range: timedelta
    """
    Used in combination with `max_requests` to measure throttle
    """

    def __init__(self, url_pattern: str, max_requests: int, per_time_range: timedelta = timedelta(seconds=1)) -> None:
        self.url_pattern = re.compile(url_pattern)
        self.max_requests = max_requests
        self.per_time_range = per_time_range

        if isinstance(max_requests, float):
            raise TypeError(f"{max_requests=} should be an integer")

    @property
    def readable_time_range(self) -> str:
        seconds = self.per_time_range.total_seconds()
        periods = {
            ("hour", 3600),
            ("minute", 60),
            ("second", 1),
        }

        parts: list[str] = []
        for period_name, period_seconds in periods:
            if seconds >= period_seconds:
                period_value, seconds = divmod(seconds, period_seconds)
                if period_value == 1:
                    parts.append(period_name)
                else:
                    parts.append(f"{int(period_value)} {period_name}s")

            if seconds < 1:
                break

        if len(parts) == 1:
            return parts[0]
        else:
            return ", ".join(parts[:-1]) + " and " + parts[-1]

    def __str__(self) -> str:
        return f"{self.max_requests} requests per {self.readable_time_range} for URLs matching {self.url_pattern}"

    def calculate_await_time(self, controller: ThrottleController) -> float:
        """
        Checks current reqs/second and awaits if limit is reached.
        Returns whether limit was hit or not.
        """
        rate_limit = self.max_requests
        cur_rate = controller.calculate_requests_per_rule(self.url_pattern, self.per_time_range)

        if cur_rate >= rate_limit:
            time_diff = (rate_limit - cur_rate) or 1
            waiting_time = self.per_time_range.total_seconds() / time_diff
            return waiting_time

        return 0.0


class ThrottleLocker:
    def __init__(self) -> None:
        self._regex_lock = t.DefaultDict[t.Pattern[str], Lock](Lock)
        self._generic_lock = Lock()

    @contextmanager
    def lock_rule(self, rule: ThrottleRule):
        with self._regex_lock[rule.url_pattern] as lock:
            yield lock

    @contextmanager
    def lock_check(self):
        with self._generic_lock as lock:
            yield lock

    def is_rule_throttled(self, rule: ThrottleRule) -> bool:
        return self._regex_lock[rule.url_pattern].locked()


THROTTLE_LOCKER: t.Final = ThrottleLocker()


class GracefulThrottle:
    rules: list[ThrottleRule] = []
    log_limit_reached: LogEvent | None = None
    log_wait_over: LogEvent | None = None

    def __init__(
        self,
        rules: list[ThrottleRule] | ThrottleRule,
        log_limit_reached: LogEvent | None = None,
        log_wait_over: LogEvent | None = None,
    ) -> None:
        self.rules = rules if isinstance(rules, t.Iterable) else [rules]
        self.log_limit_reached = log_limit_reached
        self.log_wait_over = log_wait_over


class ThrottleController:
    def __init__(self) -> None:
        self._control: dict[str, list[datetime]] = t.DefaultDict[str, t.List[datetime]](list)

    def init_request(self, request_context: GracyRequestContext):
        with THROTTLE_LOCKER.lock_check():
            self._control[request_context.url].append(datetime.now())  # This should always keep it sorted asc

    def calculate_requests_per_rule(self, url_pattern: t.Pattern[str], range: timedelta) -> float:
        with THROTTLE_LOCKER.lock_check():
            past_time_window = datetime.now() - range
            request_rate = 0.0

            request_times = sorted(
                itertools.chain(
                    *[started_ats for url, started_ats in self._control.items() if url_pattern.match(url)],
                ),
                reverse=True,
            )

            req_idx = 0
            total_reqs = len(request_times)
            while req_idx < total_reqs:
                # e.g. Limit 4 requests per 2 seconds, now is 09:55
                # request_time=09:54 >= past_time_window=09:53
                if request_times[req_idx] >= past_time_window:
                    request_rate += 1
                else:
                    # Because it's sorted desc there's no need to keep iterating
                    return request_rate

                req_idx += 1

            return request_rate

    def calculate_requests_per_sec(self, url_pattern: t.Pattern[str]) -> float:
        with THROTTLE_LOCKER.lock_check():
            requests_per_second = 0.0
            coalesced_started_ats = sorted(
                itertools.chain(*[started_ats for url, started_ats in self._control.items() if url_pattern.match(url)])
            )

            if coalesced_started_ats:
                # Best effort to measure rate if we just performed 1 request
                last = coalesced_started_ats[-1] if len(coalesced_started_ats) > 1 else datetime.now()
                start = coalesced_started_ats[0]
                elapsed = last - start

                if elapsed.seconds > 0:
                    requests_per_second = len(coalesced_started_ats) / elapsed.seconds

            return requests_per_second

    def debug_print(self):
        # Intended only for local development
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Throttling Summary")
        table.add_column("URL", overflow="fold")
        table.add_column("Count", justify="right")
        table.add_column("Times", justify="right")

        for url, times in self._control.items():
            human_times = [time.strftime("%H:%M:%S.%f") for time in times]
            table.add_row(url, f"{len(times):,}", f"[yellow]{human_times}[/yellow]")

        console.print(table)


class GracefulValidator(ABC):
    """
    Run `check` raises exceptions in case it's not passing.
    """

    @abstractmethod
    def check(self, response: httpx.Response) -> None:
        """Returns `None` to pass or raise exception"""
        pass


@dataclass
class ConcurrentRequestLimit:
    """
    Limits how many concurrent calls for a specific endpoint can be active.

    e.g. If you limit 10 requests to the endpoing /xyz
    """

    limit: int
    uurl_pattern: t.Pattern[str] = re.compile(".*")
    blocking_args: t.Optional[t.Iterable[str]] = None
    """
    Combine endpoint args to decide whether to limit.
    Optional, leaving it blank means that any request to the endpoint will be blocked
    """

    free_on_capacitity: float = 0

    log_limit_reached: LOG_EVENT_TYPE = None
    log_limit_freed: LOG_EVENT_TYPE = None

    def get_blocking_key(self, request_context: GracyRequestContext) -> t.Tuple[str, ...]:
        if self.blocking_args:
            args = [request_context.endpoint_args.get(arg, "") for arg in self.blocking_args]
            return (request_context.unformatted_url, *args)

        return (request_context.unformatted_url,)

    def should_free(self, done: int) -> bool:
        if self.free_on_capacitity == 0:
            return done == self.limit

        cur_cap = (done / self.limit) * 100
        return cur_cap >= self.free_on_capacitity


CONCURRENT_REQUEST_TYPE = t.Union[t.Iterable[ConcurrentRequestLimit], ConcurrentRequestLimit, None, Unset]


@dataclass
class GracyConfig:
    log_request: LOG_EVENT_TYPE = UNSET_VALUE
    log_response: LOG_EVENT_TYPE = UNSET_VALUE
    log_errors: LOG_EVENT_TYPE = UNSET_VALUE

    retry: GracefulRetry | None | Unset = UNSET_VALUE

    strict_status_code: t.Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE
    """Strictly enforces only one or many HTTP Status code to be considered as successful.

    e.g. Setting it to 201 would raise exceptions for both 204 or 200"""

    allowed_status_code: t.Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE
    """Adds one or many HTTP Status code that would normally be considered an error

    e.g. 404 would consider any 200-299 and 404 as successful.

    NOTE: `strict_status_code` takes precedence.
    """

    validators: t.Iterable[GracefulValidator] | GracefulValidator | None | Unset = UNSET_VALUE
    """Adds one or many validators to be run for the response to decide whether it was successful or not.

    NOTE: `strict_status_code` or `allowed_status_code` are executed before.
    If none is set, it will first check whether the response has a successful code.
    """

    parser: PARSER_TYPE = UNSET_VALUE
    """
    Tell Gracy how to deal with the responses for you.

    Examples:
        - `"default": lambda response: response.json()`
        - `HTTPStatus.OK: lambda response: response.json()["ok_data"]`
        - `HTTPStatus.NOT_FOUND: None`
        - `HTTPStatus.INTERNAL_SERVER_ERROR: UserDefinedServerException`
    """

    throttling: GracefulThrottle | None | Unset = UNSET_VALUE

    concurrent_requests: CONCURRENT_REQUEST_TYPE = UNSET_VALUE

    def should_retry(self, response: httpx.Response | None, req_or_validation_exc: Exception | None) -> bool:
        """Only checks if given status requires retry. Does not consider attempts."""

        if self.has_retry:
            retry = t.cast(GracefulRetry, self.retry)

            retry_on: t.Iterable[STATUS_OR_EXCEPTION]
            if not isinstance(retry.retry_on, t.Iterable) and retry.retry_on is not None:
                retry_on = [retry.retry_on]
            elif retry.retry_on is None:
                retry_on = []
            else:
                retry_on = retry.retry_on

            if response is None:
                if retry.retry_on is None:
                    return True

                for maybe_exc in retry_on:
                    if inspect.isclass(maybe_exc) and isinstance(req_or_validation_exc, maybe_exc):
                        return True

                return False

            response_status = response.status_code

            if retry.retry_on is None:
                if req_or_validation_exc or response.is_success is False:
                    return True

            if isinstance(retry.retry_on, t.Iterable):
                if response_status in retry.retry_on:
                    return True

                for maybe_exc in retry.retry_on:
                    if inspect.isclass(maybe_exc) and isinstance(req_or_validation_exc, maybe_exc):
                        return True

            elif inspect.isclass(retry.retry_on):
                return isinstance(req_or_validation_exc, retry.retry_on)

            else:
                return retry.retry_on == response_status

        return False

    @property
    def has_retry(self) -> bool:
        return self.retry is not None and self.retry != UNSET_VALUE

    @classmethod
    def merge_config(cls, base: GracyConfig, modifier: GracyConfig):
        new_obj = copy.copy(base)

        for key, value in vars(modifier).items():
            if getattr(base, key) == UNSET_VALUE:
                setattr(new_obj, key, value)
            elif value != UNSET_VALUE:
                setattr(new_obj, key, value)

        return new_obj

    def get_concurrent_limit(self, context: GracyRequestContext) -> t.Optional[ConcurrentRequestLimit]:
        if isinstance(self.concurrent_requests, Unset) or self.concurrent_requests is None:
            return None

        if isinstance(self.concurrent_requests, ConcurrentRequestLimit):
            if self.concurrent_requests.uurl_pattern.match(context.unformatted_url):
                return self.concurrent_requests

            return None

        for rule in self.concurrent_requests:
            if rule.uurl_pattern.match(context.unformatted_url):
                return rule

        return None


DEFAULT_CONFIG: t.Final = GracyConfig(
    log_request=None,
    log_response=None,
    log_errors=LogEvent(LogLevel.ERROR),
    strict_status_code=None,
    allowed_status_code=None,
    retry=None,
)


class BaseEndpoint(str, Enum):
    def __str__(self) -> str:
        return self.value


Endpoint = t.TypeVar("Endpoint", bound=t.Union[BaseEndpoint, str])  # , default=str)


class GracefulRequest:
    request: httpx.Request
    request_func: t.Callable[..., t.Awaitable[httpx.Response]]
    """Can't use coroutine because we need to retrigger it during retries, and coro can't be awaited twice"""
    args: tuple[t.Any, ...]
    kwargs: dict[str, t.Any]

    def __init__(
        self,
        request: httpx.Request,
        request_func: t.Callable[..., t.Awaitable[httpx.Response]],
        *args: t.Any,
        **kwargs: t.Any,
    ) -> None:
        self.request = request
        self.request_func = request_func
        self.args = args
        self.kwargs = kwargs

    def __call__(self) -> t.Awaitable[httpx.Response]:
        return self.request_func(*self.args, **self.kwargs)


class GracyRequestContext:
    def __init__(
        self,
        method: str,
        base_url: str,
        endpoint: str,
        endpoint_args: t.Union[t.Dict[str, str], None],
        active_config: GracyConfig,
    ) -> None:
        if base_url.endswith("/"):
            base_url = base_url[:-1]

        final_endpoint = endpoint.format(**endpoint_args) if endpoint_args else endpoint

        self.endpoint_args = endpoint_args or {}
        self.endpoint = final_endpoint
        self.unformatted_endpoint = endpoint

        self.url = f"{base_url}{self.endpoint}"
        self.unformatted_url = f"{base_url}{self.unformatted_endpoint}"

        self.method = method
        self._active_config = active_config

    @property
    def active_config(self) -> GracyConfig:
        return self._active_config
