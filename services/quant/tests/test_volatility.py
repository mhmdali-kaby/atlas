"""volatility_forecast on simulated GARCH(1,1) data with known parameters (ATL-80)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from atlas_quant import VolParams, volatility_forecast

OMEGA, ALPHA, BETA = 0.02, 0.08, 0.90  # per-bar variance in %², 1H bars


def _simulate(seed: int, n: int = 3000) -> tuple[pd.DataFrame, float]:
    """A 1H GARCH(1,1) path in % log returns, and the true conditional 24h sigma at its end."""
    rng = np.random.default_rng(seed)
    long_run = OMEGA / (1 - ALPHA - BETA)
    var = long_run
    r = np.empty(n)
    for t in range(n):
        r[t] = math.sqrt(var) * rng.standard_normal()
        var = OMEGA + ALPHA * r[t] ** 2 + BETA * var  # ends as sigma²_{T+1}
    true_var = sum(long_run + (ALPHA + BETA) ** k * (var - long_run) for k in range(24))
    c = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(r / 100.0)]))
    idx = pd.date_range("2025-01-01", periods=n + 1, freq="1h", tz="UTC")
    df = pd.DataFrame({"o": c, "h": c * 1.002, "l": c * 0.998, "c": c}, index=idx)
    return df, math.sqrt(true_var)


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_garch_recovers_the_true_24h_sigma(seed: int) -> None:
    df, true_sigma = _simulate(seed)
    f = volatility_forecast(df, 24)
    assert f.method == "garch" and f.fallback_reason is None
    assert f.horizon_bars == 24 and f.bar_hours == pytest.approx(1.0)
    assert abs(f.sigma_pct / true_sigma - 1) <= 0.15
    assert abs(f.params["alpha"] + f.params["beta"] - (ALPHA + BETA)) < 0.05


def test_ten_bars_use_ewma() -> None:
    df, _ = _simulate(0)
    f = volatility_forecast(df.iloc[:10], 24)
    assert f.method == "ewma"
    assert f.fallback_reason is not None and "garch_min_bars" in f.fallback_reason
    assert f.params == {"lambda": pytest.approx(0.94)}


def test_ewma_hand_calculation() -> None:
    closes = [100.0, 101.0, 99.0, 102.0, 100.5]
    idx = pd.date_range("2025-01-01", periods=5, freq="4h", tz="UTC")
    df = pd.DataFrame({"o": closes, "h": closes, "l": closes, "c": closes}, index=idx)
    r = [100 * math.log(closes[i] / closes[i - 1]) for i in range(1, 5)]
    lam = 0.94
    v = r[0] ** 2
    for x in r[:-1]:
        v = lam * v + (1 - lam) * x**2
    nxt = lam * v + (1 - lam) * r[-1] ** 2
    f = volatility_forecast(df, 24)  # 6 bars of 4H
    assert f.method == "ewma" and f.horizon_bars == 6
    assert f.sigma_next_bar_pct == pytest.approx(math.sqrt(nxt), rel=1e-12)
    assert f.sigma_pct == pytest.approx(math.sqrt(6 * nxt), rel=1e-12)
    assert f.sigma_atr is None  # shorter than the ATR window


def test_non_convergence_falls_back_to_ewma() -> None:
    df, _ = _simulate(1)
    f = volatility_forecast(df, 24, VolParams.from_defaults().model_copy(update={"max_iter": 1}))
    assert f.method == "ewma"
    assert f.fallback_reason is not None and "converge" in f.fallback_reason


def test_sigma_in_atr_units() -> None:
    df, _ = _simulate(2)
    f = volatility_forecast(df, 24)
    assert f.atr is not None and f.sigma_atr is not None
    assert f.sigma_atr == pytest.approx(f.sigma_pct / 100 * f.last_close / f.atr, rel=1e-12)


def test_partial_horizon_rounds_up() -> None:
    df, _ = _simulate(3)
    f = volatility_forecast(df.resample("4h").last().dropna(), 10)
    assert f.horizon_bars == 3


def test_zero_mean_option() -> None:
    df, true_sigma = _simulate(4)
    f = volatility_forecast(df, 24, VolParams.from_defaults().model_copy(update={"garch_mean": "zero"}))
    assert f.method == "garch" and f.params["mu"] == 0.0
    assert abs(f.sigma_pct / true_sigma - 1) <= 0.15


def test_bad_inputs_raise() -> None:
    df, _ = _simulate(0)
    with pytest.raises(ValueError):
        volatility_forecast(df, 0)
    with pytest.raises(ValueError):
        volatility_forecast(df.iloc[:2], 24)
