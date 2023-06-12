from __future__ import annotations
from contextlib import asynccontextmanager

import hashlib
import logging
import pickle
import typing as t
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx

from gracy.exceptions import GracyReplayRequestNotFound

from ._base import GracyReplayStorage

logger = logging.getLogger(__name__)

FORBID_CHARS = [
    "&",
    "$",
    "@",
    "=",
    ";",
    ":",
    ",",
    "+",
    " ",  # Space
    "?",
    "\\",
    "^",
    "{",
    "}",
    "%",
    "`",
    "[",
    "]",
    ">",
    "<",
    "'",
    "~",
    "#",
    "|",
]
"""
https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html
Check
    * 'Characters that might require special handling'
    * 'Characters to avoid'
"""


@dataclass
class AWSCredentials:
    region_name: str | None = None
    aws_secret_access_key: str | None = None
    aws_access_key_id: str | None = None


class S3ReplayStorage(GracyReplayStorage):
    def __init__(self, bucket_name: str, key_prefix: str, creds: AWSCredentials | None = None) -> None:
        self.bucket_name = bucket_name
        self.key_prefix = key_prefix
        self._creds = creds

    def prepare(self) -> None:
        from aiobotocore.session import get_session

        self._session = get_session()

    def _safe_format_request_url(self, request: httpx.Request) -> str:
        safe_url = str(request.url)
        for ch in FORBID_CHARS:
            if ch in safe_url:
                safe_url = safe_url.replace(ch, "_")

        return safe_url

    def _make_key(self, request: httpx.Request) -> str:
        url = self._safe_format_request_url(request)
        req_body_hash = hashlib.sha1(request.content).hexdigest()

        return f"{request.method}/{url}/{req_body_hash}.gracy"

    @asynccontextmanager
    async def _create_s3_client(self):
        creds = asdict(self._creds) if self._creds else {}
        async with self._session.create_client("s3", **creds) as client:
            yield client


    async def record(self, response: httpx.Response) -> None:
        request_key = self._make_key(response.request)
        response_serialized = pickle.dumps(response)

        async with self._create_s3_client() as client:
            await client.put_object(Bucket=self.bucket_name, Key=request_key, Body=response_serialized)

    async def load(self, request: httpx.Request, discard_before: datetime | None) -> httpx.Response:
        import botocore.exceptions

        request_key = self._make_key(request)

        async with self._create_s3_client() as client:
            try:
                response = await client.get_object(Bucket=self.bucket_name, Key=request_key)
                if discard_before and response['LastModified'] < discard_before:

            except:


        creds = asdict(self._creds) if self._creds else {}
        async with self._session.create_client("s3", **creds) as client:
