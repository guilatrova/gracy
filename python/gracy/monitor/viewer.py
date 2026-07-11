"""Gracy live terminal dashboard.

Reads the snapshot files published by running Gracy clients (schema 1, one
JSON file per client instance in the monitor spool dir) and renders a
full-screen rich dashboard: header, big-number tiles, sparkline activity
chart, per-endpoint table and a per-source footer.

Run it with ``python -m gracy.monitor``. Requires the ``rich`` extra.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
import typing as t
from collections import deque
from dataclasses import dataclass

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from rich.console import Console, RenderableType

# ------------------------------------------------------------------ constants

SCHEMA_VERSION: t.Final = 1
LIVE_MAX_AGE_S: t.Final = 3.0  # newer than this (and not closed) -> LIVE
STALE_MAX_AGE_S: t.Final = 30.0  # older than this -> ignored + cleaned up
CLOSED_TILE_GRACE_S: t.Final = 5.0  # closed sources leave the tiles after this
MAX_TABLE_ROWS: t.Final = 12

_BLOCKS: t.Final = "▁▂▃▄▅▆▇█"

# Restrained palette: one accent per concept, reused everywhere.
_C_INFLIGHT: t.Final = "cyan"
_C_HOLD: t.Final = "yellow"
_C_THROTTLE: t.Final = "magenta"
_C_PAUSE: t.Final = "red"
_C_ABORT: t.Final = "red"
_C_RETRY: t.Final = "dark_orange"
_C_REPLAY: t.Final = "blue"
_C_RATE: t.Final = "green"


def default_spool_dir() -> str:
    """Where publishers write snapshots. Kept in sync with gracy.monitor (publisher)."""
    return os.environ.get("GRACY_MONITOR_DIR") or os.path.join(
        tempfile.gettempdir(), "gracy-monitor"
    )


def _require_rich() -> None:
    try:
        import rich  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SystemExit(
            "The Gracy monitor needs the 'rich' package to render.\n"
            "Install it with: pip install gracy[rich]"
        ) from exc


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


# ------------------------------------------------------------------ sparklines


def spark(values: t.Sequence[float], width: int) -> str:
    """Render `values` as a `width`-character sparkline of ▁..█ blocks.

    The series is rescaled horizontally to exactly `width` samples and
    vertically against its own max. Empty (or all-zero) input renders a
    flat ▁ baseline.
    """
    if width <= 0:
        return ""
    if not values:
        return _BLOCKS[0] * width

    n = len(values)
    resampled = [float(values[min(n - 1, (i * n) // width)]) for i in range(width)]
    vmax = max(float(v) for v in values)
    if vmax <= 0 or not math.isfinite(vmax):
        return _BLOCKS[0] * width

    chars: list[str] = []
    for value in resampled:
        if value <= 0:
            chars.append(_BLOCKS[0])
        else:
            level = int(round((value / vmax) * (len(_BLOCKS) - 1)))
            chars.append(_BLOCKS[max(1, min(level, len(_BLOCKS) - 1))])
    return "".join(chars)


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


# ------------------------------------------------------------------ rendering


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _tile(value: str, caption: str, color: str, hot: bool) -> RenderableType:
    from rich.align import Align
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    value_style = f"bold {color}" if hot else "dim"
    body = Group(
        Align.center(Text(value, style=value_style)),
        Align.center(Text(caption, style="dim")),
    )
    return Panel(body, border_style=f"{color}" if hot else "dim", padding=(0, 0))


def _tiles_row(agg: Aggregate) -> RenderableType:
    from rich.table import Table

    paused_value = f"{agg.paused_max:.1f}s" if agg.paused_max > 0 else "0"
    tiles = [
        _tile(str(agg.in_flight), "IN-FLIGHT", _C_INFLIGHT, agg.in_flight > 0),
        _tile(str(agg.pending), "ON HOLD", _C_HOLD, agg.pending > 0),
        _tile(str(agg.throttle_waits), "THROTTLES", _C_THROTTLE, agg.throttle_waits > 0),
        _tile(paused_value, "PAUSED", _C_PAUSE, agg.paused_max > 0),
        _tile(str(agg.aborts), "ABORTS", _C_ABORT, agg.aborts > 0),
        _tile(str(agg.retries), "RETRIES", _C_RETRY, agg.retries > 0),
        _tile(str(agg.replays), "REPLAYS", _C_REPLAY, agg.replays > 0),
    ]
    grid = Table.grid(expand=True)
    for _ in tiles:
        grid.add_column(ratio=1)
    grid.add_row(*tiles)
    return grid


def _header(sources: list[Source], agg: Aggregate, now: float) -> RenderableType:
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    live = [s for s in sources if s.state == "live"]
    engines = sorted({str(s.data.get("engine", "?")) for s in sources})
    right = Text(justify="right")
    right.append(f"{len(sources)} source{'s' if len(sources) != 1 else ''}", style="bold")
    for engine in engines:
        right.append("  ")
        color = "orange3" if engine == "rust" else "blue"
        right.append(f" {engine} ", style=f"bold black on {color}")
    if live:
        oldest = min(float(s.data.get("started_at", now)) for s in live)
        right.append(f"  up {_fmt_duration(now - oldest)}", style="dim")
    right.append(f"  {agg.req_per_sec:.1f} req/s", style=f"bold {_C_RATE}")

    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_row(Text("⚡ GRACY MONITOR", style="bold"), right)
    return Panel(grid, box=box.ROUNDED, border_style="dim", padding=(0, 1))


HistoryPoint = t.Tuple[float, int, int, float]  # (ts, in_flight, pending, req_per_sec)


def _chart(
    history: t.Sequence[HistoryPoint], window: int, width: int, agg: Aggregate
) -> RenderableType:
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    label_w, value_w = 10, 8
    spark_w = max(10, width - label_w - value_w - 6)  # panel borders + padding

    now = time.time()
    recent = [point for point in history if now - point[0] <= window]

    def series(index: int) -> list[float]:
        values = [float(point[index]) for point in recent]
        if len(values) < spark_w:  # right-align young history against a flat baseline
            values = [0.0] * (spark_w - len(values)) + values
        return values

    rows: list[tuple[str, str, list[float], str]] = [
        ("in-flight", _C_INFLIGHT, series(1), str(agg.in_flight)),
        ("on hold", _C_HOLD, series(2), str(agg.pending)),
        ("req/s", _C_RATE, series(3), f"{agg.req_per_sec:.1f}"),
    ]

    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=label_w, justify="left")
    grid.add_column(width=spark_w, justify="left", no_wrap=True)
    grid.add_column(width=value_w, justify="right")
    for label, color, values, current in rows:
        line = spark(values, spark_w)
        style = color if any(v > 0 for v in values) else "dim"
        grid.add_row(
            Text(label, style="dim"),
            Text(line, style=style),
            Text(current, style=f"bold {color}"),
        )
    return Panel(
        grid,
        title=f"activity — last {window}s",
        title_align="left",
        box=box.ROUNDED,
        border_style="dim",
        padding=(0, 1),
    )


def _endpoint_cell(uurl: str, max_width: int) -> RenderableType:
    from rich.text import Text

    text = Text(overflow="ellipsis", no_wrap=True)
    shown = uurl
    if len(shown) > max_width:
        shown = "…" + shown[-(max_width - 1) :]
    # dim the base (scheme://host), highlight the path tail
    split_at = shown.find("/", shown.find("://") + 3) if "://" in shown else -1
    if split_at > 0:
        text.append(shown[:split_at], style="dim")
        text.append(shown[split_at:])
    else:
        text.append(shown)
    return text


def _endpoint_table(rows: list[dict[str, t.Any]], width: int) -> RenderableType:
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    # 7 fixed numeric columns (43 cells) + inter-column padding + panel borders:
    # whatever is left belongs to the endpoint column, and we left-truncate to it
    # ourselves so the uurl always keeps its tail path.
    uurl_width = max(20, width - 67)

    table = Table(
        box=box.SIMPLE_HEAD, expand=False, padding=(0, 1), pad_edge=False, show_edge=False
    )
    table.add_column("endpoint", width=uurl_width, no_wrap=True, overflow="crop")
    table.add_column("reqs", justify="right", width=6)
    table.add_column("ok%", justify="right", width=6)
    table.add_column("retries", justify="right", width=7)
    table.add_column("thr", justify="right", width=5)
    table.add_column("aborts", justify="right", width=6)
    table.add_column("p95 ms", justify="right", width=7)
    table.add_column("req/s", justify="right", width=6)
    for row in rows[:MAX_TABLE_ROWS]:
        ok = float(row["success_rate"])
        ok_style = "green" if ok >= 99 else ("yellow" if ok >= 90 else "red")
        table.add_row(
            _endpoint_cell(str(row["uurl"]), uurl_width),
            str(row["total"]),
            Text(f"{ok:.1f}", style=ok_style),
            _count_text(row["retries"], _C_RETRY),
            _count_text(row["throttles"], _C_THROTTLE),
            _count_text(row["aborts"], _C_ABORT),
            f"{float(row['p95_latency']) * 1000.0:.0f}",
            f"{float(row['req_rate_per_sec']):.1f}",
        )
    if len(rows) > MAX_TABLE_ROWS:
        table.add_row(Text(f"… {len(rows) - MAX_TABLE_ROWS} more", style="dim"))
    if not rows:
        table.add_row(Text("no requests tracked yet", style="dim"))
    return Panel(
        table,
        title="endpoints",
        title_align="left",
        box=box.ROUNDED,
        border_style="dim",
        padding=(0, 1),
    )


def _count_text(value: t.Any, color: str) -> RenderableType:
    from rich.text import Text

    count = int(value)
    return Text(str(count), style=color if count else "dim")


def _sources_footer(sources: list[Source]) -> RenderableType:
    from rich.console import Group
    from rich.text import Text

    lines: list[Text] = []
    for src in sources:
        text = Text(no_wrap=True, overflow="ellipsis")
        state = src.state
        if state == "live":
            text.append("● ", style="green")
            text.append("LIVE ", style="bold green")
        elif state == "stale":
            text.append("○ ", style="yellow")
            text.append(f"STALE (ago {src.age:.0f}s) ", style="yellow")
        else:
            text.append("◌ ", style="dim")
            text.append("closed ", style="dim")
        style = "dim" if state != "live" else ""
        text.append(
            f" {src.client} pid {src.data.get('pid', '?')} ({src.data.get('engine', '?')})",
            style=style,
        )
        lines.append(text)
    lines.append(Text("ctrl+c to quit", style="dim"))
    return Group(*lines)


def _empty_state(directory: str) -> RenderableType:
    from rich import box
    from rich.align import Align
    from rich.panel import Panel
    from rich.text import Text

    body = Text(justify="center")
    body.append("No Gracy clients are publishing snapshots yet.\n\n", style="bold")
    body.append("Enable monitoring on a client:\n", style="dim")
    body.append("    Gracy(monitor=True)", style="bold cyan")
    body.append("   or   ", style="dim")
    body.append("GRACY_MONITOR=1\n\n", style="bold cyan")
    body.append("watching ", style="dim")
    body.append(directory, style="underline")
    panel = Panel(
        Align.center(body, vertical="middle"),
        title="⚡ GRACY MONITOR",
        box=box.ROUNDED,
        border_style="dim",
        padding=(1, 4),
    )
    return Align.center(panel)


def build_dashboard(
    directory: str,
    history: t.MutableSequence[HistoryPoint],
    window: int,
    width: int,
) -> RenderableType:
    """Read the spool dir, push one history sample, and build the full frame."""
    from rich.console import Group

    now = time.time()
    sources = read_sources(directory, now)
    if not sources:
        history.clear()
        return _empty_state(directory)

    agg = aggregate(sources)
    history.append((now, agg.in_flight, agg.pending, agg.req_per_sec))
    rows = aggregate_rows(sources)

    return Group(
        _header(sources, agg, now),
        _tiles_row(agg),
        _chart(history, window, width, agg),
        _endpoint_table(rows, width),
        _sources_footer(sources),
    )


# ------------------------------------------------------------------ entrypoint


def _make_console() -> Console:
    from rich.console import Console

    console = Console()
    if not console.is_terminal:  # stable layout for pipes / tests / CI
        console = Console(width=100)
    return console


def run(directory: str, fps: float, window: int, once: bool) -> int:
    _require_rich()
    from rich.live import Live

    console = _make_console()
    fps = max(0.2, fps)
    max_points = max(int(window * fps) + 8, 64)
    history: deque[HistoryPoint] = deque(maxlen=max_points)

    if once:
        console.print(build_dashboard(directory, history, window, console.width))
        return 0

    try:
        with Live(console=console, screen=True, refresh_per_second=fps) as live:
            while True:
                live.update(build_dashboard(directory, history, window, console.width))
                time.sleep(1.0 / fps)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: t.Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m gracy.monitor",
        description="Live terminal dashboard for running Gracy clients.",
    )
    parser.add_argument(
        "--dir",
        default=default_spool_dir(),
        help="snapshot spool directory (default: $GRACY_MONITOR_DIR or the system temp dir)",
    )
    parser.add_argument("--fps", type=float, default=4.0, help="refresh rate (frames/second)")
    parser.add_argument(
        "--once", action="store_true", help="render exactly one frame to stdout and exit"
    )
    parser.add_argument(
        "--window", type=int, default=60, help="seconds of chart history to keep (default 60)"
    )
    args = parser.parse_args(argv)
    return run(directory=args.dir, fps=args.fps, window=args.window, once=args.once)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
