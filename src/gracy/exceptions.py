from http import HTTPStatus
from typing import Any, Iterable

import httpx

from ._models import GracyRequestContext


class GracyException(Exception):
    pass


class GracyParseFailed(Exception):
    def __init__(self, response: httpx.Response) -> None:
        msg = (
            f"Unable to parse result from [{response.request.method}] {response.url} ({response.status_code}). "
            f"Response content is: {response.text}"
        )

        self.url = response.request.url
        self.response = response

        super().__init__(msg)


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

    def __init__(self, request_context: GracyRequestContext, response: httpx.Response) -> None:
        self._request_context = request_context
        self._response = response
        super().__init__(self._format_message(request_context, response))

    def _build_default_args(self) -> dict[str, Any]:
        request_context = self._request_context

        return dict(
            # Context
            ENDPOINT=request_context.endpoint,
            UURL=request_context.unformatted_url,
            UENDPOINT=request_context.unformatted_endpoint,
            # Response
            URL=self.response.request.url,
            METHOD=self.response.request.method,
            STATUS=self.response.status_code,
            ELAPSED=self.response.elapsed,
        )

    def _format_message(self, request_context: GracyRequestContext, response: httpx.Response) -> str:
        format_args = self._build_default_args()
        return self.BASE_MESSAGE.format(**format_args)

    @property
    def url(self):
        return self._request_context.url

    @property
    def endpoint(self):
        return self._request_context.url

    @property
    def response(self):
        return self._response


class GracyReplayRequestNotFound(GracyException):
    def __init__(self, request: httpx.Request) -> None:
        self.request = request

        msg = f"Gracy was unable to replay {request.method} {request.url} - did you forget to record it?"
        super().__init__(msg)
