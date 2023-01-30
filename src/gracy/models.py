from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from enum import Enum, IntEnum
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Final, Iterable, Literal, Pattern, Type, Union

import httpx

from gracy.throttling import ThrottleController


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

    Allowed placeholders:
    - `STATUS`: Response status code
    - `METHOD`: Method used in the request
    - `URL`: URL request was made to
    - `ELAPSED`: Elapsed milliseconds on request
    - `THROTTLE_LIMIT`: Limit defined for the rule
    - `THROTTLE_TIME`: Time the throttling decided to wait

    e.g.
      - `{URL} executed`
      - `API replied {STATUS} and took {ELAPSED}`
      - `{METHOD} {URL} returned {STATUS}`
      - `Becareful because {URL} is flaky`
    """


class Unset:
    """
    The default "unset" state indicates that whatever default is set on the
    client should be used. This is different to setting `None`, which
    explicitly disables the parameter, possibly overriding a client default.
    """

    def __bool__(self):
        return False


UNSET_VALUE: Final = Unset()

LOG_EVENT_TYPE = None | Unset | LogEvent

PARSER_KEY = HTTPStatus | Literal["default"]
PARSER_VALUE = Union[Type[Exception], Callable[[httpx.Response], Any], None]
PARSER_TYPE = dict[PARSER_KEY, PARSER_VALUE] | Unset | None


class GracefulRetryState:
    cur_attempt: int = 0
    success: bool = False
    delay: float  # TODO: use property

    def __init__(self, retry_config: GracefulRetry) -> None:
        self._retry_config = retry_config
        self.delay = retry_config.delay

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
            self.delay = self.delay * self._retry_config.delay_modifier


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

    throttling: GracefulThrottle | None = None

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
