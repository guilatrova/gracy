"""Gracy live terminal dashboard.

Reads the snapshot files published by running Gracy clients (schema 1, one
JSON file per client instance in the monitor spool dir) and renders a
full-screen rich dashboard: header, big-number tiles, sparkline activity
chart, per-endpoint table, client-emitted messages (scrollable with ↑/↓,
shown only once a client calls ``message()``) and a per-source footer.

Run it with ``python -m gracy.monitor``. Requires the ``rich`` extra.

Snapshot parsing/aggregation lives in ``gracy.monitor._sources`` and the
rich panel renderers in ``gracy.monitor._widgets``; both are re-exported
here for backwards compatibility.
"""

from __future__ import annotations

import os
import time
import typing as t
from collections import deque

from gracy.monitor._sources import (  # noqa: F401 - re-exported for compatibility
    CLOSED_TILE_GRACE_S,
    LIVE_MAX_AGE_S,
    MAX_MESSAGES,
    SCHEMA_VERSION,
    STALE_MAX_AGE_S,
    Aggregate,
    MessageLog,
    Source,
    _safe_float,
    aggregate,
    aggregate_rows,
    default_spool_dir,
    read_sources,
)
from gracy.monitor._widgets import (  # noqa: F401 - re-exported for compatibility
    _BLOCKS,
    _C_ABORT,
    _C_HOLD,
    _C_INFLIGHT,
    _C_MESSAGE_LEVELS,
    _C_PAUSE,
    _C_RATE,
    _C_REPLAY,
    _C_RETRY,
    _C_THROTTLE,
    MAX_TABLE_ROWS,
    MESSAGE_TAIL_ROWS,
    HistoryPoint,
    _chart,
    _count_text,
    _empty_state,
    _endpoint_cell,
    _endpoint_table,
    _fmt_duration,
    _header,
    _messages_panel,
    _require_rich,
    _sources_footer,
    _tile,
    _tiles_row,
    build_dashboard,
    spark,
)

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from rich.console import Console

# ------------------------------------------------------------------ keyboard

_KEY_SEQUENCES: t.Final = {
    b"[A": "up",
    b"[B": "down",
    b"[5~": "pgup",
    b"[6~": "pgdn",
    b"[H": "home",
    b"[F": "end",
    b"OH": "home",
    b"OF": "end",
}


def decode_keys(data: bytes) -> list[str]:
    """Map raw terminal input to key names ('up', 'down', 'pgup', 'pgdn',
    'home', 'end', 'esc'); vi keys j/k work too. Unknown bytes are ignored."""
    keys: list[str] = []
    i = 0
    while i < len(data):
        byte = data[i : i + 1]
        if byte == b"\x1b":
            for seq, name in _KEY_SEQUENCES.items():
                if data.startswith(seq, i + 1):
                    keys.append(name)
                    i += 1 + len(seq)
                    break
            else:
                keys.append("esc")
                i += 1
            continue
        if byte == b"k":
            keys.append("up")
        elif byte == b"j":
            keys.append("down")
        i += 1
    return keys


def scroll_offset(key: str, offset: int, total: int) -> int:
    """Next messages-panel scroll offset (0 = follow the tail live)."""
    top = max(0, total - MESSAGE_TAIL_ROWS)
    if key == "up":
        offset += 1
    elif key == "down":
        offset -= 1
    elif key == "pgup":
        offset += MESSAGE_TAIL_ROWS
    elif key == "pgdn":
        offset -= MESSAGE_TAIL_ROWS
    elif key == "home":
        offset = top
    elif key in ("end", "esc"):
        offset = 0
    return max(0, min(offset, top))


class _KeyPoller:
    """Non-blocking key reader for message scrolling (POSIX terminals only).

    Inside the context stdin sits in cbreak mode (ISIG stays on, so ctrl+c
    still quits); ``wait(timeout)`` doubles as the frame sleep and returns any
    keys pressed. On Windows or without a TTY it degrades to a plain sleep -
    the dashboard renders normally, only scrolling is unavailable.
    """

    def __init__(self) -> None:
        self._fd: int | None = None
        self._saved: t.Any = None

    def __enter__(self) -> _KeyPoller:
        try:
            import sys
            import termios
            import tty

            if sys.stdin.isatty():
                self._fd = sys.stdin.fileno()
                self._saved = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
        except Exception:
            self._fd = None
        return self

    def __exit__(self, *exc_info: t.Any) -> None:
        if self._fd is not None and self._saved is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def wait(self, timeout: float) -> list[str]:
        if self._fd is None:
            time.sleep(timeout)
            return []
        import select

        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return []
        return decode_keys(os.read(self._fd, 64))


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
    messages = MessageLog()

    if once:
        console.print(build_dashboard(directory, history, messages, 0, window, console.width))
        return 0

    msg_offset = 0
    try:
        with _KeyPoller() as keys, Live(console=console, screen=True, refresh_per_second=fps) as live:
            while True:
                live.update(
                    build_dashboard(directory, history, messages, msg_offset, window, console.width)
                )
                for key in keys.wait(1.0 / fps):
                    msg_offset = scroll_offset(key, msg_offset, len(messages))
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
