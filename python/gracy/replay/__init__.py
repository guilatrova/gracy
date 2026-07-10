"""Replay v2 — record/replay HTTP traffic through pickle-free cassette storages.

Design notes (pragmatic, documented):

* ``Scrub`` runs BEFORE both the match-hash computation AND before recording,
  so hashes stay consistent between record and replay runs regardless of the
  secret values present at either time.
* Storage seam: the :class:`gracy._protocols.ReplayStorage` protocol is
  spec-shaped (``find(spec, discard_before)`` / ``record(spec, response)``) so
  custom storages can match however they like. The storages that ship with
  gracy additionally expose a keyed API — ``find_by_key(key, discard_before)``
  and ``record_key(key, spec, response)`` — which :class:`Replay` prefers
  (hasattr check) so the hash is computed exactly once, here, with the user's
  ``match_on``/``scrub`` settings. Custom protocol-only storages receive the
  already-scrubbed spec.
"""

from __future__ import annotations

import hashlib
import json
import typing as t
from dataclasses import dataclass, replace

from gracy._types import RequestSpec, Response
from gracy.exceptions import GracyConfigError

if t.TYPE_CHECKING:
    from datetime import datetime

    from gracy._protocols import ReplayStorage

ReplayMode = t.Literal["record", "replay", "smart-replay", "off"]

DEFAULT_MATCH_ON: t.Final[tuple[str, ...]] = ("method", "url", "body")

_HASH_DIMENSIONS: t.Final[tuple[str, ...]] = ("method", "url", "body", "headers")

_REDACTED: t.Final = "***"


# --------------------------------------------------------------------------- scrub


@dataclass(frozen=True)
class Scrub:
    """Redacts secrets from a RequestSpec before hashing and before recording.

    ``headers``: header NAMES (case-insensitive) whose values become "***".
    ``json_fields``: top-level JSON object fields in the request body that
    become "***". When ``json_fields`` is set and the body parses as a JSON
    dict, the body is re-encoded canonically (sorted keys, compact separators)
    so equivalent bodies hash identically.
    """

    headers: tuple[str, ...] = ("authorization", "cookie", "x-api-key")
    json_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", tuple(self.headers))
        object.__setattr__(self, "json_fields", tuple(self.json_fields))

    def apply(self, spec: RequestSpec) -> RequestSpec:
        out = spec

        if self.headers and out.headers:
            targets = {h.lower() for h in self.headers}
            redact = {k: _REDACTED for k, v in out.headers if k.lower() in targets and v != _REDACTED}
            if redact:
                out = out.with_headers(redact)

        if self.json_fields and out.content:
            try:
                data = json.loads(out.content)
            except (ValueError, UnicodeDecodeError):
                data = None
            if isinstance(data, dict):
                for field_name in self.json_fields:
                    if field_name in data:
                        data[field_name] = _REDACTED
                encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
                out = replace(out, content=encoded)

        return out


# --------------------------------------------------------------------------- match hash


def match_hash(spec: RequestSpec, match_on: tuple[str, ...], scrub: Scrub | None) -> str:
    """sha256 hex over the selected request dimensions (scrubbed first).

    Dimensions are always hashed in the canonical order method/url/body/headers
    regardless of the order given in ``match_on``, so equivalent selections
    produce identical hashes.
    """
    unknown = set(match_on) - set(_HASH_DIMENSIONS)
    if unknown:
        raise GracyConfigError(
            f"Unknown match_on dimension(s) {sorted(unknown)!r}; valid: {list(_HASH_DIMENSIONS)!r}"
        )

    if scrub is not None:
        spec = scrub.apply(spec)

    parts: list[bytes] = []
    for dim in _HASH_DIMENSIONS:
        if dim not in match_on:
            continue
        if dim == "method":
            parts.append(spec.method.upper().encode("utf-8"))
        elif dim == "url":
            parts.append(spec.url.encode("utf-8"))
        elif dim == "body":
            parts.append(spec.content or b"")
        else:  # headers
            lines = sorted(f"{k.lower()}={v}" for k, v in spec.headers)
            parts.append("\n".join(lines).encode("utf-8"))

    return hashlib.sha256(b"\x00".join(parts)).hexdigest()


# --------------------------------------------------------------------------- replay


class Replay:
    """User-facing replay settings + the record/replay decision surface.

    The pipeline calls :meth:`find` before admission (replay hits never spend
    throttle tokens) and :meth:`record` after a live response when recording.
    """

    def __init__(
        self,
        mode: ReplayMode,
        storage: ReplayStorage,
        match_on: tuple[str, ...] = DEFAULT_MATCH_ON,
        scrub: Scrub | None = Scrub(),
        discard_older_than: datetime | None = None,
        discard_bad_responses: bool = False,
        disable_throttling: bool = False,
        display_report: bool = True,
    ) -> None:
        self.mode: ReplayMode = mode
        self.storage = storage
        self.match_on = tuple(match_on)
        self.scrub = scrub
        self.discard_older_than = discard_older_than
        self.discard_bad_responses = discard_bad_responses
        self.disable_throttling = disable_throttling
        self.display_report = display_report
        self.records_made: int = 0
        self.replays_made: int = 0

        # Validate match_on eagerly (fail at construction, not first request).
        if mode != "off":
            match_hash(RequestSpec(method="GET", url="", uurl=""), self.match_on, None)

    async def prepare(self) -> None:
        await self.storage.prepare()

    async def find(self, spec: RequestSpec) -> Response | None:
        """Return the recorded response for this spec, or None."""
        if self.mode == "off":
            return None

        discard_before = self.discard_older_than.timestamp() if self.discard_older_than is not None else None

        find_by_key = getattr(self.storage, "find_by_key", None)
        if find_by_key is not None:
            key = match_hash(spec, self.match_on, self.scrub)
            response = await find_by_key(key, discard_before)
        else:
            scrubbed = self.scrub.apply(spec) if self.scrub is not None else spec
            response = await self.storage.find(scrubbed, discard_before)

        if response is not None and self.discard_bad_responses and not response.is_success:
            return None
        if response is not None:
            response.is_replay = True
            self.replays_made += 1
        return response

    async def record(self, spec: RequestSpec, response: Response) -> None:
        """Persist (scrubbed spec, response) keyed by the match hash."""
        scrubbed = self.scrub.apply(spec) if self.scrub is not None else spec

        record_key = getattr(self.storage, "record_key", None)
        if record_key is not None:
            key = match_hash(spec, self.match_on, self.scrub)
            await record_key(key, scrubbed, response)
        else:
            await self.storage.record(scrubbed, response)
        self.records_made += 1

    async def flush(self) -> None:
        await self.storage.flush()


from gracy.replay.mongo import MongoReplayStorage  # noqa: E402
from gracy.replay.storages import SCHEMA_VERSION, MemoryStorage, SqliteStorage  # noqa: E402

__all__ = [
    "DEFAULT_MATCH_ON",
    "SCHEMA_VERSION",
    "MemoryStorage",
    "MongoReplayStorage",
    "Replay",
    "ReplayMode",
    "Scrub",
    "SqliteStorage",
    "match_hash",
]
