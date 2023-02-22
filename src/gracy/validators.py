import typing as t
from abc import ABC, abstractmethod
from http import HTTPStatus

import httpx

from gracy.exceptions import NonOkResponse, UnexpectedResponse


class GracefulValidator(ABC):
    @abstractmethod
    def check(self, response: httpx.Response) -> bool | Exception:
        pass


class DefaultValidator(GracefulValidator):
    def check(self, response: httpx.Response) -> bool | Exception:
        return response.is_success


class StrictStatusValidator(GracefulValidator):
    def __init__(self, status_code: t.Union[HTTPStatus, t.Iterable[HTTPStatus]]) -> None:
        if isinstance(status_code, t.Iterable):
            self._status_codes = status_code
        else:
            self._status_codes = {status_code}

    def check(self, response: httpx.Response) -> bool | Exception:
        if HTTPStatus(response.status_code) in self._status_codes:
            return True

        raise UnexpectedResponse(str(response.url), response, self._status_codes)


class AllowedStatusValidator(GracefulValidator):
    def __init__(self, status_code: t.Union[HTTPStatus, t.Iterable[HTTPStatus]]) -> None:
        if isinstance(status_code, t.Iterable):
            self._status_codes = status_code
        else:
            self._status_codes = {status_code}

    def check(self, response: httpx.Response) -> bool | Exception:
        if response.is_success:
            return True

        if HTTPStatus(response.status_code) in self._status_codes:
            return True

        raise NonOkResponse(str(response.url), response)
