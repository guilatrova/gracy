"""Live monitor publisher: periodic JSON snapshots for the `gracy monitor` viewer.

The publisher is the WRITE side of the monitor contract (schema version 1).
Each built client with monitoring enabled owns one MonitorPublisher that
atomically rewrites a single spool file every ``interval`` seconds:

    {monitor_dir()}/{pid}-{ClientClassName}-{8-hex-id}.json

Writes are atomic (tmp sibling + os.replace) so the viewer never reads a
torn file. Snapshot/write errors are swallowed and logged - monitoring must
never take the application down. ``aclose()`` writes one final snapshot with
``closed: true`` and leaves the file behind for the viewer to reap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import typing as t
import uuid

__all__ = ["MonitorPublisher", "monitor_dir", "SCHEMA_VERSION"]

SCHEMA_VERSION: t.Final = 1

_logger = logging.getLogger("gracy")


def monitor_dir() -> str:
    """Resolve (and create) the monitor spool directory.

    ``GRACY_MONITOR_DIR`` wins; default is ``{tempdir}/gracy-monitor``.
    """
    path = os.environ.get("GRACY_MONITOR_DIR") or os.path.join(
        tempfile.gettempdir(), "gracy-monitor"
    )
    os.makedirs(path, exist_ok=True)
    return path


class MonitorPublisher:
    """Background task that publishes one client's live snapshot at ~1/interval Hz."""

    def __init__(self, client_name: str, engine: str, interval: float = 0.25) -> None:
        self._client_name = client_name
        self._engine = engine
        self._interval = interval
        self._instance_id = uuid.uuid4().hex[:8]
        self._get_snapshot: t.Callable[[], dict[str, t.Any]] | None = None
        self._task: asyncio.Task[None] | None = None
        self.started_at: float = time.time()
        self.path: str = os.path.join(
            monitor_dir(), f"{os.getpid()}-{client_name}-{self._instance_id}.json"
        )

    # ------------------------------------------------------------------ lifecycle

    async def start(self, get_snapshot: t.Callable[[], dict[str, t.Any]]) -> None:
        """Spawn the publish loop. Errors inside the loop never propagate."""
        self._get_snapshot = get_snapshot
        self.started_at = time.time()
        self._task = asyncio.get_running_loop().create_task(
            self._loop(), name=f"gracy-monitor-{self._client_name}"
        )

    async def aclose(self) -> None:
        """Cancel the loop and write one final ``closed: true`` snapshot (best effort).

        The spool file is intentionally NOT deleted - the viewer greys out and
        reaps closed files on its own schedule.
        """
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - _loop already swallows
                pass
        self._write_snapshot(closed=True)

    # ------------------------------------------------------------------ internals

    async def _loop(self) -> None:
        while True:
            self._write_snapshot(closed=False)
            await asyncio.sleep(self._interval)

    def _write_snapshot(self, *, closed: bool) -> None:
        """Envelope + atomic write. Swallows and logs EVERY error - a broken
        snapshot must never crash the app (or the publish loop)."""
        try:
            body = self._get_snapshot() if self._get_snapshot is not None else {}
            doc: dict[str, t.Any] = {
                "schema": SCHEMA_VERSION,
                "ts": time.time(),
                "started_at": self.started_at,
                "closed": closed,
                "pid": os.getpid(),
                "client": self._client_name,
                "engine": self._engine,
                **body,
            }
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, default=str)
            os.replace(tmp, self.path)
        except Exception as exc:
            _logger.warning("gracy monitor: snapshot publish failed: %r", exc)
