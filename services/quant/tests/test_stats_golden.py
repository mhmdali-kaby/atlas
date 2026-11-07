"""ATL-79: compute_stats against the hand calculation in fixtures/make_golden.py (1e-9 relative)."""

from __future__ import annotations

import json
import math
import runpy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from atlas_quant import compute_stats
from atlas_quant.stats import StatsResult, periods_per_year

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REL = 1e-9


def _frame() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "golden_4h.csv")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True), name="ts")
    return df.astype("float64")


def _expected() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "golden_4h_expected.json").read_text())
    return data


def _ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def close(actual: float | None, expected: float) -> bool:
    return actual is not None and math.isclose(actual, expected, rel_tol=REL, abs_tol=0.0)


@pytest.fixture(scope="module")
def result() -> StatsResult:
    return compute_stats(_frame(), "4H", asset_class="crypto")


def test_fixture_files_are_what_the_hand_calculation_produces() -> None:
    build = runpy.run_path(str(FIXTURES / "make_golden.py"))["build"]
    worked, expected_json = build()
    assert worked == (FIXTURES / "golden_4h_worked.csv").read_text()
    assert expected_json == (FIXTURES / "golden_4h_expected.json").read_text()


def test_sample_and_period(result: StatsResult) -> None:
    exp = _expected()
    assert result.n == exp["n"] == 28
    assert result.period_start == _ts("2026-10-26T00:00:00Z")
    assert result.period_end == _ts("2026-10-30T12:00:00Z")
    assert result.periods_per_year == exp["periods_per_year"] == 2190.0


@pytest.mark.parametrize("kind", ["log_returns", "simple_returns"])
def test_returns_summaries(result: StatsResult, kind: str) -> None:
    exp = _expected()[kind]
    got = getattr(result, kind)
    assert got.count == exp["count"] == 27
    for key in ("mean", "std", "min", "max", "total"):
        assert close(getattr(got, key), exp[key]), (kind, key, getattr(got, key), exp[key])


@pytest.mark.parametrize(
    "field",
    [
        "realised_vol",
        "parkinson_vol",
        "garman_klass_vol",
        "atr",
        "atr_pct",
        "skew",
        "excess_kurtosis",
        "adr_pct",
    ],
)
def test_scalar_statistics(result: StatsResult, field: str) -> None:
    exp = _expected()[field]
    assert close(getattr(result, field), exp), (field, getattr(result, field), exp)


def test_atr_is_wilder_14(result: StatsResult) -> None:
    assert result.atr_window == 14


def test_max_drawdown(result: StatsResult) -> None:
    exp = _expected()["drawdown"]
    assert close(result.drawdown.max_drawdown, exp["max_drawdown"])
    assert result.drawdown.peak_ts == _ts(exp["peak_ts"])
    assert result.drawdown.trough_ts == _ts(exp["trough_ts"])
    # 104.00 (Tue 00:00) down to 96.80 (Wed 04:00): 96.8 / 104 - 1.
    assert close(result.drawdown.max_drawdown, 96.8 / 104.0 - 1.0)


def test_adr_uses_complete_days_only(result: StatsResult) -> None:
    # Mon..Thu have all six 4H bars; Fri 30 Oct has four and is left out.
    assert result.adr_days == _expected()["adr_days"] == 4


@pytest.mark.parametrize("table", ["hourly_utc", "hourly_local"])
def test_hour_tables(result: StatsResult, table: str) -> None:
    exp = _expected()[table]
    got = getattr(result, table)
    assert [b.hour for b in got] == [e["hour"] for e in exp]
    for b, e in zip(got, exp, strict=True):
        assert b.n == e["n"]
        assert close(b.mean_log_return, e["mean_log_return"]), (table, b.hour)
        assert close(b.std_log_return, e["std_log_return"]), (table, b.hour)


def test_local_hours_are_baghdad(result: StatsResult) -> None:
    assert result.hour_timezone == "Asia/Baghdad"
    assert [b.hour for b in result.hourly_local] == [3, 7, 11, 15, 19, 23]


def test_weekday_table(result: StatsResult) -> None:
    exp = _expected()["weekday"]
    assert [(w.weekday, w.name, w.n) for w in result.weekday] == [
        (e["weekday"], ("Mon", "Tue", "Wed", "Thu", "Fri")[e["weekday"]], e["n"]) for e in exp
    ]
    for w, e in zip(result.weekday, exp, strict=True):
        assert close(w.mean_return, e["mean_return"]), w.weekday


def test_stock_annualisation_uses_252_days() -> None:
    crypto = compute_stats(_frame(), "4H", asset_class="crypto")
    stock = compute_stats(_frame(), "4H", asset_class="stock")
    assert stock.periods_per_year == periods_per_year("4H", "commodity") == 252 * 6
    assert crypto.realised_vol is not None and stock.realised_vol is not None
    assert close(stock.realised_vol / crypto.realised_vol, math.sqrt(252 / 365))


def test_unordered_input_is_sorted_and_naive_index_refused() -> None:
    df = _frame()
    assert compute_stats(df.iloc[::-1], "4H") == compute_stats(df, "4H")
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_stats(df.tz_localize(None), "4H")


def test_short_frames_degrade_to_none() -> None:
    small = compute_stats(_frame().iloc[:3], "4H")
    assert small.atr is None and small.excess_kurtosis is None and small.adr_pct is None
    assert small.skew is None  # two returns
    assert small.realised_vol is not None
    empty = compute_stats(_frame().iloc[:0], "4H")
    assert empty.n == 0 and empty.period_start is None and empty.log_returns.count == 0
    assert empty.drawdown.max_drawdown == 0.0
