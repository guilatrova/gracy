from __future__ import annotations

import logging
import typing
from typing import Any, Callable, TypeVar, cast

import httpx
from typing_extensions import Self

from gracy.models import DEFAULT_CONFIG, BaseEndpoint, GracefulMethod, GracyConfig

logger = logging.getLogger(__name__)


Endpoint = TypeVar("Endpoint", bound=BaseEndpoint | str)  # , default=str)


def _wrap_framed_request(framed: GracefulMethod):
    def _wrapper(*args: Any, **kwargs: Any):
        return framed.function(*args, **kwargs)

    return _wrapper


def _wrap_regular_request(func: Callable[..., Any]):
    def _wrapper(*args: Any, **kwargs: Any):
        return func(*args, **kwargs)

    return _wrapper


FORBIDDEN_METHODS = {"get", "post", "put", "patch", "head"}


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
                setattr(instance, attrname, _wrap_framed_request(attr))

            elif callable(attr) and not attrname.startswith("_") and attrname not in FORBIDDEN_METHODS:
                setattr(instance, attrname, _wrap_regular_request(attr))

        return instance


class Gracy(typing.Generic[Endpoint], metaclass=GracyMeta):  # type: ignore
    """Helper class that provides a standard way to create an Requester using
    inheritance.
    """

    def __init__(self, base_url: str = "", config: GracyConfig | None = None) -> None:
        self._base_config = config or DEFAULT_CONFIG

        event_hooks = {}  # {"request": self._before_request, "response": self._on_response}

        self._client = httpx.Client(base_url=base_url, event_hooks=event_hooks)

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


def graceful(config: GracyConfig):
    def _inner_frame(wrapped_function: AnyRequesterMethod) -> AnyRequesterMethod:
        framed = GracefulMethod(config, wrapped_function)
        return cast(AnyRequesterMethod, framed)

    return _inner_frame
