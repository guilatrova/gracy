from http import HTTPStatus
from typing import Any, Iterable

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


class GracyUserDefinedException(GracyException):
    BASE_MESSAGE: str = "[{METHOD}] {URL} returned {}"

    def __init__(self, base_endpoint: str, endpoint_args: dict[str, str] | None, response: httpx.Response) -> None:
        self._base_endpoint = base_endpoint
        self._endpoint_args = endpoint_args or {}
        self._response = response
        super().__init__(self._format_message(base_endpoint, self._endpoint_args, response))

    def _build_default_args(self) -> dict[str, Any]:
        return dict(
            STATUS=self.response.status_code,
            METHOD=self.response.request.method,
            URL=self.response.request.url,
            ELAPSED=self.response.elapsed,
        )

    def _format_message(self, base_endpoint: str, endpoint_args: dict[str, str], response: httpx.Response) -> str:
        format_args = self._build_default_args()
        return self.BASE_MESSAGE.format(**format_args)

    @property
    def base_endpoint(self):
        return self._base_endpoint

    @property
    def endpoint_args(self):
        return self._endpoint_args

    @property
    def response(self):
        return self._response
