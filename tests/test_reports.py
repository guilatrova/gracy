"""Reports v2: frozen GracyReport snapshots, pure printers, per-instance metrics.

Scripted mix (via MockTransport):
  2x 200 on /a, 1x 404 on /a, 1x 500 on /b, 1x transport exception on /c,
  and one retried endpoint /flaky (503 then 200).
"""

from __future__ import annotations

import dataclasses
import logging
import typing as t

import pytest

from gracy import Gracy, GracyRequestFailed, NonOkResponse, Retry, get, status
from gracy.reports import TOTAL_LABEL, GracyReport, GracyRequestRow
from gracy.testing import MockTransport

BASE = "https://reports.test"

A = f"{BASE}/a"
B = f"{BASE}/b"
C = f"{BASE}/c"
FLAKY = f"{BASE}/flaky"


class ReportAPI(Gracy):
    base_url = BASE

    @get("/a")
    async def get_a(self) -> dict: ...

    @get("/b")
    async def get_b(self) -> dict: ...

    @get("/c")
    async def get_c(self) -> dict: ...

    @get("/flaky", retry=Retry(on=status(503), attempts=3, wait=0.0))
    async def get_flaky(self) -> dict: ...


def script(*values: t.Any) -> t.Callable[[t.Any], t.Any]:
    """MockTransport callable: pops scripted values in order (last one repeats).
    Exception instances are raised to simulate transport errors."""
    remaining = list(values)

    def responder(spec: t.Any) -> t.Any:
        value = remaining.pop(0) if remaining else values[-1]
        if isinstance(value, BaseException):
            raise value
        return value

    return responder


def rows_by_uurl(report: GracyReport) -> dict[str, GracyRequestRow]:
    return {row.uurl: row for row in report.rows}


@pytest.fixture
async def mix_report() -> GracyReport:
    mock = MockTransport(
        {
            f"GET {A}": script({"n": 1}, {"n": 2}, (404, {"err": "nope"})),
            f"GET {B}": (500, {"err": "boom"}),
            f"GET {C}": script(RuntimeError("wire down")),
            f"GET {FLAKY}": script((503, {"retry": True}), {"ok": True}),
        }
    )
    async with ReportAPI(transport=mock) as api:
        assert await api.get_a() == {"n": 1}
        assert await api.get_a() == {"n": 2}
        with pytest.raises(NonOkResponse):
            await api.get_a()  # third call -> 404
        with pytest.raises(NonOkResponse):
            await api.get_b()  # 500
        with pytest.raises(GracyRequestFailed):
            await api.get_c()  # transport exception
        assert await api.get_flaky() == {"ok": True}  # 503 then 200 (1 retry)
        return api.report()


# --------------------------------------------------------------------------- row contents


async def test_mix_totals_per_uurl(mix_report: GracyReport) -> None:
    rows = rows_by_uurl(mix_report)
    assert set(rows) == {A, B, C, FLAKY}
    assert rows[A].total == 3
    assert rows[B].total == 1
    assert rows[C].total == 1
    assert rows[FLAKY].total == 2  # initial attempt + 1 retry (total counts attempts)


async def test_mix_status_counts(mix_report: GracyReport) -> None:
    rows = rows_by_uurl(mix_report)
    assert rows[A].status_counts == ((200, 2), (404, 1))
    assert rows[B].status_counts == ((500, 1),)
    assert rows[C].status_counts == ((0, 1),)  # REQUEST_ERROR_STATUS bucket
    assert rows[FLAKY].status_counts == ((200, 1), (503, 1))


async def test_aborts_only_on_transport_error_row(mix_report: GracyReport) -> None:
    rows = rows_by_uurl(mix_report)
    assert rows[C].aborts == 1
    assert rows[A].aborts == 0
    assert rows[B].aborts == 0
    assert rows[FLAKY].aborts == 0


async def test_retries_counted_on_retried_row(mix_report: GracyReport) -> None:
    rows = rows_by_uurl(mix_report)
    assert rows[FLAKY].retries == 1
    assert rows[A].retries == 0
    assert rows[B].retries == 0
    assert rows[C].retries == 0


async def test_success_and_failed_rates(mix_report: GracyReport) -> None:
    rows = rows_by_uurl(mix_report)
    assert rows[A].success_rate == pytest.approx(200.0 / 3)  # 2 of 3
    assert rows[A].failed_rate == pytest.approx(100.0 / 3)
    assert rows[B].success_rate == 0.0
    assert rows[B].failed_rate == 100.0
    assert rows[C].success_rate == 0.0  # transport error is a failure
    assert rows[FLAKY].success_rate == pytest.approx(50.0)
    for row in rows.values():
        assert row.success_rate + row.failed_rate == pytest.approx(100.0)


async def test_3xx_counts_as_success() -> None:
    """Documented v2 change: success = 2xx + 3xx (even though the default
    status policy still raises for a 302)."""
    mock = MockTransport({f"GET {B}": (302, {"moved": True})})
    async with ReportAPI(transport=mock) as api:
        with pytest.raises(NonOkResponse):
            await api.get_b()
        report = api.report()
    (row,) = report.rows
    assert row.status_counts == ((302, 1),)
    assert row.success_rate == 100.0
    assert row.failed_rate == 0.0


async def test_latency_columns_populated(mix_report: GracyReport) -> None:
    rows = rows_by_uurl(mix_report)
    for uurl in (A, B, FLAKY):
        row = rows[uurl]
        assert row.max_latency > 0.0
        assert 0.0 < row.avg_latency <= row.max_latency
        assert 0.0 < row.p95_latency <= row.max_latency
        assert 0.0 < row.p99_latency <= row.max_latency


async def test_req_rate_capped_at_total(mix_report: GracyReport) -> None:
    for row in mix_report.rows:
        assert 0.0 < row.req_rate_per_sec <= float(row.total)
    # A single-attempt row deterministically hits the cap: min-window floor
    # would render 1 request as 1000 reqs/s without it.
    rows = rows_by_uurl(mix_report)
    assert rows[C].req_rate_per_sec == pytest.approx(1.0)
    assert rows[B].req_rate_per_sec == pytest.approx(1.0)


# --------------------------------------------------------------------------- TOTAL row


async def test_total_row_sums_and_weights(mix_report: GracyReport) -> None:
    total_row = mix_report.total_row
    rows = mix_report.rows

    assert total_row.uurl == TOTAL_LABEL
    assert total_row.total == sum(r.total for r in rows) == 7
    assert total_row.aborts == 1
    assert total_row.retries == 1
    assert total_row.replays == 0

    merged: dict[int, int] = {}
    for row in rows:
        for code, count in row.status_counts:
            merged[code] = merged.get(code, 0) + count
    assert dict(total_row.status_counts) == merged == {0: 1, 200: 3, 404: 1, 500: 1, 503: 1}

    expected_success = sum(r.success_rate * r.total for r in rows) / total_row.total
    assert total_row.success_rate == pytest.approx(expected_success)
    assert total_row.failed_rate == pytest.approx(100.0 - expected_success)

    assert total_row.max_latency == max(r.max_latency for r in rows)
    expected_avg = sum(r.avg_latency * r.total for r in rows) / total_row.total
    assert total_row.avg_latency == pytest.approx(expected_avg)
    expected_rate = sum(r.req_rate_per_sec * r.total for r in rows) / total_row.total
    assert total_row.req_rate_per_sec == pytest.approx(expected_rate)


async def test_total_row_not_in_rows(mix_report: GracyReport) -> None:
    assert all(row.uurl != TOTAL_LABEL for row in mix_report.rows)


# --------------------------------------------------------------------------- immutability


async def test_print_list_is_pure(
    mix_report: GracyReport, capsys: pytest.CaptureFixture[str]
) -> None:
    """v1 bug regression: printing appended the TOTAL row into the report, so
    printing twice duplicated rows. v2 printers never mutate the report."""
    rows_before = mix_report.rows
    assert isinstance(rows_before, tuple)
    assert len(rows_before) == 4

    mix_report.print("list")
    out1 = capsys.readouterr().out
    mix_report.print("list")
    out2 = capsys.readouterr().out

    assert out1 == out2
    assert out1.count(TOTAL_LABEL) == 1  # TOTAL rendered exactly once per print
    assert mix_report.rows is rows_before
    assert len(mix_report.rows) == 4


async def test_report_is_frozen(mix_report: GracyReport) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        mix_report.rows = ()  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        mix_report.rows[0].total = 999  # type: ignore[misc]


# --------------------------------------------------------------------------- isolation / reset


async def test_per_instance_isolation() -> None:
    mock1 = MockTransport({f"GET {A}": {"ok": 1}})
    mock2 = MockTransport({f"GET {B}": {"ok": 2}})
    async with ReportAPI(transport=mock1) as api1, ReportAPI(transport=mock2) as api2:
        await api1.get_a()
        await api1.get_a()
        await api2.get_b()
        report1 = api1.report()
        report2 = api2.report()

    assert {row.uurl for row in report1.rows} == {A}
    assert {row.uurl for row in report2.rows} == {B}
    assert report1.total_row.total == 2
    assert report2.total_row.total == 1


async def test_reset_metrics_empties_report() -> None:
    mock = MockTransport({f"GET {A}": {"ok": True}})
    async with ReportAPI(transport=mock) as api:
        await api.get_a()
        assert api.report().rows
        api.reset_metrics()
        report = api.report()
    assert report.rows == ()
    assert report.total_row.total == 0
    assert report.total_row.success_rate == 0.0


def test_report_before_build_is_empty() -> None:
    report = ReportAPI().report()  # unstarted client: no runtime state, empty snapshot
    assert report.rows == ()
    assert report.total_row.uurl == TOTAL_LABEL
    assert report.total_row.total == 0


# --------------------------------------------------------------------------- printers


async def test_print_logger(mix_report: GracyReport, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="gracy"):
        mix_report.print("logger")
    records = [r for r in caplog.records if r.name == "gracy"]
    assert len(records) == len(mix_report.rows) + 1  # one line per row + TOTAL
    assert all(r.levelno == logging.INFO for r in records)
    last = records[-1].getMessage()
    assert last.startswith(TOTAL_LABEL)
    assert "total=7" in last
    assert "aborts=1" in last
    assert "retries=1" in last


async def test_print_logger_custom_logger(mix_report: GracyReport, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="my.reports"):
        mix_report.print("logger", logger=logging.getLogger("my.reports"))
    records = [r for r in caplog.records if r.name == "my.reports"]
    assert len(records) == len(mix_report.rows) + 1


async def test_print_rich_smoke(mix_report: GracyReport, capsys: pytest.CaptureFixture[str]) -> None:
    mix_report.print("rich")  # must not raise (rich is installed)
    out = capsys.readouterr().out
    assert "Gracy" in out
    assert mix_report.rows and mix_report.rows[0].uurl != TOTAL_LABEL  # still not mutated


async def test_print_unknown_kind_raises(mix_report: GracyReport) -> None:
    with pytest.raises(ValueError, match="Unknown report printer"):
        mix_report.print("csv")


def _plotly_available() -> bool:
    try:
        import pandas  # noqa: F401
        import plotly  # noqa: F401
    except ImportError:
        return False
    return True


async def test_to_plotly_figure(mix_report: GracyReport) -> None:
    pytest.importorskip("plotly")
    pytest.importorskip("pandas")
    fig = mix_report.to_plotly()
    assert fig is not None
    assert fig.data  # a bar trace of totals per uurl


async def test_to_plotly_missing_extra_is_instructive(mix_report: GracyReport) -> None:
    if _plotly_available():
        pytest.skip("plotly installed; the missing-extra path is not reachable")
    with pytest.raises(ImportError, match=r"pip install gracy\[plotly\]"):
        mix_report.to_plotly()
