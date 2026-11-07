"""drawdown_profile against a hand-built 20-bar series with two known drawdowns (ATL-80).

Closes (daily bars from 2026-01-01, index 0..19):

    0:100  1:105  2:110  3:104  4:99   5:102  6:108  7:111  8:115  9:112
    10:103.5 11:92 12:97 13:101 14:109 15:116 16:118 17:118 18:119 19:121

Drawdown A: high 110 at bar 2, trough 99 at bar 4 (99/110 - 1 = -10%), recovered at bar 7 (111 ≥ 110):
5 bars long, 3 bars from trough to recovery.
Drawdown B: high 115 at bar 8, trough 92 at bar 11 (92/115 - 1 = -20%), recovered at bar 15 (116 ≥ 115):
7 bars long, 4 bars from trough to recovery.
Bars 16-17 equal the high (no drawdown), and the series ends at a new high, so the current drawdown is 0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from atlas_quant import drawdown_profile

CLOSES = [100, 105, 110, 104, 99, 102, 108, 111, 115, 112,
          103.5, 92, 97, 101, 109, 116, 118, 118, 119, 121]  # fmt: skip
DAY0 = datetime(2026, 1, 1, tzinfo=UTC)


def _day(i: int) -> datetime:
    return DAY0 + timedelta(days=i)


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(DAY0, periods=len(closes), freq="1D")
    return pd.DataFrame({"c": [float(c) for c in closes]}, index=idx)


def test_golden_two_drawdowns() -> None:
    p = drawdown_profile(_frame(CLOSES))
    assert p.n == 20 and p.n_drawdowns == 2
    assert p.period_start == _day(0) and p.period_end == _day(19)

    b, a = p.top  # deepest first
    assert b.depth == pytest.approx(-0.20, abs=1e-12)
    assert (b.start, b.trough, b.recovery) == (_day(8), _day(11), _day(15))
    assert (b.duration_bars, b.duration_days) == (7, 7.0)
    assert (b.time_to_recover_bars, b.time_to_recover_days) == (4, 4.0)

    assert a.depth == pytest.approx(-0.10, abs=1e-12)
    assert (a.start, a.trough, a.recovery) == (_day(2), _day(4), _day(7))
    assert (a.duration_bars, a.duration_days) == (5, 5.0)
    assert (a.time_to_recover_bars, a.time_to_recover_days) == (3, 3.0)

    assert p.median_time_to_recover_days == 3.5
    assert p.current.depth == 0.0
    assert p.current.peak == _day(19) and p.current.duration_bars == 0


def test_top_n_limits_the_list() -> None:
    p = drawdown_profile(_frame(CLOSES), top_n=1)
    assert p.n_drawdowns == 2 and len(p.top) == 1 and p.top[0].depth == pytest.approx(-0.2)
    assert p.median_time_to_recover_days == 4.0


def test_open_drawdown_and_current() -> None:
    closes = [*CLOSES[:17], 114, 112, 116]  # high 118 at bar 16, never regained
    p = drawdown_profile(_frame(closes))
    assert p.n_drawdowns == 3
    open_ep = next(e for e in p.top if e.recovery is None)
    assert open_ep.start == _day(16) and open_ep.trough == _day(18)
    assert open_ep.depth == pytest.approx(112 / 118 - 1, rel=1e-12)
    assert open_ep.duration_bars == 3 and open_ep.time_to_recover_days is None
    assert p.current.depth == pytest.approx(116 / 118 - 1, rel=1e-12)
    assert p.current.peak == _day(16) and p.current.duration_days == 3.0
    assert p.median_time_to_recover_days == 3.5  # the open one has no recovery time


def test_rising_series_has_no_drawdown() -> None:
    p = drawdown_profile(_frame([1, 2, 3, 4]))
    assert p.top == [] and p.n_drawdowns == 0 and p.median_time_to_recover_days is None
    assert p.current.depth == 0.0


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        drawdown_profile(_frame([]))
