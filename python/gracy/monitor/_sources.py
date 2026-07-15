"""Snapshot reading and aggregation for the Gracy monitor viewer.

Parses the schema-1 JSON snapshot files published by running Gracy clients
and merges them into cross-source aggregates (tiles, endpoint rows) plus the
cross-frame message log used by the dashboard's messages panel.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import typing as t
from dataclasses import dataclass

# ------------------------------------------------------------------ constants

SCHEMA_VERSION: t.Final = 1
LIVE_MAX_AGE_S: t.Final = 3.0  # newer than this (and not closed) -> LIVE
STALE_MAX_AGE_S: t.Final = 30.0  # older than this -> ignored + cleaned up
CLOSED_TILE_GRACE_S: t.Final = 5.0  # closed sources leave the tiles after this
MAX_MESSAGES: t.Final = 1000  # viewer-side history (outlives the clients' own buffers)


def default_spool_dir() -> str:
    """Where publishers write snapshots. Kept in sync with gracy.monitor (publisher)."""
    return os.environ.get("GRACY_MONITOR_DIR") or os.path.join(
        tempfile.gettempdir(), "gracy-monitor"
    )


# ------------------------------------------------------------------ sources


@dataclass(slots=True)
class Source:
    """One snapshot file, parsed and classified."""

    path: str
    data: dict[str, t.Any]
    age: float  # now - snapshot ts

    @property
    def closed(self) -> bool:
        return bool(self.data.get("closed"))

    @property
    def state(self) -> str:
        """"live" | "stale" | "closed"."""
        if self.closed:
            return "closed"
        return "live" if self.age < LIVE_MAX_AGE_S else "stale"

    @property
    def counts_in_tiles(self) -> bool:
        if self.closed:
            return self.age <= CLOSED_TILE_GRACE_S
        return True  # live and stale both keep their last numbers on the board

    @property
    def client(self) -> str:
        return str(self.data.get("client", "?"))

    @property
    def queue(self) -> dict[str, t.Any]:
        return self.data.get("queue") or {}

    @property
    def totals(self) -> dict[str, t.Any]:
        return self.data.get("totals") or {}

    @property
    def messages(self) -> list[dict[str, t.Any]]:
        raw = self.data.get("messages")
        return raw if isinstance(raw, list) else []


def read_sources(directory: str, now: float | None = None) -> list[Source]:
    """Parse every snapshot in `directory`, skipping partial/corrupt files and
    cleaning up entries older than STALE_MAX_AGE_S."""
    now = time.time() if now is None else now
    sources: list[Source] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return sources

    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue  # partial write / corrupt file: try again next frame
        if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
            continue
        try:
            age = now - float(data["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if age > STALE_MAX_AGE_S:  # dead publisher: clean up its spool file
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        sources.append(Source(path=path, data=data, age=age))
    return sources


# ------------------------------------------------------------------ aggregation


@dataclass(slots=True)
class Aggregate:
    """Cross-source totals for the current frame (tile/header numbers)."""

    in_flight: int = 0
    pending: int = 0
    throttle_waits: int = 0
    paused_max: float = 0.0
    aborts: int = 0
    retries: int = 0
    replays: int = 0
    requests: int = 0
    req_per_sec: float = 0.0


def aggregate(sources: list[Source]) -> Aggregate:
    agg = Aggregate()
    for src in sources:
        if not src.counts_in_tiles:
            continue
        queue, totals = src.queue, src.totals
        agg.in_flight += int(queue.get("in_flight", 0))
        agg.pending += int(queue.get("pending", 0))
        agg.throttle_waits += sum(int(v) for v in (queue.get("throttle_hits") or {}).values())
        paused = queue.get("paused") or {}
        if paused:
            agg.paused_max = max(agg.paused_max, max(float(v) for v in paused.values()))
        agg.aborts += int(totals.get("aborts", 0))
        agg.retries += int(totals.get("retries", 0))
        agg.replays += int(totals.get("replays", 0))
        agg.requests += int(totals.get("requests", 0))
        agg.req_per_sec += float(totals.get("req_per_sec", 0.0))
    return agg


def aggregate_rows(sources: list[Source]) -> list[dict[str, t.Any]]:
    """Merge per-endpoint rows across sources by uurl (sum counts, weight rates)."""
    merged: dict[str, dict[str, t.Any]] = {}
    for src in sources:
        if not src.counts_in_tiles:
            continue
        for row in src.data.get("rows") or ():
            uurl = str(row.get("uurl", "?"))
            total = int(row.get("total", 0))
            agg = merged.get(uurl)
            if agg is None:
                agg = merged[uurl] = {
                    "uurl": uurl,
                    "total": 0,
                    "retries": 0,
                    "throttles": 0,
                    "aborts": 0,
                    "replays": 0,
                    "req_rate_per_sec": 0.0,
                    "_ok_weight": 0.0,  # success_rate * total
                    "_p95_weight": 0.0,  # p95 * total
                }
            agg["total"] += total
            agg["retries"] += int(row.get("retries", 0))
            agg["throttles"] += int(row.get("throttles", 0))
            agg["aborts"] += int(row.get("aborts", 0))
            agg["replays"] += int(row.get("replays", 0))
            agg["req_rate_per_sec"] += float(row.get("req_rate_per_sec", 0.0))
            agg["_ok_weight"] += float(row.get("success_rate", 0.0)) * total
            agg["_p95_weight"] += float(row.get("p95_latency", 0.0)) * total

    rows = list(merged.values())
    for row in rows:
        total = row["total"] or 1
        row["success_rate"] = row.pop("_ok_weight") / total
        row["p95_latency"] = row.pop("_p95_weight") / total
    rows.sort(key=lambda r: (-r["total"], r["uurl"]))
    return rows


# ------------------------------------------------------------------ messages


def _safe_float(value: t.Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class MessageLog:
    """Cross-frame accumulator for messages emitted via ``client.message()``.

    Every snapshot re-sends the client's current (rotating) message buffer, so
    entries are deduped by (spool file, per-client message id) and retained
    here even after they rotate out of the client's own buffer: scrolling back
    never loses history, up to MAX_MESSAGES.

    Keyed messages (``client.message(..., key=...)``) are live gauges instead:
    deduped by (spool file, key), a changed id replaces the old text and moves
    the single entry to the tail - updates never pile up in the history.
    """

    def __init__(self, maxlen: int = MAX_MESSAGES) -> None:
        self._maxlen = maxlen
        self._entries: dict[tuple[str, str], dict[str, t.Any]] = {}
        self._clients: set[str] = set()

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def multi_client(self) -> bool:
        return len(self._clients) > 1

    def ingest(self, sources: list[Source]) -> None:
        fresh: list[tuple[tuple[str, str], dict[str, t.Any]]] = []
        for src in sources:
            for msg in src.messages:
                if not isinstance(msg, dict):
                    continue
                try:
                    msg_id = int(msg["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                gauge = msg.get("key")
                key = (src.path, f"k:{gauge}" if gauge else f"i:{msg_id}")
                existing = self._entries.get(key)
                if existing is not None:
                    if existing["id"] == msg_id:
                        continue  # plain re-send of a known entry
                    del self._entries[key]  # keyed gauge update: re-append at the tail
                fresh.append(
                    (
                        key,
                        {
                            "id": msg_id,
                            "ts": _safe_float(msg.get("ts")),
                            "level": str(msg.get("level", "info")),
                            "text": str(msg.get("text", "")),
                            "client": src.client,
                        },
                    )
                )
        fresh.sort(key=lambda item: item[1]["ts"])  # interleave multi-source bursts by time
        for key, entry in fresh:
            self._entries[key] = entry
            self._clients.add(entry["client"])
        while len(self._entries) > self._maxlen:
            del self._entries[next(iter(self._entries))]

    def window(self, offset: int, rows: int) -> tuple[list[dict[str, t.Any]], int, int]:
        """The `rows` entries ending `offset` back from the tail.

        Returns (entries, start_index, total); offset is clamped so the window
        never runs past either end.
        """
        entries = list(self._entries.values())
        total = len(entries)
        offset = max(0, min(offset, total - rows)) if total > rows else 0
        end = total - offset
        return entries[max(0, end - rows) : end], max(0, end - rows), total
