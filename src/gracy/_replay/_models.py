from dataclasses import dataclass
from datetime import datetime


@dataclass
class GracyRecording:
    url: str
    method: str

    request_body: bytes | None
    response: bytes

    updated_at: datetime
