from http import HTTPStatus
from typing import Any, Callable, Final, Literal, Type, Union

import httpx


class Unset:
    """
    The default "unset" state indicates that whatever default is set on the
    client should be used. This is different to setting `None`, which
    explicitly disables the parameter, possibly overriding a client default.
    """

    def __bool__(self):
        return False


PARSER_KEY = HTTPStatus | Literal["default"]
PARSER_VALUE = Union[Type[Exception], Callable[[httpx.Response], Any], None]
PARSER_TYPE = dict[PARSER_KEY, PARSER_VALUE] | Unset | None

UNSET_VALUE: Final = Unset()
