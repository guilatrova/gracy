from __future__ import annotations

import logging
from asyncio import sleep
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Generic, Iterable, TypeVar, cast

import httpx
from typing_extensions import Self

from gracy.exceptions import NonOkResponse, UnexpectedResponse
from gracy.models import (
    DEFAULT_CONFIG,
    UNSET_VALUE,
    BaseEndpoint,
    GracefulMethod,
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
    gracy_method: Callable[..., Awaitable[httpx.Response]],
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
        result = await gracy_method()

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


def _gracify(gracy: GracefulMethod):
    async def _wrapper(instance: Gracy[str], *args: Any, **kwargs: Any):
        active_config = GracyConfig.merge_config(instance.base_config, gracy.config)

        result = await gracy.method(instance, *args, **kwargs)

        strict_pass = _check_strictness(active_config, result)
        if strict_pass is False:
            retry_result = None
            if active_config.has_retry:
                retry_result = await _gracefully_retry(
                    active_config.retry,  # type: ignore
                    active_config,
                    gracy.method.__name__,
                    gracy_method=lambda: gracy.method(instance, *args, **kwargs),
                    check_func=_check_strictness,
                )

            if not retry_result or retry_result.failed:
                strict_codes: HTTPStatus | Iterable[HTTPStatus] = active_config.strict_status_code  # type: ignore
                raise UnexpectedResponse(str(result.url), result, strict_codes)

        allowed_pass = _check_allowed(active_config, result)
        if allowed_pass is False:
            if active_config.has_retry is False:
                raise NonOkResponse(str(result.url), result)

        return result

    return _wrapper


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

        for attrname in dir(instance):
            attr = getattr(instance, attrname)

            if isinstance(attr, GracefulMethod):
                setattr(instance, attrname, _gracify(attr))

        return instance


class Gracy(Generic[Endpoint], metaclass=GracyMeta):
    """Helper class that provides a standard way to create an Requester using
    inheritance.
    """

    class Config:
        BASE_URL: str = ""

    def __init__(self) -> None:
        self.base_config = DEFAULT_CONFIG

        self._client = httpx.AsyncClient(base_url=self.Config.BASE_URL)

    async def _get(
        self,
        endpoint: Endpoint,
        format: dict[str, str] | None = None,
        *args: Any,
        **kwargs: Any,
    ):
        if format:
            endpoint = endpoint.format(**format)

        return await self._client.get(
            endpoint,
            *args,
            **kwargs,
        )


AnyRequesterMethod = TypeVar("AnyRequesterMethod", bound=Callable[..., Awaitable[httpx.Response]])


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

    def _inner_frame(wrapped_function: AnyRequesterMethod) -> AnyRequesterMethod:
        framed = GracefulMethod(config, wrapped_function)
        return cast(AnyRequesterMethod, framed)

    return _inner_frame
