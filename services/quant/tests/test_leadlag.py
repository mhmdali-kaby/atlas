"""ATL-81: cross_asset_lead_lag."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from atlas_quant import cross_asset_lead_lag

START = datetime(2026, 1, 1, tzinfo=UTC)


def _walk(seed: int, n: int = 500) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex([START + timedelta(hours=i) for i in range(n)])
    return pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)


def test_b_is_a_shifted_by_three_bars() -> None:
    a = _walk(1)
    b = a.shift(3).dropna()  # b repeats a's moves three bars later
    res = cross_asset_lead_lag(a, b, max_lag=6)
    assert res.best_lag == 3
    assert res.best_corr is not None and res.best_corr > 0.99
    assert res.leader == "a"
    assert [lc.lag for lc in res.lags] == list(range(-6, 7))
    assert res.corr_lag0 is not None and abs(res.corr_lag0) < 0.2


def test_negative_lag_when_b_leads() -> None:
    a = _walk(2)
    res = cross_asset_lead_lag(a.shift(2).dropna(), a, max_lag=4)
    assert res.best_lag == -2 and res.leader == "b"
    assert res.best_corr is not None and res.best_corr > 0.99


def test_same_series_is_lag_zero_and_accepts_frames() -> None:
    a = _walk(3)
    res = cross_asset_lead_lag(pd.DataFrame({"c": a}), a, max_lag=2)
    assert res.best_lag == 0 and res.leader == "none"
    assert res.corr_lag0 == pytest.approx(1.0)


def test_unrelated_series_have_low_correlation() -> None:
    res = cross_asset_lead_lag(_walk(4), _walk(5), max_lag=3)
    assert res.best_corr is not None and abs(res.best_corr) < 0.2


def test_too_short_gives_none() -> None:
    a = _walk(6, n=3)
    res = cross_asset_lead_lag(a, a, max_lag=5)
    assert res.best_lag is None and res.leader is None
    assert all(lc.corr is None for lc in res.lags)


def test_rejects_negative_max_lag_and_naive_index() -> None:
    a = _walk(7)
    with pytest.raises(ValueError):
        cross_asset_lead_lag(a, a, max_lag=-1)
    with pytest.raises(ValueError):
        cross_asset_lead_lag(a.tz_localize(None), a, max_lag=1)
