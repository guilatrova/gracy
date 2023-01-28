from http import HTTPStatus
from typing import Iterable

import httpx


class GracyException(Exception):
    pass


class BadResponse(GracyException):
    def __init__(
        self,
        message: str | None,
        url: str,
        response: httpx.Response,
        expected: str | HTTPStatus | Iterable[HTTPStatus],
    ) -> None:
        self.url = url
        self.response = response

        if isinstance(expected, str):
            expectedstr = expected
        elif isinstance(expected, HTTPStatus):
            expectedstr = str(expected.value)
        else:
            expectedstr = ", ".join([str(s.value) for s in expected])

        curmsg = message or f"{url} raised {response.status_code}, but it was expecting {expectedstr}"

        super().__init__(curmsg)


class UnexpectedResponse(BadResponse):
    def __init__(self, url: str, response: httpx.Response, expected: str | HTTPStatus | Iterable[HTTPStatus]) -> None:
        super().__init__(None, url, response, expected)


class NonOkResponse(BadResponse):
    def __init__(self, url: str, response: httpx.Response) -> None:
        super().__init__(None, url, response, "any successful status code")
