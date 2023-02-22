import typing as t
from http import HTTPStatus

import httpx

from ._models import GracefulValidator
from .exceptions import NonOkResponse, UnexpectedResponse


class DefaultValidator(GracefulValidator):
    def check(self, response: httpx.Response) -> None:
        if response.is_success:
            return None

        raise NonOkResponse(str(response.url), response)


class StrictStatusValidator(GracefulValidator):
    def __init__(self, status_code: t.Union[HTTPStatus, t.Iterable[HTTPStatus]]) -> None:
        if isinstance(status_code, t.Iterable):
            self._status_codes = status_code
        else:
            self._status_codes = {status_code}

    def check(self, response: httpx.Response) -> None:
        if HTTPStatus(response.status_code) in self._status_codes:
            return None

        raise UnexpectedResponse(str(response.url), response, self._status_codes)


class AllowedStatusValidator(GracefulValidator):
    def __init__(self, status_code: t.Union[HTTPStatus, t.Iterable[HTTPStatus]]) -> None:
        if isinstance(status_code, t.Iterable):
            self._status_codes = status_code
        else:
            self._status_codes = {status_code}

    def check(self, response: httpx.Response) -> None:
        if response.is_success:
            return None

        if HTTPStatus(response.status_code) in self._status_codes:
            return None

        raise NonOkResponse(str(response.url), response)
