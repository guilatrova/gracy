from __future__ import annotations

import sys
import typing as t
from http import HTTPStatus

import httpx

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec


class Unset:
    """
    The default "unset" state indicates that whatever default is set on the
    client should be used. This is different to setting `None`, which
    explicitly disables the parameter, possibly overriding a client default.
    """

    def __bool__(self):
        return False


PARSER_KEY = t.Union[HTTPStatus, int, t.Literal["default"]]
PARSER_VALUE = t.Union[t.Type[Exception], t.Callable[[httpx.Response], t.Any], None]
PARSER_TYPE = t.Union[t.Dict[PARSER_KEY, PARSER_VALUE], Unset, None]

UNSET_VALUE: t.Final = Unset()


P = ParamSpec("P")
T = t.TypeVar("T")


def parsed_response(return_type: t.Type[T]):  # type: ignore
    def _decorated(func: t.Callable[P, t.Any]) -> t.Callable[P, t.Coroutine[t.Any, t.Any, T]]:
        async def _gracy_method(*args: P.args, **kwargs: P.kwargs) -> T:
            return await func(*args, **kwargs)

        return _gracy_method

    return _decorated


def generated_parsed_response(return_type: t.Type[T]):  # type: ignore
    def _decorated(func: t.Callable[P, t.AsyncGenerator[t.Any, t.Any]]) -> t.Callable[P, t.AsyncGenerator[t.Any, T]]:
        async def _gracy_method(*args: P.args, **kwargs: P.kwargs) -> t.AsyncGenerator[t.Any, T]:
            async for i in func(*args, **kwargs):
                yield i

        return _gracy_method

    return _decorated
