"""Gracy exception hierarchy. All exceptions are picklable and preserve subclass identity."""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from gracy._types import RequestContext, Response


class GracyException(Exception):
    """Base for everything gracy raises."""


class GracyConfigError(GracyException):
    """Invalid configuration detected at build()/compile time."""


class GracyWrongLoopError(GracyException):
    """Client used from a different event loop than the one it was built on."""


class GracyForkedClientError(GracyException):
    """A started client was used in a forked child process. Build clients per-worker (post-fork)."""


class GracyClientClosedError(GracyException):
    """Request issued on a client that was never started or is already closed."""


class GracyQueueFull(GracyException):
    """Queue admission rejected the request (Queue(on_full='raise'))."""


class GracyRequestFailed(GracyException):
    """Transport-level failure (connect error, timeout, ...). Wraps the original exception.

    Hooks and `retry.on` matching ALWAYS see this wrapper - consistently, on
    every attempt (v1 mixed raw and wrapped exceptions between attempts).
    """

    def __init__(self, url: str, original_exc: BaseException) -> None:
        self.url = url
        self.original_exc = original_exc
        super().__init__(f"Request to {url} failed: {type(original_exc).__name__}: {original_exc}")

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (type(self), (self.url, self.original_exc))


class GracyResponseError(GracyException):
    """Base for validation failures that carry a response."""

    def __init__(self, message: str, response: Response | None = None) -> None:
        self.response = response
        super().__init__(message)

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (type(self), (self.args[0], self.response))


class NonOkResponse(GracyResponseError):
    """Default validator: response was not successful (2xx)."""


class UnexpectedResponse(GracyResponseError):
    """Strict status policy: response status not in the allowed set."""

    def __init__(self, message: str, response: Response | None = None, expected: tuple[int, ...] = ()) -> None:
        self.expected = expected
        GracyException.__init__(self, message)
        self.response = response

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (type(self), (self.args[0], self.response, self.expected))


class GracyParseFailed(GracyResponseError):
    """A parser/decoder callable raised while transforming the response."""


class GracyReplayRequestNotFound(GracyException):
    """Replay mode: no recording matches this request."""


class GracyUserDefinedException(GracyException):
    """Subclass this for `raises(MyError)` parser actions and rich messages.

    BASE_MESSAGE placeholders: {URL} {UURL} {METHOD} {STATUS} {ELAPSED} plus
    any UPPERCASE endpoint arg (e.g. {NAME} for "/pokemon/{NAME}").
    """

    BASE_MESSAGE: str = "{METHOD} {URL} returned {STATUS}"

    def __init__(self, context: RequestContext | None = None, response: Response | None = None) -> None:
        self.context = context
        self.response = response
        super().__init__(self._format_message(context, response))

    def _format_message(self, context: RequestContext | None, response: Response | None) -> str:
        from gracy.logging_events import build_placeholders, safe_format

        values = build_placeholders(context=context, response=response)
        return safe_format(self.BASE_MESSAGE, values)

    def __reduce__(self):  # type: ignore[no-untyped-def]
        # Preserves the SUBCLASS (v1 reconstructed the base class and lost identity).
        return (type(self), (self.context, self.response))
