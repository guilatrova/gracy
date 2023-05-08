import typing as t
from http import HTTPStatus

import httpx


class Unset:
    """
    The default "unset" state indicates that whatever default is set on the
    client should be used. This is different to setting `None`, which
    explicitly disables the parameter, possibly overriding a client default.
    """

    def __bool__(self):
        return False


PARSER_KEY = HTTPStatus | t.Literal["default"]
PARSER_VALUE = t.Union[t.Type[Exception], t.Callable[[httpx.Response], t.Any], None]
PARSER_TYPE = dict[PARSER_KEY, PARSER_VALUE] | Unset | None

UNSET_VALUE: t.Final = Unset()
