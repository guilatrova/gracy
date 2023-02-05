from __future__ import annotations

import copy
import itertools
import logging
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum
from http import HTTPStatus
from threading import Lock
from time import time
from typing import Any, Awaitable, Callable, Final, Iterable, Literal, Pattern, TypeVar

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
    custom_message: str | None = None
    """You can add some placeholders to be injected in the log.

    e.g.
      - `{URL} executed`
      - `API replied {STATUS} and took {ELAPSED}`
      - `{METHOD} {URL} returned {STATUS}`
      - `Becareful because {URL} is flaky`

    ### Allowed placeholders:

    | Placeholder        | Description                                           | Example                                     | Supported Events     |
    | ------------------ | ----------------------------------------------------- | ------------------------------------------- | -------------------- |
    | `{URL}`            | Full url being targetted                              | `https://pokeapi.co/api/v2/pokemon/pikachu` | *All*                | # noqa: E501
    | `{UURL}`           | Full **Unformatted** url being targetted              | `https://pokeapi.co/api/v2/pokemon/{NAME}`  | *All*                |
    | `{ENDPOINT}`       | Endpoint being targetted                              | `/pokemon/pikachu`                          | *All*                |
    | `{UENDPOINT}`      | **Unformatted** endpoint being targetted              | `/pokemon/{NAME}`                           | *All*                |
    | `{METHOD}`         | HTTP Request being used                               | `GET`, `POST`                               | *All*                |
    | `{STATUS}`         | Status code returned by the response                  | `200`, `404`, `501`                         | *All*                |
    | `{ELAPSED}`        | Amount of seconds taken for the request to complete   | *Numeric*                                   | *All*                |
    | `{RETRY_DELAY}`    | How long Gracy will wait before repeating the request | *Numeric*                                   | *Any Retry event*    |
    | `{CUR_ATTEMPT}`    | Current attempt count for the current request         | *Numeric*                                   | *Any Retry event*    |
    | `{MAX_ATTEMPT}`    | Max attempt defined for the current request           | *Numeric*                                   | *Any Retry event*    |
    | `{THROTTLE_LIMIT}` | How many reqs/s is defined for the current request    | *Numeric*                                   | *Any Throttle event* |
    | `{THROTTLE_TIME}`  | How long Gracy will wait before calling the request   | *Numeric*                                   | *Any Throttle event* |
    """


LOG_EVENT_TYPE = None | Unset | LogEvent


class GracefulRetryState:
    cur_attempt: int = 1
    success: bool = False

    def __init__(self, retry_config: GracefulRetry) -> None:
        self._retry_config = retry_config
        self._delay = retry_config.delay

    @property
    def delay(self) -> float:
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

    def increment(self):
        self.cur_attempt += 1

        if self.cur_attempt > 1:
            self._delay *= self._retry_config.delay_modifier


@dataclass
class GracefulRetry:
    delay: float
    max_attempts: int

    delay_modifier: float = 1
    retry_on: HTTPStatus | Iterable[HTTPStatus] | None = None
    log_before: None | LogEvent = None
    log_after: None | LogEvent = None
    log_exhausted: None | LogEvent = None
    behavior: Literal["break", "pass"] = "break"

    def needs_retry(self, response_result: HTTPStatus) -> bool:
        if self.retry_on is None:
            return True

        retry_on_status = self.retry_on
        if not isinstance(retry_on_status, Iterable):
            retry_on_status = {retry_on_status}

        return response_result in retry_on_status

    def create_state(self) -> GracefulRetryState:
        return GracefulRetryState(self)


class ThrottleRule:
    url_pattern: Pattern[str]
    """
    Which URLs do you want to account for this?
    e.g.
        Strict values:
        - `"https://myapi.com/endpoint"`

        Regex values:
        - `"https://.*"`
        - `"http(s)?://myapi.com/.*"`
    """

    requests_per_second_limit: float
    """
    How many requests should be run per second
    """

    def __init__(self, url_regex: str, limit_per_second: int) -> None:
        self.url_pattern = re.compile(url_regex)
        self.requests_per_second_limit = limit_per_second

    def calculate_await_time(self, controller: ThrottleController) -> float:
        """
        Checks current reqs/second and awaits if limit is reached.
        Returns whether limit was hit or not.
        """
        rate_limit = self.requests_per_second_limit
        cur_reqs_second = controller.calculate_requests_per_second(self.url_pattern)

        if cur_reqs_second >= rate_limit:
            time_diff = (rate_limit - cur_reqs_second) or 1
            waiting_time = 1.0 / time_diff
            return waiting_time

        return 0.0


class ThrottleLocker:
    def __init__(self) -> None:
        self._regex_lock = defaultdict[Pattern[str], Lock](Lock)
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


THROTTLE_LOCKER: Final = ThrottleLocker()


class GracefulThrottle:
    rules: list[ThrottleRule] = []
    log_limit_reached: None | LogEvent = None
    log_wait_over: None | LogEvent = None

    def __init__(
        self,
        rules: list[ThrottleRule] | ThrottleRule,
        log_limit_reached: None | LogEvent = None,
        log_wait_over: None | LogEvent = None,
    ) -> None:
        self.rules = rules if isinstance(rules, Iterable) else [rules]
        self.log_limit_reached = log_limit_reached
        self.log_wait_over = log_wait_over


class ThrottleController:
    def __init__(self) -> None:
        self._control: dict[str, list[float]] = defaultdict[str, list[float]](list)

    def init_request(self, request_context: GracyRequestContext):
        with THROTTLE_LOCKER.lock_check():
            self._control[request_context.url].append(time())  # This should always keep it sorted asc

    def calculate_requests_per_second(self, url_pattern: Pattern[str]) -> float:
        with THROTTLE_LOCKER.lock_check():
            up_to_one_sec = time() - 1
            requests_per_second = 0.0
            coalesced_started_ats = sorted(
                itertools.chain(*[started_ats for url, started_ats in self._control.items() if url_pattern.match(url)])
            )

            if coalesced_started_ats:
                requests = [request for request in coalesced_started_ats if request >= up_to_one_sec]
                requests_per_second = len(requests) / 1

            return requests_per_second

    def calculate_requests_rate(self, url_pattern: Pattern[str]) -> float:
        with THROTTLE_LOCKER.lock_check():
            requests_per_second = 0.0
            coalesced_started_ats = sorted(
                itertools.chain(*[started_ats for url, started_ats in self._control.items() if url_pattern.match(url)])
            )

            if coalesced_started_ats:
                # Best effort to measure rate if we just performed 1 request
                last = coalesced_started_ats[-1] if len(coalesced_started_ats) > 1 else time()
                start = coalesced_started_ats[0]
                elapsed = last - start

                if elapsed > 0:
                    requests_per_second = len(coalesced_started_ats) / elapsed

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
            human_times = [datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f") for ts in times]
            table.add_row(url, f"{len(times):,}", str(human_times))

        console.print(table)


@dataclass
class GracyConfig:
    log_request: LOG_EVENT_TYPE = UNSET_VALUE
    log_response: LOG_EVENT_TYPE = UNSET_VALUE
    log_errors: LOG_EVENT_TYPE = UNSET_VALUE

    retry: GracefulRetry | None | Unset = UNSET_VALUE

    strict_status_code: Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE
    """Strictly enforces only one or many HTTP Status code to be considered as successful.

    e.g. Setting it to 201 would raise exceptions for both 204 or 200"""

    allowed_status_code: Iterable[HTTPStatus] | HTTPStatus | None | Unset = UNSET_VALUE
    """Adds one or many HTTP Status code that would normally be considered an error

    e.g. 404 would consider any 200-299 and 404 as successful.

    NOTE: `strict_status_code` takes precedence.
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

    def should_retry(self, response_status: int) -> bool:
        if self.has_retry:
            retry: GracefulRetry = self.retry  # type: ignore
            if retry.retry_on is None:
                return True

            if isinstance(retry.retry_on, Iterable):
                return HTTPStatus(response_status) in retry.retry_on

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


DEFAULT_CONFIG: Final = GracyConfig(
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


Endpoint = TypeVar("Endpoint", bound=BaseEndpoint | str)  # , default=str)


class GracefulRequest:
    request: Callable[..., Awaitable[httpx.Response]]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]

    def __init__(self, request: Callable[..., Awaitable[httpx.Response]], *args: Any, **kwargs: dict[str, Any]) -> None:
        self.request = request
        self.args = args
        self.kwargs = kwargs

    def __call__(self) -> Awaitable[httpx.Response]:
        return self.request(*self.args, **self.kwargs)


class GracyRequestContext:
    def __init__(
        self,
        method: str,
        base_url: str,
        endpoint: str,
        endpoint_args: dict[str, str] | None,
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
