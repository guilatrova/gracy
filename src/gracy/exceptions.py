from __future__ import annotations

import typing as t
from abc import ABC, abstractmethod

import httpx

from ._models import GracyRequestContext

REDUCE_PICKABLE_RETURN = t.Tuple[t.Type[Exception], t.Tuple[t.Any, ...]]


class GracyException(Exception, ABC):
    @abstractmethod
    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        """
        `__reduce__` is required to avoid Gracy from breaking in different
        environments that pickles the results (e.g. inside ThreadPools).

        More context: https://stackoverflow.com/a/36342588/2811539
        """
        pass


class GracyRequestFailed(GracyException):
    """
    Sometimes the httpx's request fails for whatever reason (TCP, SSL, etc errors), this
    is a wrapper exception so the client can be easily "retried" for any failed requests.

    NOTE: Consider that failed requests means NO RESPONSE because the request never completed

    Maybe this would be an `ExceptionGroup` if Gracy ever deprecates Python < 3.11
    """

    def __init__(self, context: GracyRequestContext, original_exc: Exception) -> None:
        self.original_exc = original_exc
        self.request_context = context

        super().__init__(
            f"The request for [{context.method}] {context.url} never got a response due to {str(original_exc)} "
        )

        # Inspired by https://stackoverflow.com/a/54716092/2811539
        # We include the original exception as part of the stack trace by doing that.
        self.__cause__ = original_exc
        self.__context__ = original_exc

    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        return (GracyRequestFailed, (self.request_context, self.original_exc))


class GracyParseFailed(GracyException):
    def __init__(self, response: httpx.Response) -> None:
        msg = (
            f"Unable to parse result from [{response.request.method}] {response.url} ({response.status_code}). "
            f"Response content is: {response.text}"
        )

        self.url = response.request.url
        self.response = response

        super().__init__(msg)

    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        return (GracyParseFailed, (self.response,))


class BadResponse(GracyException):
    def __init__(
        self,
        message: str | None,
        url: str,
        response: httpx.Response,
        expected: str | int | t.Iterable[int],
    ) -> None:
        self.url = url
        self.response = response

        self._args = (
            message,
            url,
            response,
            expected,
        )

        if isinstance(expected, str):
            expectedstr = expected
        elif isinstance(expected, int):
            expectedstr = str(expected)
        else:
            expectedstr = ", ".join([str(s) for s in expected])

        curmsg = message or f"{url} raised {response.status_code}, but it was expecting {expectedstr}"

        super().__init__(curmsg)

    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        return (BadResponse, self._args)


class UnexpectedResponse(BadResponse):
    def __init__(self, url: str, response: httpx.Response, expected: str | int | t.Iterable[int]) -> None:
        super().__init__(None, url, response, expected)

        self.url = url
        self.response = response
        self.expected = expected

    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        return (UnexpectedResponse, (self.url, self.response, self.expected))


class NonOkResponse(BadResponse):
    def __init__(self, url: str, response: httpx.Response) -> None:
        super().__init__(None, url, response, "any successful status code")

        self.arg1 = url
        self.arg2 = response

    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        return (NonOkResponse, (self.arg1, self.arg2))


class GracyUserDefinedException(GracyException):
    BASE_MESSAGE: str = "[{METHOD}] {URL} returned {}"

    def __init__(self, request_context: GracyRequestContext, response: httpx.Response) -> None:
        self._request_context = request_context
        self._response = response
        super().__init__(self._format_message(request_context, response))

    def _build_default_args(self) -> dict[str, t.Any]:
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
        return self._request_context.endpoint

    @property
    def response(self):
        return self._response

    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        return (GracyUserDefinedException, (self._request_context, self._response))


class GracyReplayRequestNotFound(GracyException):
    def __init__(self, request: httpx.Request) -> None:
        self.request = request

        msg = f"Gracy was unable to replay {request.method} {request.url} - did you forget to record it?"
        super().__init__(msg)

    def __reduce__(self) -> REDUCE_PICKABLE_RETURN:
        return (GracyReplayRequestNotFound, (self.request,))
