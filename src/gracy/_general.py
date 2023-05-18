from __future__ import annotations

import typing as t

VALID_BUILD_REQUEST_KEYS = {
    "content",
    "data",
    "files",
    "json",
    "params",
    "headers",
    "cookies",
    "timeout",
    "extensions",
}
"""
There're some kwargs that are handled by httpx request, but only a few are properly handled by https build_request.
Defined in httpx._client:322
"""


def extract_request_kwargs(kwargs: dict[str, t.Any]) -> dict[str, t.Any]:
    return {k: v for k, v in kwargs.items() if k in VALID_BUILD_REQUEST_KEYS}
