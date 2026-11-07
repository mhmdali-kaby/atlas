"""ATL-132 golden test: EMA20, RSI14, MACD, ADX14 and ATR14 on a stored 300-bar fixture, to 1e-8.

The expected values come from fixtures/ta/make_golden.py, an independent plain-Python loop
reference; the test re-runs it so the JSON can never drift from the reference code. A small worked
example (by hand, below) pins the EMA and RSI seeding conventions.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from atlas_agents.ta import indicator_frame
from atlas_agents.ta.indicators import ta_config

FIXTURES = Path(__file__).parent / "fixtures" / "ta"
TOL = 1e-8

COLUMNS = {
    "ema20": "ema20",
    "rsi14": "rsi",
    "macd": "macd",
    "macd_signal": "macd_signal",
    "macd_hist": "macd_hist",
    "atr14": "atr",
    "adx14": "adx",
    "plus_di14": "plus_di",
    "minus_di14": "minus_di",
}


def load_fixture() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "ohlcv_1h_300.csv")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True), name="ts")
    return df


def test_reference_script_reproduces_the_stored_golden_file() -> None:
    before = (FIXTURES / "ohlcv_1h_300_expected.json").read_text()
    subprocess.run([sys.executable, str(FIXTURES / "make_golden.py")], check=True)  # noqa: S603
    assert (FIXTURES / "ohlcv_1h_300_expected.json").read_text() == before


@pytest.mark.parametrize("golden_key", sorted(COLUMNS))
def test_indicator_matches_golden_values(golden_key: str) -> None:
    expected = json.loads((FIXTURES / "ohlcv_1h_300_expected.json").read_text())[golden_key]
    frame = indicator_frame(load_fixture())
    actual = frame[COLUMNS[golden_key]].tolist()
    assert len(actual) == len(expected) == 300
    defined = 0
    for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
        if e is None:
            assert math.isnan(a), f"{golden_key}[{i}] should be undefined, got {a}"
        else:
            defined += 1
            assert a == pytest.approx(e, abs=TOL), f"{golden_key}[{i}]"
    assert defined >= 250  # the comparison covers nearly every bar, not just the tail


def test_worked_example_ema_and_rsi_by_hand() -> None:
    # Closes 10, 11, 12, 11, 13 with EMA span 3 (a = 0.5) and RSI window 2 (a = 0.5):
    # EMA: 10, 10.5, 11.25, 11.125, 12.0625 -> defined from the 3rd bar.
    # changes +1, +1, -1, +2: G = 1, 1, 0.5, 1.25 and L = 0, 0, 0.5, 0.25 (seeded at the first change)
    # RSI from bar 2: L = 0 -> 100; bar 3: 0.5/0.5 -> 50; bar 4: 1.25/0.25 = 5 -> 100 - 100/6.
    cfg = dict(ta_config())
    cfg["ema_spans"] = [3]
    cfg["rsi_window"] = 2
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    c = [10.0, 11.0, 12.0, 11.0, 13.0]
    df = pd.DataFrame({"o": c, "h": c, "l": c, "c": c}, index=idx)
    frame = indicator_frame(df, cfg)
    assert math.isnan(frame["ema3"].iloc[1])
    assert frame["ema3"].iloc[2:].tolist() == pytest.approx([11.25, 11.125, 12.0625], abs=TOL)
    assert math.isnan(frame["rsi"].iloc[1])
    assert frame["rsi"].iloc[2:].tolist() == pytest.approx([100.0, 50.0, 100.0 - 100.0 / 6.0], abs=TOL)
