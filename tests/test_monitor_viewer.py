"""Tests for the gracy.monitor terminal dashboard (viewer side).

The subprocess tests write synthetic schema-1 snapshot files into a tmp dir
and render one frame via ``python -m gracy.monitor --once --dir ...``; the
unit tests exercise the sparkline helper directly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import typing as t
from pathlib import Path

from gracy.monitor.viewer import MessageLog, Source, decode_keys, scroll_offset, spark

# ---------------------------------------------------------------- helpers


def _snapshot(
    *,
    client: str,
    pid: int,
    engine: str = "rust",
    ts: float,
    closed: bool = False,
    rows: list[dict[str, t.Any]] | None = None,
    queue: dict[str, t.Any] | None = None,
    totals: dict[str, t.Any] | None = None,
    messages: list[dict[str, t.Any]] | None = None,
) -> dict[str, t.Any]:
    return {
        "messages": messages or [],
        "schema": 1,
        "ts": ts,
        "started_at": ts - 120.0,
        "closed": closed,
        "pid": pid,
        "client": client,
        "engine": engine,
        "queue": queue
        or {
            "pending": 0,
            "in_flight": 0,
            "throttle_hits": {},
            "throttled_by_uurl": {},
            "paused": {},
        },
        "totals": totals
        or {"requests": 0, "aborts": 0, "retries": 0, "replays": 0, "req_per_sec": 0.0},
        "rows": rows or [],
    }


def _write(directory: Path, name: str, snapshot: dict[str, t.Any]) -> None:
    (directory / name).write_text(json.dumps(snapshot), encoding="utf-8")


def _render_once(directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gracy.monitor", "--once", "--dir", str(directory)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


# ---------------------------------------------------------------- spark()


def test_spark_empty_history_renders_dim_baseline() -> None:
    assert spark([], 12) == "▁" * 12


def test_spark_all_zero_renders_baseline() -> None:
    assert spark([0.0, 0.0, 0.0], 8) == "▁" * 8


def test_spark_stretches_short_series_to_width() -> None:
    # 2 samples over 6 slots: first half baseline, second half peak
    assert spark([0.0, 10.0], 6) == "▁▁▁███"


def test_spark_compresses_long_series_to_width() -> None:
    out = spark([float(v) for v in range(1000)], 20)
    assert len(out) == 20
    assert out[-1] == "█"  # the max sample always tops out
    assert out[0] == "▁"  # zero start stays on the baseline


def test_spark_output_is_always_exactly_width_blocks() -> None:
    blocks = set("▁▂▃▄▅▆▇█")
    for values in ([1.0], [5.0, 5.0, 5.0], list(range(7)), [0.3, 0.1, 0.9]):
        for width in (1, 3, 40):
            out = spark([float(v) for v in values], width)
            assert len(out) == width
            assert set(out) <= blocks


def test_spark_zero_width() -> None:
    assert spark([1.0, 2.0], 0) == ""


# ---------------------------------------------------------------- --once frames


def test_once_renders_live_stale_and_closed_sources(tmp_path: Path) -> None:
    now = time.time()
    _write(
        tmp_path,
        "111-PokeAPI-abcd1234.json",
        _snapshot(
            client="PokeAPI",
            pid=111,
            engine="rust",
            ts=now,
            queue={
                "pending": 4,
                "in_flight": 2,
                "throttle_hits": {"0": 12},
                "throttled_by_uurl": {"https://pokeapi.co/api/v2/pokemon/{NAME}": 12},
                "paused": {},
            },
            totals={
                "requests": 120,
                "aborts": 1,
                "retries": 4,
                "replays": 0,
                "req_per_sec": 3.1,
            },
            rows=[
                {
                    "uurl": "https://pokeapi.co/api/v2/pokemon/{NAME}",
                    "total": 120,
                    "success_rate": 98.3,
                    "retries": 4,
                    "throttles": 12,
                    "replays": 0,
                    "aborts": 1,
                    "avg_latency": 0.12,
                    "p95_latency": 0.4,
                    "req_rate_per_sec": 1.5,
                }
            ],
        ),
    )
    _write(
        tmp_path,
        "222-GithubAPI-deadbeef.json",
        _snapshot(client="GithubAPI", pid=222, engine="python", ts=now - 10.0),
    )
    _write(
        tmp_path,
        "333-OldAPI-cafebabe.json",
        _snapshot(client="OldAPI", pid=333, ts=now - 1.0, closed=True),
    )

    result = _render_once(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout

    # every source shows up in the footer
    assert "PokeAPI" in out
    assert "GithubAPI" in out
    assert "OldAPI" in out
    # big-tile captions
    assert "IN-FLIGHT" in out
    assert "ON HOLD" in out
    # sparkline chart rendered block characters
    assert "▁" in out
    # endpoint uurl keeps its tail path even when left-truncated
    assert "pokemon/{NAME}" in out
    # the stale source is flagged
    assert "STALE" in out


def test_once_empty_dir_shows_how_to_enable(tmp_path: Path) -> None:
    result = _render_once(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "monitor=True" in out
    assert "GRACY_MONITOR=1" in out
    # the watched spool dir is displayed (long paths wrap inside the panel,
    # so only check the label and the path's tail component)
    assert "watching" in out
    assert tmp_path.name[-12:] in out.replace("\n", "").replace("│", "").replace(" ", "")


# ---------------------------------------------------------------- MessageLog


def _msg_source(path: str, client: str, msgs: list[dict[str, t.Any]]) -> Source:
    return Source(path=path, data={"client": client, "messages": msgs}, age=0.0)


def _msg(mid: int, text: str, *, ts: float = 0.0, level: str = "info") -> dict[str, t.Any]:
    return {"id": mid, "ts": ts or float(mid), "level": level, "text": text}


def test_messagelog_dedupes_resent_buffers() -> None:
    log = MessageLog()
    src = _msg_source("a.json", "API", [_msg(1, "one"), _msg(2, "two")])
    log.ingest([src])
    log.ingest([src])  # snapshots re-send the whole buffer every frame
    assert len(log) == 2


def test_messagelog_retains_entries_beyond_client_buffer_rotation() -> None:
    log = MessageLog()
    log.ingest([_msg_source("a.json", "API", [_msg(1, "one"), _msg(2, "two")])])
    # client buffer rotated: id 1 is gone from the snapshot, ids 3-4 are new
    log.ingest([_msg_source("a.json", "API", [_msg(2, "two"), _msg(3, "three"), _msg(4, "four")])])
    entries, start, total = log.window(0, 3)
    assert total == 4  # "one" was never lost
    assert [e["text"] for e in entries] == ["two", "three", "four"]
    assert start == 1


def test_messagelog_window_clamps_offset_and_shrinks_below_rows() -> None:
    log = MessageLog()
    log.ingest([_msg_source("a.json", "API", [_msg(i, f"m{i}") for i in range(1, 6)])])
    entries, start, total = log.window(99, 3)  # over-scrolled: clamp to oldest window
    assert (start, total) == (0, 5)
    assert [e["text"] for e in entries] == ["m1", "m2", "m3"]

    small = MessageLog()
    small.ingest([_msg_source("a.json", "API", [_msg(1, "only")])])
    entries, start, total = small.window(0, 3)
    assert [e["text"] for e in entries] == ["only"]  # 1 line, not padded to 3
    assert (start, total) == (0, 1)


def test_messagelog_interleaves_sources_by_ts_and_caps_history() -> None:
    log = MessageLog(maxlen=3)
    log.ingest(
        [
            _msg_source("a.json", "A", [_msg(1, "a-late", ts=20.0)]),
            _msg_source("b.json", "B", [_msg(1, "b-early", ts=10.0)]),
        ]
    )
    entries, _, _ = log.window(0, 3)
    assert [e["text"] for e in entries] == ["b-early", "a-late"]
    assert log.multi_client

    log.ingest([_msg_source("a.json", "A", [_msg(2, "x", ts=30.0), _msg(3, "y", ts=40.0)])])
    entries, _, total = log.window(0, 3)
    assert total == 3  # capped: oldest evicted
    assert [e["text"] for e in entries] == ["a-late", "x", "y"]


def test_messagelog_skips_malformed_entries() -> None:
    log = MessageLog()
    log.ingest(
        [
            _msg_source(
                "a.json",
                "API",
                [{"text": "no id"}, {"id": "NaN", "text": "bad id"}, "not-a-dict", _msg(7, "ok")],  # type: ignore[list-item]
            )
        ]
    )
    entries, _, total = log.window(0, 3)
    assert total == 1
    assert entries[0]["text"] == "ok"


# ---------------------------------------------------------------- keyboard


def test_decode_keys_arrows_pages_and_vi() -> None:
    assert decode_keys(b"\x1b[A\x1b[B") == ["up", "down"]
    assert decode_keys(b"\x1b[5~\x1b[6~") == ["pgup", "pgdn"]
    assert decode_keys(b"\x1b[H\x1b[F\x1bOF") == ["home", "end", "end"]
    assert decode_keys(b"kj") == ["up", "down"]
    assert decode_keys(b"\x1b") == ["esc"]
    assert decode_keys(b"zzz") == []


def test_scroll_offset_clamps_and_resets() -> None:
    total = 10  # top offset = 7 (window of 3)
    assert scroll_offset("up", 0, total) == 1
    assert scroll_offset("up", 7, total) == 7  # already at the oldest window
    assert scroll_offset("down", 1, total) == 0
    assert scroll_offset("down", 0, total) == 0
    assert scroll_offset("pgup", 0, total) == 3
    assert scroll_offset("home", 0, total) == 7
    assert scroll_offset("end", 5, total) == 0
    assert scroll_offset("esc", 5, total) == 0
    assert scroll_offset("up", 0, 2) == 0  # fewer messages than the window: no scrolling


# ---------------------------------------------------------------- messages panel (--once)


def test_once_renders_messages_panel_when_present(tmp_path: Path) -> None:
    now = time.time()
    _write(
        tmp_path,
        "555-MsgAPI-aaaa1111.json",
        _snapshot(
            client="MsgAPI",
            pid=555,
            ts=now,
            messages=[
                {"id": 1, "ts": now - 5, "level": "info", "text": "base resources fetched"},
                {"id": 2, "ts": now - 1, "level": "warn", "text": "rate limit near ceiling"},
            ],
        ),
    )
    result = _render_once(tmp_path)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "messages · 2" in out
    assert "base resources fetched" in out
    assert "rate limit near ceiling" in out


def test_once_hides_messages_panel_when_empty(tmp_path: Path) -> None:
    _write(tmp_path, "666-QuietAPI-bbbb2222.json", _snapshot(client="QuietAPI", pid=666, ts=time.time()))
    result = _render_once(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "messages" not in result.stdout


def test_once_skips_corrupt_files(tmp_path: Path) -> None:
    now = time.time()
    (tmp_path / "broken.json").write_text('{"schema": 1, "ts":', encoding="utf-8")
    _write(tmp_path, "444-SoloAPI-11112222.json", _snapshot(client="SoloAPI", pid=444, ts=now))

    result = _render_once(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "SoloAPI" in result.stdout
