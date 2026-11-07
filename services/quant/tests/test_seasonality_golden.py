"""ATL-81: seasonality against the hand calculation in fixtures/make_seasonality_golden.py."""

from __future__ import annotations

import json
import math
import runpy
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from atlas_quant import seasonality
from atlas_quant.seasonality import SeasonBucket

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REL = 1e-9


def _frame() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "seasonality_1h.csv")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True), name="ts")
    return df.astype("float64")


def _expected() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "seasonality_1h_expected.json").read_text())
    return data


def _check(buckets: list[SeasonBucket], expected: dict[str, Any]) -> None:
    got = {("outside" if b.key is None else str(b.key)): b for b in buckets}
    assert set(got) == set(expected)
    for key, exp in expected.items():
        b = got[key]
        assert b.n == exp["n"], key
        assert math.isclose(b.mean_log_return, exp["mean"], rel_tol=REL), key
        assert math.isclose(b.median_log_return, exp["median"], rel_tol=REL), key
        if exp["t_stat"] is None:
            assert b.t_stat is None, key
        else:
            assert b.t_stat is not None and math.isclose(b.t_stat, exp["t_stat"], rel_tol=REL), key


def test_fixture_files_are_what_the_hand_calculation_produces() -> None:
    build = runpy.run_path(str(FIXTURES / "make_seasonality_golden.py"))["build"]
    bars, expected = build()
    assert bars == (FIXTURES / "seasonality_1h.csv").read_text()
    assert expected == (FIXTURES / "seasonality_1h_expected.json").read_text()


def test_hour_buckets_in_utc_and_baghdad() -> None:
    res = seasonality(_frame(), "hour")
    exp = _expected()
    assert res.n == exp["n"] == 59
    assert res.timezone == "UTC" and res.local_timezone == "Asia/Baghdad"
    _check(res.buckets, exp["utc"])
    assert res.local_buckets is not None
    _check(res.local_buckets, exp["baghdad"])
    # Baghdad is UTC+3: the UTC 21:00 bucket is the Baghdad 00:00 bucket.
    utc = {b.key: b for b in res.buckets}
    local = {b.key: b for b in res.local_buckets}
    for h in range(24):
        assert (
            utc[h].model_copy(update={"key": (h + 3) % 24, "label": local[(h + 3) % 24].label})
            == local[(h + 3) % 24]
        )
    assert [b.label for b in res.buckets][:2] == ["00:00", "01:00"]


def test_weekday_means() -> None:
    res = seasonality(_frame(), "weekday")
    assert res.local_buckets is None
    _check(res.buckets, _expected()["weekday"])
    assert [(b.key, b.label) for b in res.buckets] == [(0, "Mon"), (1, "Tue"), (5, "Sat"), (6, "Sun")]


def test_funding_buckets() -> None:
    res = seasonality(_frame(), "funding")
    _check(res.buckets, _expected()["funding"])
    assert [b.label for b in res.buckets] == ["-2h", "-1h", "+0h", "+1h", "outside"]


def test_month_buckets() -> None:
    res = seasonality(_frame(), "month")
    assert [(b.key, b.label, b.n) for b in res.buckets] == [(10, "Oct", 59)]


def test_unknown_grouping_is_refused() -> None:
    with pytest.raises(ValueError):
        seasonality(_frame(), "quarter")  # type: ignore[arg-type]
