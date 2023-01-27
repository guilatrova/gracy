from __future__ import annotations

import logging
import typing
from http import HTTPStatus
from typing import Any, Callable, Iterable, TypeVar, cast

import httpx
from typing_extensions import Self

from gracy import exceptions
from gracy.models import DEFAULT_CONFIG, UNSET_VALUE, BaseEndpoint, GracefulMethod, GracyConfig, Unset

logger = logging.getLogger(__name__)


Endpoint = TypeVar("Endpoint", bound=BaseEndpoint | str)  # , default=str)


def _gracify(gracy: GracefulMethod):
    def _wrapper(instance: Gracy[str], *args: Any, **kwargs: Any):
        active_config = GracyConfig.merge_config(instance.base_config, gracy.config)

        result = gracy.method(instance, *args, **kwargs)
        http_result = HTTPStatus(result.status_code)

        if active_config.strict_status_code:
            if not isinstance(active_config.strict_status_code, Unset):
                strict_statuses = active_config.strict_status_code
                if not isinstance(strict_statuses, Iterable):
                    strict_statuses = {strict_statuses}

                if http_result not in strict_statuses:
                    raise exceptions.UnexpectedResponse(str(result.url), result, strict_statuses)

        if not result.is_success:
            if active_config.allowed_status_code:
                if not isinstance(active_config.allowed_status_code, Unset):
                    allowed = active_config.allowed_status_code
                    if not isinstance(allowed, Iterable):
                        allowed = {allowed}

                    if http_result not in allowed:
                        raise exceptions.NonOkResponse(str(result.url), result)

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


class Gracy(typing.Generic[Endpoint], metaclass=GracyMeta):  # type: ignore
    """Helper class that provides a standard way to create an Requester using
    inheritance.
    """

    class Config:
        BASE_URL: str = ""

    def __init__(self) -> None:
        self.base_config = DEFAULT_CONFIG

        event_hooks = {}  # {"request": self._before_request, "response": self._on_response}

        self._client = httpx.Client(base_url=self.Config.BASE_URL, event_hooks=event_hooks)

    def _get(
        self,
        endpoint: Endpoint,
        format: dict[str, str] | None = None,
        *args: typing.Any,
        **kwargs: typing.Any,
    ):
        if format:
            endpoint = endpoint.format(**format)

        return self._client.get(
            endpoint,
            *args,
            **kwargs,
        )


AnyRequesterMethod = TypeVar("AnyRequesterMethod", bound=Callable[..., httpx.Response])


def graceful(
    strict_status_code: Iterable[HTTPStatus] | HTTPStatus | Unset = UNSET_VALUE,
    allowed_status_code: Iterable[HTTPStatus] | HTTPStatus | Unset = UNSET_VALUE,
):
    config = GracyConfig(
        strict_status_code=strict_status_code,
        allowed_status_code=allowed_status_code,
    )

    def _inner_frame(wrapped_function: AnyRequesterMethod) -> AnyRequesterMethod:
        framed = GracefulMethod(config, wrapped_function)
        return cast(AnyRequesterMethod, framed)

    return _inner_frame
