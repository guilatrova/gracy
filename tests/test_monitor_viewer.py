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

from gracy.monitor.viewer import spark

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
) -> dict[str, t.Any]:
    return {
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


def test_once_skips_corrupt_files(tmp_path: Path) -> None:
    now = time.time()
    (tmp_path / "broken.json").write_text('{"schema": 1, "ts":', encoding="utf-8")
    _write(tmp_path, "444-SoloAPI-11112222.json", _snapshot(client="SoloAPI", pid=444, ts=now))

    result = _render_once(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "SoloAPI" in result.stdout
