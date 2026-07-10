"""Replay v2 behavior: record / replay / smart-replay through real storages.

Covers: SQLite schema-v2 persistence (inspected with raw sqlite3, zero pickle),
replay hits bypassing the transport entirely, match_on dimensions, the
scrub-before-hash-and-record invariant, discard_older_than (v1 str-vs-datetime
TypeError regression) and discard_bad_responses freshness rules, strict-miss
errors, MemoryStorage roundtrips, counters, and flush-on-aclose.

Everything runs against gracy.testing.MockTransport — no real server needed.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from gracy import Gracy, GracyReplayRequestNotFound, RequestSpec, Response, get
from gracy.replay import SCHEMA_VERSION, MemoryStorage, Replay, SqliteStorage
from gracy.testing import MockTransport

BASE = "https://replay.test"

MATCH_WITH_HEADERS = ("method", "url", "body", "headers")

# Pickle stream prefixes (protocol 2..5). v1 cassettes were pickle.dumps() blobs;
# schema v2 must never contain them. 3-byte sequences keep false positives from
# random-looking WAL salts/checksums astronomically unlikely.
PICKLE_MAGICS = (b"\x80\x02c", b"\x80\x03c", b"\x80\x04\x95", b"\x80\x05\x95")


class Api(Gracy):
    base_url = BASE

    @get("/thing/{name}")
    async def get_thing(self, name): ...  # no return annotation -> raw gracy Response

    @get("/broken", status_policy=None)  # policy off: a recorded 500 must not raise
    async def get_broken(self): ...


def ok_transport() -> MockTransport:
    return MockTransport({f"{BASE}/thing/*": {"ok": True}})


def sqlite_file_bytes(db_path: os.PathLike[str]) -> bytes:
    """Raw bytes of the cassette db + its WAL/SHM siblings (checkpointed first)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    raw = b""
    for path in (str(db_path), f"{db_path}-wal", f"{db_path}-shm"):
        if os.path.exists(path):
            with open(path, "rb") as fh:
                raw += fh.read()
    return raw


def all_rows(db_path: os.PathLike[str], columns: str = "*") -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT {columns} FROM gracy_recordings_v2").fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------- record -> sqlite


async def test_record_mode_persists_schema_v2_row(tmp_path):
    db = tmp_path / "cassette.db"
    transport = ok_transport()
    replay = Replay(mode="record", storage=SqliteStorage(db))

    async with Api(replay=replay, transport=transport) as api:
        live = await api.get_thing("mew")

    assert live.status == 200
    assert live.is_replay is False
    assert len(transport.calls) == 1

    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "gracy_recordings_v2" in tables

    rows = all_rows(db, "method, url, status, response_body, recorded_at, schema_version")
    assert len(rows) == 1
    method, url, status, response_body, recorded_at, schema_version = rows[0]
    assert method == "GET"
    assert url == f"{BASE}/thing/mew"
    assert status == 200
    assert bytes(response_body) == live.body == b'{"ok": true}'  # BLOB == served body
    assert schema_version == SCHEMA_VERSION == 2
    assert isinstance(recorded_at, int)  # unix epoch MILLISECONDS (not str, not datetime)
    now_ms = time.time() * 1000
    assert now_ms - 5 * 60_000 <= recorded_at <= now_ms + 60_000


async def test_sqlite_cassette_has_no_pickle_magic_bytes(tmp_path):
    db = tmp_path / "cassette.db"
    async with Api(replay=Replay(mode="record", storage=SqliteStorage(db)), transport=ok_transport()) as api:
        await api.get_thing("pikachu")

    raw = sqlite_file_bytes(db)
    assert raw.startswith(b"SQLite format 3\x00")  # sanity: we read the real db
    for magic in PICKLE_MAGICS:
        assert magic not in raw


# --------------------------------------------------------------------------- replay / smart-replay


async def test_replay_mode_returns_recorded_response_without_touching_transport(tmp_path):
    db = tmp_path / "cassette.db"
    async with Api(replay=Replay(mode="record", storage=SqliteStorage(db)), transport=ok_transport()) as api:
        live = await api.get_thing("mew")

    replay_transport = MockTransport()  # anything reaching it would 404
    async with Api(replay=Replay(mode="replay", storage=SqliteStorage(db)), transport=replay_transport) as api:
        replayed = await api.get_thing("mew")

    assert replayed.status == live.status == 200
    assert replayed.body == live.body
    assert replayed.is_replay is True
    assert replay_transport.calls == []  # replay never reached the transport


async def test_smart_replay_records_first_run_then_replays_second_run(tmp_path):
    db = tmp_path / "cassette.db"

    transport1 = ok_transport()
    replay1 = Replay(mode="smart-replay", storage=SqliteStorage(db))
    async with Api(replay=replay1, transport=transport1) as api:
        first = await api.get_thing("mew")
    assert first.is_replay is False
    assert len(transport1.calls) == 1  # miss -> live call
    assert replay1.records_made == 1
    assert replay1.replays_made == 0

    transport2 = MockTransport()
    replay2 = Replay(mode="smart-replay", storage=SqliteStorage(db))
    async with Api(replay=replay2, transport=transport2) as api:
        second = await api.get_thing("mew")
    assert second.is_replay is True
    assert second.body == first.body
    assert transport2.calls == []  # hit -> transport never consulted
    assert replay2.records_made == 0
    assert replay2.replays_made == 1


# --------------------------------------------------------------------------- match_on


async def test_default_match_on_differentiates_post_bodies(tmp_path):
    db = tmp_path / "cassette.db"
    echo = MockTransport({f"POST {BASE}/things": lambda spec: (200, {"echo": spec.content.decode()})})

    async with Api(replay=Replay(mode="record", storage=SqliteStorage(db)), transport=echo) as api:
        await api.request("POST", "/things", json={"n": 1})
        await api.request("POST", "/things", json={"n": 2})

    assert len(all_rows(db, "match_hash")) == 2  # different bodies -> different recordings

    async with Api(replay=Replay(mode="replay", storage=SqliteStorage(db)), transport=MockTransport()) as api:
        one = await api.request("POST", "/things", json={"n": 1})
        two = await api.request("POST", "/things", json={"n": 2})
    assert one.json() == {"echo": '{"n": 1}'}
    assert two.json() == {"echo": '{"n": 2}'}
    assert one.is_replay is True and two.is_replay is True


async def test_default_match_on_ignores_headers(tmp_path):
    db = tmp_path / "cassette.db"
    transport = MockTransport({f"{BASE}/h": {"ok": True}})

    async with Api(replay=Replay(mode="record", storage=SqliteStorage(db)), transport=transport) as api:
        await api.request("GET", "/h", headers={"x-run": "one"})
        await api.request("GET", "/h", headers={"x-run": "two"})

    assert len(all_rows(db, "match_hash")) == 1  # same hash -> second overwrote the first

    async with Api(replay=Replay(mode="replay", storage=SqliteStorage(db)), transport=MockTransport()) as api:
        hit = await api.request("GET", "/h", headers={"x-run": "three"})  # yet another header
    assert hit.status == 200
    assert hit.is_replay is True


async def test_match_on_with_headers_differentiates(tmp_path):
    db = tmp_path / "cassette.db"
    transport = MockTransport({f"{BASE}/tenant": {"ok": True}})

    rec = Replay(mode="record", storage=SqliteStorage(db), match_on=MATCH_WITH_HEADERS)
    async with Api(replay=rec, transport=transport) as api:
        await api.request("GET", "/tenant", headers={"x-tenant": "a"})

    rep = Replay(mode="replay", storage=SqliteStorage(db), match_on=MATCH_WITH_HEADERS)
    async with Api(replay=rep, transport=MockTransport()) as api:
        with pytest.raises(GracyReplayRequestNotFound):
            await api.request("GET", "/tenant", headers={"x-tenant": "b"})
        hit = await api.request("GET", "/tenant", headers={"x-tenant": "a"})
    assert hit.status == 200
    assert hit.is_replay is True


# --------------------------------------------------------------------------- scrub


async def test_default_scrub_keeps_authorization_out_of_the_sqlite_file(tmp_path):
    secret = "Bearer sup3r-s3cret-t0ken-XYZ"
    db = tmp_path / "cassette.db"
    transport = MockTransport({f"{BASE}/private": {"ok": True}})

    async with Api(replay=Replay(mode="record", storage=SqliteStorage(db)), transport=transport) as api:
        live = await api.request("GET", "/private", headers={"authorization": secret})
    assert live.status == 200
    # The live request DID carry the secret on the wire (scrub is storage-only).
    assert dict(transport.calls[0].headers)["authorization"] == secret

    raw = sqlite_file_bytes(db)
    assert secret.encode() not in raw
    assert b"s3cret" not in raw

    (headers_json,) = all_rows(db, "request_headers_json")[0]
    assert "authorization" in headers_json.lower()
    assert "***" in headers_json  # redacted, not dropped


async def test_scrub_runs_before_hash_so_rotated_secrets_still_match(tmp_path):
    db = tmp_path / "cassette.db"
    transport = MockTransport({f"{BASE}/private": {"ok": True}})

    rec = Replay(mode="record", storage=SqliteStorage(db), match_on=MATCH_WITH_HEADERS)
    async with Api(replay=rec, transport=transport) as api:
        await api.request("GET", "/private", headers={"authorization": "Bearer token-A"})

    rep = Replay(mode="replay", storage=SqliteStorage(db), match_on=MATCH_WITH_HEADERS)
    async with Api(replay=rep, transport=MockTransport()) as api:
        hit = await api.request("GET", "/private", headers={"authorization": "Bearer token-B"})
    assert hit.status == 200
    assert hit.is_replay is True  # both tokens scrub to "***" before hashing


# --------------------------------------------------------------------------- freshness / quality discards


async def test_discard_older_than_future_cutoff_is_a_miss_on_sqlite(tmp_path):
    """Regression (v1 bug #5): SQLite freshness compare must not TypeError on str-vs-datetime."""
    db = tmp_path / "cassette.db"
    async with Api(
        replay=Replay(mode="record", storage=SqliteStorage(db)),
        transport=MockTransport({f"{BASE}/thing/*": {"v": "old"}}),
    ) as api:
        await api.get_thing("mew")

    # Cutoff 1h in the future: the just-recorded entry is "too old" -> miss -> live re-fetch.
    fresh_transport = MockTransport({f"{BASE}/thing/*": {"v": "new"}})
    strict_fresh = Replay(
        mode="smart-replay",
        storage=SqliteStorage(db),
        discard_older_than=datetime.now() + timedelta(hours=1),
    )
    async with Api(replay=strict_fresh, transport=fresh_transport) as api:
        refetched = await api.get_thing("mew")
    assert refetched.json() == {"v": "new"}
    assert refetched.is_replay is False
    assert len(fresh_transport.calls) == 1
    assert strict_fresh.replays_made == 0
    assert strict_fresh.records_made == 1  # smart-replay re-recorded the fresh response

    # Control: cutoff 1h in the past -> the entry is fresh enough -> replayed.
    lenient_transport = MockTransport()
    lenient = Replay(
        mode="smart-replay",
        storage=SqliteStorage(db),
        discard_older_than=datetime.now() - timedelta(hours=1),
    )
    async with Api(replay=lenient, transport=lenient_transport) as api:
        replayed = await api.get_thing("mew")
    assert replayed.json() == {"v": "new"}
    assert replayed.is_replay is True
    assert lenient_transport.calls == []


async def test_discard_bad_responses_makes_smart_replay_refetch(tmp_path):
    db = tmp_path / "cassette.db"

    # Phase 1: record a 500 (endpoint has status_policy=None so it doesn't raise).
    broken_transport = MockTransport({f"{BASE}/broken": (500, {"err": "boom"})})
    async with Api(replay=Replay(mode="record", storage=SqliteStorage(db)), transport=broken_transport) as api:
        recorded = await api.get_broken()
    assert recorded.status == 500

    # Control: without the flag, smart-replay happily serves the recorded 500.
    control_transport = MockTransport({f"{BASE}/broken": (200, {"fixed": True})})
    async with Api(replay=Replay(mode="smart-replay", storage=SqliteStorage(db)), transport=control_transport) as api:
        stale = await api.get_broken()
    assert stale.status == 500
    assert stale.is_replay is True
    assert control_transport.calls == []

    # With the flag, the recorded 500 counts as a miss -> live re-fetch of the 200.
    fixed_transport = MockTransport({f"{BASE}/broken": (200, {"fixed": True})})
    picky = Replay(mode="smart-replay", storage=SqliteStorage(db), discard_bad_responses=True)
    async with Api(replay=picky, transport=fixed_transport) as api:
        fixed = await api.get_broken()
    assert fixed.status == 200
    assert fixed.json() == {"fixed": True}
    assert fixed.is_replay is False
    assert len(fixed_transport.calls) == 1


# --------------------------------------------------------------------------- strict miss


async def test_strict_replay_miss_raises_and_never_hits_transport(tmp_path):
    transport = ok_transport()  # could serve the request — must never be asked to
    replay = Replay(mode="replay", storage=SqliteStorage(tmp_path / "empty.db"))
    async with Api(replay=replay, transport=transport) as api:
        with pytest.raises(GracyReplayRequestNotFound):
            await api.get_thing("mew")
    assert transport.calls == []
    assert replay.replays_made == 0


# --------------------------------------------------------------------------- MemoryStorage


async def test_memory_storage_roundtrip():
    storage = MemoryStorage()
    async with Api(replay=Replay(mode="record", storage=storage), transport=ok_transport()) as api:
        live = await api.get_thing("mew")
    assert len(storage) == 1

    transport2 = MockTransport()
    async with Api(replay=Replay(mode="replay", storage=storage), transport=transport2) as api:
        replayed = await api.get_thing("mew")
    assert (replayed.status, replayed.body) == (live.status, live.body)
    assert replayed.is_replay is True
    assert transport2.calls == []


# --------------------------------------------------------------------------- counters


async def test_records_made_and_replays_made_counters(tmp_path):
    db = tmp_path / "cassette.db"

    rec = Replay(mode="record", storage=SqliteStorage(db))
    async with Api(replay=rec, transport=ok_transport()) as api:
        await api.get_thing("mew")
        await api.get_thing("ditto")
    assert rec.records_made == 2
    assert rec.replays_made == 0

    rep = Replay(mode="replay", storage=SqliteStorage(db))
    async with Api(replay=rep, transport=MockTransport()) as api:
        await api.get_thing("mew")
        await api.get_thing("ditto")
        await api.get_thing("mew")  # same recording replays as often as asked
    assert rep.replays_made == 3
    assert rep.records_made == 0


# --------------------------------------------------------------------------- flush on aclose


class SpyStorage:
    """Protocol-only storage (no keyed API) wrapping MemoryStorage, counting calls."""

    def __init__(self) -> None:
        self._inner = MemoryStorage()
        self.flush_calls = 0
        self.record_calls = 0

    async def prepare(self) -> None:
        await self._inner.prepare()

    async def flush(self) -> None:
        self.flush_calls += 1
        await self._inner.flush()

    async def record(self, spec: RequestSpec, response: Response) -> None:
        self.record_calls += 1
        await self._inner.record(spec, response)

    async def find(self, spec: RequestSpec, discard_before: float | None) -> Response | None:
        return await self._inner.find(spec, discard_before)


async def test_aclose_awaits_storage_flush():
    spy = SpyStorage()
    async with Api(replay=Replay(mode="record", storage=spy), transport=ok_transport()) as api:
        await api.get_thing("mew")
        assert spy.flush_calls == 0  # nothing flushes mid-flight
    assert spy.flush_calls == 1  # aclose() awaited storage.flush()
    assert spy.record_calls == 1

    # The protocol-only (spec-shaped) storage face roundtrips too.
    transport2 = MockTransport()
    async with Api(replay=Replay(mode="replay", storage=spy), transport=transport2) as api:
        replayed = await api.get_thing("mew")
    assert replayed.is_replay is True
    assert transport2.calls == []
    assert spy.flush_calls == 2
