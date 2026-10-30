"""ATL-104 AC2/AC3: Market Scout feature values against hand calculations, and missing inputs.

The expected values below are computed with plain numpy loops written from the definitions in
``atlas_agents.scout.features``'s docstring, independently of atlas_quant.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from atlas_agents.scout.features import Catalyst, FeatureInputs, compute_features

TOL = 1e-6
D0 = datetime(2025, 6, 1, 16, 0, tzinfo=UTC)  # 1D bars open at 16:00 UTC
N_DAYS = 260
N_HOURS = 800
AS_OF = D0 + timedelta(days=N_DAYS)  # the last daily bar has just closed
H0 = AS_OF - timedelta(hours=N_HOURS)


def _frame(start: datetime, step: timedelta, n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    opens = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(opens, close) * (1 + rng.uniform(0.001, 0.02, n))
    low = np.minimum(opens, close) * (1 - rng.uniform(0.001, 0.02, n))
    vol = rng.uniform(1_000, 5_000, n)
    idx = pd.DatetimeIndex([start + step * i for i in range(n)], name="ts")
    return pd.DataFrame(
        {
            "o": opens,
            "h": high,
            "l": low,
            "c": close,
            "vol": vol,
            "vol_ccy": vol,
            "vol_ccy_quote": vol * close,
        },
        index=idx,
    )


DAILY = _frame(D0, timedelta(days=1), N_DAYS, 11)
HOURLY = _frame(H0, timedelta(hours=1), N_HOURS, 12)


def _wilder_atr_pct(df: pd.DataFrame, bars: int, window: int = 14) -> float:
    d = df.iloc[-bars:]
    h, lo, c = d["h"].tolist(), d["l"].tolist(), d["c"].tolist()
    tr = [h[0] - lo[0]]
    for i in range(1, len(d)):
        tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    atr = sum(tr[:window]) / window
    for t in tr[window:]:
        atr = (atr * (window - 1) + t) / window
    return atr / c[-1] * 100


def _std(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _inputs(**overrides: object) -> FeatureInputs:
    base: dict[str, object] = {
        "inst_id": "GOLD-USDT-SWAP",
        "asset_class": "crypto",
        "daily": DAILY,
        "hourly": HOURLY,
        "funding": [(AS_OF - timedelta(hours=8 * i), 0.0001 * (1 + i % 3)) for i in range(21)],
        "oi": [(AS_OF - timedelta(hours=h), 1_000_000.0 + 1000 * (30 - h)) for h in range(30)],
        "oi_series": "GOLD-USDT-SWAP",
        "long_short": (AS_OF - timedelta(hours=1), 1.25),
        "news": [(AS_OF - timedelta(hours=2), 4), (AS_OF - timedelta(hours=30), 5)],
    }
    base.update(overrides)
    return FeatureInputs(**base)  # type: ignore[arg-type]


def test_feature_values_match_hand_calculation() -> None:
    f = compute_features(_inputs(), AS_OF)
    c = DAILY["c"].tolist()

    for n in (1, 3, 7, 30):
        assert f.returns_pct[f"{n}d"] == pytest.approx((c[-1] / c[-1 - n] - 1) * 100, abs=TOL)

    assert f.atr_pct_1h == pytest.approx(_wilder_atr_pct(HOURLY, 100), abs=TOL)
    assert f.atr_pct_1d == pytest.approx(_wilder_atr_pct(DAILY, 100), abs=TOL)

    hc = HOURLY["c"].tolist()
    log_r = [math.log(hc[i] / hc[i - 1]) for i in range(1, len(hc))]
    assert f.vol_ratio_24h_30d == pytest.approx(_std(log_r[-24:]) / _std(log_r[-720:]), abs=TOL)

    qv = (DAILY["vol_ccy_quote"]).tolist()
    prior = qv[-31:-1]
    expected_z = (qv[-1] - sum(prior) / 30) / _std(prior)
    assert f.volume_z_30d == pytest.approx(expected_z, abs=TOL)

    for n in (20, 50, 200):
        sma = sum(c[-n:]) / n
        assert f.ma_distance_pct[f"{n}d"] == pytest.approx((c[-1] / sma - 1) * 100, abs=TOL)

    prior_bars = DAILY.iloc[-21:-1]
    assert f.breakout_high_20d is bool(c[-1] > prior_bars["h"].max())
    assert f.breakout_low_20d is bool(c[-1] < prior_bars["l"].min())

    # Funding: newest is 0.0001 (i = 0); the 7-day window holds rates with ts > as_of - 7d.
    window = [0.0001 * (1 + i % 3) for i in range(21) if 8 * i < 7 * 24]
    assert f.funding_rate == pytest.approx(0.0001, abs=1e-12)
    assert f.funding_z_7d == pytest.approx((0.0001 - sum(window) / len(window)) / _std(window), abs=TOL)

    # OI: newest (h = 0) 1,030,000 vs the newest row at least 24h older (h = 24) 1,006,000.
    assert f.oi_change_24h_pct == pytest.approx((1_030_000 / 1_006_000 - 1) * 100, abs=TOL)
    assert f.long_short_ratio == 1.25
    assert f.news_count_24h == 1
    assert f.news_max_importance_24h == 4
    assert f.regime_label is not None
    assert f.data_as_of == AS_OF
    assert "returns_pct.30d" not in f.missing


def test_correlations_use_the_last_overlapping_daily_log_returns() -> None:
    ref = _frame(D0, timedelta(days=1), N_DAYS, 99)
    f = compute_features(_inputs(btc_daily=DAILY, spy_daily=ref), AS_OF)
    assert f.corr_btc_30d == pytest.approx(1.0, abs=TOL)
    a = np.diff(np.log(DAILY["c"].to_numpy()))[-30:]
    b = np.diff(np.log(ref["c"].to_numpy()))[-30:]
    assert f.corr_spy_30d == pytest.approx(float(np.corrcoef(a, b)[0, 1]), abs=TOL)


def test_no_funding_history_gives_null_funding_with_a_reason() -> None:
    f = compute_features(_inputs(funding=[], oi=[], long_short=None), AS_OF)
    assert f.funding_rate is None
    assert f.funding_z_7d is None
    assert "no funding history" in f.missing["funding_rate"]
    assert "no funding history" in f.missing["funding_z_7d"]
    assert f.oi_change_24h_pct is None and "oi_change_24h_pct" in f.missing
    assert f.long_short_ratio is None and "long_short_ratio" in f.missing


def test_missing_inputs_are_null_never_zero() -> None:
    f = compute_features(_inputs(daily=DAILY.iloc[:10], hourly=HOURLY.iloc[:0], funding=None, news=[]), AS_OF)
    assert f.returns_pct["30d"] is None and "returns_pct.30d" in f.missing
    assert f.ma_distance_pct["200d"] is None and "ma_distance_pct.200d" in f.missing
    assert f.atr_pct_1h is None and "atr_pct_1h" in f.missing
    assert f.vol_ratio_24h_30d is None and "vol_ratio_24h_30d" in f.missing
    assert f.volume_z_30d is None and "volume_z_30d" in f.missing
    assert f.funding_rate is None and "not a perpetual" in f.missing["funding_rate"]
    assert f.corr_btc_30d is None and "corr_btc_30d" in f.missing
    assert f.news_count_24h == 0
    assert f.news_max_importance_24h is None and "news_max_importance_24h" in f.missing


def test_catalysts_combine_shared_and_own() -> None:
    cpi = Catalyst(
        kind="economic",
        name="CPI",
        at=AS_OF + timedelta(hours=5),
        date=AS_OF.date(),
        importance=5,
        source="x",
    )
    earn = Catalyst(
        kind="earnings", name="AAPL earnings", at=None, date=AS_OF.date(), importance=None, source="y"
    )
    f = compute_features(_inputs(shared_catalysts=[cpi], catalysts=[earn]), AS_OF)
    assert f.catalyst_count_48h == 2
    assert f.catalyst_max_importance_48h == 5
    assert f.catalysts == [earn]
