"""Monitor publisher: spool-file lifecycle, schema-1 shape, error resilience.

All against MockTransport in a tmp GRACY_MONITOR_DIR - no real network, no
shared global spool directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import typing as t
from pathlib import Path

import pytest

from gracy import Gracy, get
from gracy.testing import MockTransport

BASE = "https://api.test"
INTERVAL = 0.25  # publisher default


class MonitoredAPI(Gracy):
    base_url = BASE

    @get("/thing/{name}")
    async def get_thing(self, name: str) -> dict: ...


def ok_transport() -> MockTransport:
    return MockTransport({f"GET {BASE}/thing/*": {"ok": True}})


@pytest.fixture
def spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GRACY_MONITOR_DIR", str(tmp_path))
    monkeypatch.delenv("GRACY_MONITOR", raising=False)
    return tmp_path


def spool_files(spool: Path) -> list[Path]:
    return sorted(spool.glob("*.json"))


def read_snapshot(spool: Path) -> dict[str, t.Any]:
    files = spool_files(spool)
    assert len(files) == 1, f"expected exactly one spool file, got {files}"
    return json.loads(files[0].read_text(encoding="utf-8"))


async def wait_for(predicate: t.Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s")


# --------------------------------------------------------------------- schema


async def test_file_appears_quickly_and_matches_schema(spool: Path):
    async with MonitoredAPI(transport=ok_transport(), monitor=True):
        await wait_for(lambda: bool(spool_files(spool)), timeout=0.6)
        doc = read_snapshot(spool)

        assert doc["schema"] == 1
        assert isinstance(doc["ts"], float)
        assert isinstance(doc["started_at"], float)
        assert doc["ts"] >= doc["started_at"]
        assert doc["closed"] is False
        assert doc["pid"] == os.getpid()
        assert doc["client"] == "MonitoredAPI"
        assert doc["engine"] in ("rust", "python")

        queue = doc["queue"]
        assert isinstance(queue["pending"], int)
        assert isinstance(queue["in_flight"], int)
        assert isinstance(queue["throttle_hits"], dict)
        assert isinstance(queue["throttled_by_uurl"], dict)
        assert isinstance(queue["paused"], dict)

        totals = doc["totals"]
        assert set(totals) == {"requests", "aborts", "retries", "replays", "req_per_sec"}
        assert totals["requests"] == 0
        assert isinstance(totals["req_per_sec"], (int, float))

        assert doc["rows"] == []

    # spool file naming: {pid}-{ClientClassName}-{8-hex}.json
    name = spool_files(spool)[0].name
    pid, client, suffix = name.split("-")
    assert pid == str(os.getpid())
    assert client == "MonitoredAPI"
    hex_id = suffix.removesuffix(".json")
    assert len(hex_id) == 8
    int(hex_id, 16)  # must be valid hex


async def test_ts_advances_between_snapshots(spool: Path):
    async with MonitoredAPI(transport=ok_transport(), monitor=True):
        await wait_for(lambda: bool(spool_files(spool)), timeout=0.6)
        first = read_snapshot(spool)["ts"]
        await wait_for(lambda: read_snapshot(spool)["ts"] > first)


async def test_totals_and_rows_update_after_requests(spool: Path):
    async with MonitoredAPI(transport=ok_transport(), monitor=True) as api:
        for name in ("mew", "ditto", "pikachu"):
            assert await api.get_thing(name) == {"ok": True}

        await wait_for(lambda: bool(spool_files(spool)) and read_snapshot(spool)["totals"]["requests"] == 3)
        doc = read_snapshot(spool)

        assert doc["totals"]["requests"] == 3
        assert doc["totals"]["aborts"] == 0
        assert doc["totals"]["req_per_sec"] > 0

        assert len(doc["rows"]) == 1
        row = doc["rows"][0]
        assert row["uurl"] == f"{BASE}/thing/{{name}}"
        assert row["total"] == 3
        assert row["success_rate"] == 100.0
        assert row["retries"] == 0
        assert row["throttles"] == 0
        assert row["replays"] == 0
        assert row["aborts"] == 0
        assert isinstance(row["avg_latency"], float)
        assert isinstance(row["p95_latency"], float)
        assert row["req_rate_per_sec"] > 0

        # queue is idle after the awaits complete
        assert doc["queue"]["in_flight"] == 0


# --------------------------------------------------------------------- lifecycle


async def test_final_snapshot_marks_closed_and_file_survives(spool: Path):
    api = MonitoredAPI(transport=ok_transport(), monitor=True)
    async with api:
        await api.get_thing("mew")
        await wait_for(lambda: bool(spool_files(spool)))
        assert read_snapshot(spool)["closed"] is False

    doc = read_snapshot(spool)  # file NOT deleted on close
    assert doc["closed"] is True
    assert doc["totals"]["requests"] == 1


async def test_disabled_client_writes_nothing(spool: Path):
    # default (monitor=None, env unset) and explicit False both stay silent
    for kwargs in ({}, {"monitor": False}):
        async with MonitoredAPI(transport=ok_transport(), **kwargs) as api:
            await api.get_thing("mew")
            await asyncio.sleep(INTERVAL * 2)
        assert spool_files(spool) == []
        assert api._monitor_publisher is None  # no task was ever spawned


async def test_env_var_enables_monitor(spool: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GRACY_MONITOR", "1")
    async with MonitoredAPI(transport=ok_transport()):  # monitor=None -> env wins
        await wait_for(lambda: bool(spool_files(spool)), timeout=0.6)
    assert read_snapshot(spool)["closed"] is True


# --------------------------------------------------------------------- messages


async def test_messages_land_in_snapshot(spool: Path):
    api = MonitoredAPI(transport=ok_transport(), monitor=True)
    api.message("queued before build")  # pre-build works (inert buffer)
    async with api:
        api.message("base resources fetched")
        api.message("rate limit near ceiling", level="warn")
        api.message("kaboom", level="bogus")  # unknown level falls back to info

        await wait_for(lambda: bool(spool_files(spool)) and len(read_snapshot(spool).get("messages", [])) == 4)
        msgs = read_snapshot(spool)["messages"]

        assert [m["text"] for m in msgs] == [
            "queued before build",
            "base resources fetched",
            "rate limit near ceiling",
            "kaboom",
        ]
        assert [m["level"] for m in msgs] == ["info", "info", "warn", "info"]
        assert [m["id"] for m in msgs] == [1, 2, 3, 4]  # per-client monotonic ids
        assert all(isinstance(m["ts"], float) for m in msgs)

    api.message("after close never raises")  # fire-and-forget even when closed


async def test_no_messages_key_is_empty_list(spool: Path):
    async with MonitoredAPI(transport=ok_transport(), monitor=True):
        await wait_for(lambda: bool(spool_files(spool)), timeout=0.6)
        assert read_snapshot(spool)["messages"] == []


# --------------------------------------------------------------------- resilience


async def test_publisher_survives_get_snapshot_errors(spool: Path):
    async with MonitoredAPI(transport=ok_transport(), monitor=True) as api:
        await wait_for(lambda: bool(spool_files(spool)), timeout=0.6)

        # Poison the metrics accessor the snapshot closure relies on.
        metrics = api._metrics
        assert metrics is not None
        original = metrics.monitor_rows

        def boom(*args: t.Any, **kwargs: t.Any) -> t.Any:
            raise RuntimeError("injected monitor failure")

        metrics.monitor_rows = boom  # type: ignore[method-assign]
        await asyncio.sleep(INTERVAL * 2)  # a few failing ticks - must not crash

        metrics.monitor_rows = original  # type: ignore[method-assign]
        healthy = read_snapshot(spool)["ts"]
        await wait_for(lambda: read_snapshot(spool)["ts"] > healthy)  # loop still alive

        await api.get_thing("mew")  # the app itself was never affected
