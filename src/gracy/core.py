from __future__ import annotations

import logging
from asyncio import sleep
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Generic, Iterable, TypeVar

import httpx
from typing_extensions import Self

from gracy.context import custom_gracy_config, gracy_context
from gracy.exceptions import NonOkResponse, UnexpectedResponse
from gracy.models import (
    DEFAULT_CONFIG,
    UNSET_VALUE,
    BaseEndpoint,
    GracefulRequest,
    GracefulRetry,
    GracefulRetryState,
    GracyConfig,
    Unset,
)

logger = logging.getLogger(__name__)


Endpoint = TypeVar("Endpoint", bound=BaseEndpoint | str)  # , default=str)


async def _gracefully_retry(
    retry: GracefulRetry,
    config: GracyConfig,
    gracy_method_name: str,
    request: GracefulRequest,
    check_func: Callable[[GracyConfig, httpx.Response], bool],
) -> GracefulRetryState:
    failing = True
    state = retry.create_state()

    while failing:
        if retry.log_before:
            logger.log(
                retry.log_before.level.value,
                f"GracefulRetry: {gracy_method_name} will wait {state.delay}s before next attempt "
                f"({state.cur_attempt} out of {state.max_attempts})",
            )

        state.increment()

        if state.cant_retry:
            break

        await sleep(state.delay)
        result = await request()

        state.success = check_func(config, result)
        failing = not state.success

        if retry.log_after:
            logger.log(
                retry.log_after.level.value,
                f"GracefulRetry: {gracy_method_name} {'SUCCESS' if state.success else 'FAIL'} "
                f"({state.cur_attempt} out of {state.max_attempts})",
            )

    if state.cant_retry and retry.log_exhausted:
        logger.log(
            retry.log_exhausted.level.value,
            f"GracefulRetry: {gracy_method_name} exhausted the maximum attempts of {state.max_attempts})",
        )

    return state


def _check_strictness(active_config: GracyConfig, result: httpx.Response) -> bool:
    if active_config.strict_status_code:
        if not isinstance(active_config.strict_status_code, Unset):
            strict_statuses = active_config.strict_status_code
            if not isinstance(strict_statuses, Iterable):
                strict_statuses = {strict_statuses}

            if HTTPStatus(result.status_code) not in strict_statuses:
                return False

    return True


def _check_allowed(active_config: GracyConfig, result: httpx.Response) -> bool:
    if not result.is_success:
        if active_config.allowed_status_code:
            if not isinstance(active_config.allowed_status_code, Unset):
                allowed = active_config.allowed_status_code
                if not isinstance(allowed, Iterable):
                    allowed = {allowed}

                if HTTPStatus(result.status_code) not in allowed:
                    return False

    return True


async def _gracify(
    active_config: GracyConfig,
    endpoint: str,
    format_args: dict[str, str] | None,
    request: GracefulRequest,
):
    result = await request()

    strict_pass = _check_strictness(active_config, result)
    if strict_pass is False:
        retry_result = None
        if active_config.should_retry(result.status_code):
            retry_result = await _gracefully_retry(
                active_config.retry,  # type: ignore
                active_config,
                endpoint,
                request=request,
                check_func=_check_strictness,
            )

        if not retry_result or retry_result.failed:
            strict_codes: HTTPStatus | Iterable[HTTPStatus] = active_config.strict_status_code  # type: ignore
            raise UnexpectedResponse(str(result.url), result, strict_codes)

    allowed_pass = _check_allowed(active_config, result)
    if allowed_pass is False:
        if active_config.should_retry(result.status_code):
            retry_result = None
            if active_config.has_retry:
                retry_result = await _gracefully_retry(
                    active_config.retry,  # type: ignore
                    active_config,
                    endpoint,
                    request=request,
                    check_func=_check_allowed,
                )

            if not retry_result or retry_result.failed:
                raise NonOkResponse(str(result.url), result)

    return result


class GracyMeta(type):
    def __new__(
        cls: type[Self],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> Self:
        instance = super().__new__(cls, name, bases, namespace, *args, **kwargs)
        return instance


class Gracy(Generic[Endpoint], metaclass=GracyMeta):
    """Helper class that provides a standard way to create an Requester using
    inheritance.
    """

    class Config:
        BASE_URL: str = ""

    def __init__(self) -> None:
        self._base_config = DEFAULT_CONFIG

        self._client = httpx.AsyncClient(base_url=self.Config.BASE_URL)

    async def _get(
        self,
        endpoint: Endpoint,
        format: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        custom_config = gracy_context.get()
        active_config = self._base_config
        if custom_config:
            active_config = GracyConfig.merge_config(self._base_config, custom_config)

        if format:
            final_endpoint = endpoint.format(**format)
        else:
            final_endpoint = endpoint

        graceful_request = _gracify(
            active_config,
            endpoint,
            format,
            GracefulRequest(
                self._client.get,
                final_endpoint,
                *args,
                kwargs=kwargs,
            ),
        )

        return await graceful_request


def graceful(
    strict_status_code: Iterable[HTTPStatus] | HTTPStatus | Unset = UNSET_VALUE,
    allowed_status_code: Iterable[HTTPStatus] | HTTPStatus | Unset = UNSET_VALUE,
    retry: GracefulRetry | Unset = UNSET_VALUE,
):
    config = GracyConfig(
        strict_status_code=strict_status_code,
        allowed_status_code=allowed_status_code,
        retry=retry,
    )

    def _wrapper(wrapped_function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def _inner_wrapper(*args: Any, **kwargs: Any):
            with custom_gracy_config(config):
                res = await wrapped_function(*args, **kwargs)
                return res

        return _inner_wrapper

    return _wrapper
