"""Migration CLI: the repo's own v1 pickle fixture (.gracy/pokeapi.sqlite3) -> schema v2 -> replay."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from gracy import Gracy, MockTransport, Replay, Response, SqliteStorage
from gracy.replay.migrate import main

V1_FIXTURE = Path(__file__).resolve().parent.parent / ".gracy" / "pokeapi.sqlite3"

pytestmark = pytest.mark.skipif(not V1_FIXTURE.exists(), reason="v1 fixture DB not present")


@pytest.fixture
def v1_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "v1.sqlite3"
    shutil.copyfile(V1_FIXTURE, dst)
    return dst


def test_migrate_refuses_without_trust_flag(v1_copy: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    rc = main([str(v1_copy), str(tmp_path / "v2.db")])
    assert rc == 2
    assert "REFUSING TO MIGRATE" in capsys.readouterr().err
    assert not (tmp_path / "v2.db").exists()


def test_migrate_repo_v1_fixture(v1_copy: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    new_db = tmp_path / "v2.db"
    rc = main([str(v1_copy), str(new_db), "--yes-i-trust-this-file"])
    assert rc == 0

    v1_count = sqlite3.connect(v1_copy).execute("SELECT COUNT(*) FROM gracy_recordings").fetchone()[0]
    con = sqlite3.connect(new_db)
    rows = con.execute(
        "SELECT method, url, status, response_body, schema_version, recorded_at FROM gracy_recordings_v2"
    ).fetchall()
    # One fixture row was pickled by an ancient httpx that imported rfc3986 -
    # unpicklable today. Exactly the version-trap schema v2 kills; the tool
    # must skip it loudly and keep going, not die.
    skipped = v1_count - len(rows)
    assert len(rows) >= 8
    assert skipped <= 1
    if skipped:
        assert "SKIP" in capsys.readouterr().err
    for method, url, status, body, schema_version, recorded_at in rows:
        assert schema_version == 2
        assert isinstance(recorded_at, int)
        assert 200 <= status < 500
        # v2 stores the RAW body bytes (pokeapi JSON) - no pickle envelope.
        assert bytes(body).lstrip()[:1] in (b"{", b"["), f"{method} {url} body is not raw JSON"


async def test_replay_from_migrated_db(v1_copy: Path, tmp_path: Path):
    import asyncio

    new_db = tmp_path / "v2.db"
    # main() drives its own asyncio.run - hop off this test's loop to call it.
    assert await asyncio.to_thread(main, [str(v1_copy), str(new_db), "--yes-i-trust-this-file"]) == 0

    method, url = sqlite3.connect(new_db).execute(
        "SELECT method, url FROM gracy_recordings_v2 WHERE request_body IS NULL OR request_body = ''"
    ).fetchone()

    transport = MockTransport({})  # must never be hit
    replay = Replay(mode="replay", storage=SqliteStorage(new_db))

    class Api(Gracy):
        base_url = ""

    async with Api(transport=transport, replay=replay) as api:
        result = await api.request(method, url)

    assert isinstance(result, Response)
    assert result.is_replay is True
    assert result.status == 200
    assert result.json()  # real pokeapi payload survived the trip
    assert transport.calls == []
    assert replay.replays_made == 1
