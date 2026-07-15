"""Rich rendering widgets for the Gracy monitor dashboard.

Builds each panel of the full-screen frame - header, tiles, sparkline chart,
endpoint table, messages panel and per-source footer - and composes them via
``build_dashboard``. rich itself is imported lazily inside each renderer.
"""

from __future__ import annotations

import math
import time
import typing as t

from gracy.monitor._sources import (
    Aggregate,
    MessageLog,
    Source,
    aggregate,
    aggregate_rows,
    read_sources,
)

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from rich.console import RenderableType

# ------------------------------------------------------------------ constants

MAX_TABLE_ROWS: t.Final = 12
MESSAGE_TAIL_ROWS: t.Final = 3  # visible window; panel shrinks below this when fewer exist

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
_C_MESSAGE_LEVELS: t.Final = {"info": "white", "warn": "yellow", "error": "red"}


def _require_rich() -> None:
    try:
        import rich  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SystemExit(
            "The Gracy monitor needs the 'rich' package to render.\n"
            "Install it with: pip install gracy[rich]"
        ) from exc


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
        title=f"activity · last {window}s",
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


def _messages_panel(log: MessageLog, offset: int) -> RenderableType | None:
    """Client-emitted messages: hidden until the first one arrives, then grows
    one line per message up to MESSAGE_TAIL_ROWS and follows the tail unless
    scrolled back (offset > 0)."""
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    entries, start, total = log.window(offset, MESSAGE_TAIL_ROWS)
    if not entries:
        return None

    lines: list[Text] = []
    for entry in entries:
        level = entry["level"]
        color = _C_MESSAGE_LEVELS.get(level, _C_MESSAGE_LEVELS["info"])
        line = Text(no_wrap=True, overflow="ellipsis")
        ts = entry["ts"]
        stamp = time.strftime("%H:%M:%S", time.localtime(ts)) if ts > 0 else "--:--:--"
        line.append(f"{stamp} ", style="dim")
        line.append("• ", style=color)
        if log.multi_client:
            line.append(f"{entry['client']} ", style="dim")
        line.append(entry["text"], style="" if level == "info" else color)
        lines.append(line)

    scrolled = start + len(entries) < total
    if scrolled:
        title = f"messages · {start + 1}–{start + len(entries)}/{total}"
        subtitle = "↑/↓ scroll · esc live"
    else:
        title = f"messages · {total}"
        subtitle = "↑ older" if total > MESSAGE_TAIL_ROWS else None
    return Panel(
        Group(*lines),
        title=title,
        title_align="left",
        subtitle=Text(subtitle, style="dim") if subtitle else None,
        subtitle_align="right",
        box=box.ROUNDED,
        border_style="dim",
        padding=(0, 1),
    )


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
    messages: MessageLog,
    msg_offset: int,
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
    messages.ingest(sources)

    panels = [
        _header(sources, agg, now),
        _tiles_row(agg),
        _chart(history, window, width, agg),
        _endpoint_table(rows, width),
    ]
    messages_panel = _messages_panel(messages, msg_offset)
    if messages_panel is not None:
        panels.append(messages_panel)
    panels.append(_sources_footer(sources))
    return Group(*panels)
