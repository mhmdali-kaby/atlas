"""detect_regime on synthetic series where the answer is known (ATL-80)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from atlas_quant import RegimeParams, detect_regime
from atlas_quant.regime import GaussianHMM

CALM = 500
STORM = 500


def _frame(returns: np.ndarray, seed: int = 0, freq: str = "4h") -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1000)
    c = 100.0 * np.exp(np.cumsum(returns))
    o = np.concatenate([[100.0], c[:-1]])
    wick = np.abs(rng.normal(0.0, 0.001, len(c)))
    idx = pd.date_range("2024-01-01", periods=len(c), freq=freq, tz="UTC")
    return pd.DataFrame(
        {"o": o, "h": np.maximum(o, c) * (1 + wick), "l": np.minimum(o, c) * (1 - wick), "c": c},
        index=idx,
    )


def _calm_then_storm(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = np.concatenate([rng.normal(0.0, 0.005, CALM), rng.normal(0.0, 0.02, STORM)])
    return _frame(r, seed)


@pytest.mark.parametrize("n_states", [2, 3])
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_volatility_switch_is_high_vol_and_dated(seed: int, n_states: int) -> None:
    df = _calm_then_storm(seed)
    params = RegimeParams.from_defaults().model_copy(update={"n_states": n_states})
    res = detect_regime(df, params)

    true_days = STORM * 4 / 24  # 500 4H bars since the switch
    assert res.method == "hmm"
    assert res.fallback_reason is None
    assert res.label == "high_vol"
    assert res.probability is not None and res.probability > 0.9
    assert abs(res.days_in_regime - true_days) <= 0.1 * true_days
    assert abs(res.bars_in_regime - STORM) <= 0.1 * STORM
    assert res.n == CALM + STORM and res.n_states == n_states
    assert any(s.label == "high_vol" for s in res.states)


def test_default_params_on_the_acceptance_series() -> None:
    res = detect_regime(_calm_then_storm(0))
    assert res.method == "hmm" and res.label == "high_vol"
    assert abs(res.days_in_regime - STORM * 4 / 24) <= 0.1 * STORM * 4 / 24


def test_deterministic() -> None:
    """Seeded fit: the same frame gives the same answer (floats to BLAS rounding, ~1e-15)."""
    df = _calm_then_storm(3)
    a, b = detect_regime(df), detect_regime(df)
    assert (a.label, a.bars_in_regime, a.regime_start, a.method) == (
        b.label,
        b.bars_in_regime,
        b.regime_start,
        b.method,
    )
    assert a.probability == pytest.approx(b.probability, rel=1e-9)
    for sa, sb in zip(a.states, b.states, strict=True):
        assert sa.label == sb.label and sa.occupancy == sb.occupancy
        assert sa.typical_vol_pct == pytest.approx(sb.typical_vol_pct, rel=1e-9)


def test_calm_after_storm_is_low_vol() -> None:
    rng = np.random.default_rng(7)
    r = np.concatenate([rng.normal(0.0, 0.02, 500), rng.normal(0.0, 0.005, 500)])
    res = detect_regime(_frame(r, 7), RegimeParams.from_defaults().model_copy(update={"n_states": 2}))
    assert res.method == "hmm" and res.label == "low_vol"


def test_strong_drift_is_trend_up() -> None:
    rng = np.random.default_rng(11)
    r = np.concatenate([rng.normal(0.0, 0.01, 500), rng.normal(0.004, 0.01, 500)])
    res = detect_regime(_frame(r, 11), RegimeParams.from_defaults().model_copy(update={"n_states": 2}))
    assert res.method == "hmm"
    assert res.label == "trend_up"


def test_forced_non_convergence_uses_the_fallback() -> None:
    params = RegimeParams.from_defaults().model_copy(update={"hmm_n_iter": 1})
    res = detect_regime(_calm_then_storm(0), params)
    assert res.method == "fallback"
    assert res.fallback_reason is not None and "converge" in res.fallback_reason
    assert res.probability is None and res.states == [] and res.n_states is None
    assert res.label in {"trend_up", "trend_down", "range", "high_vol", "low_vol"}
    assert res.adx is not None and res.vol_percentile is not None


def test_fit_error_uses_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: object, x: object) -> None:
        raise ValueError("degenerate covariance")

    monkeypatch.setattr(GaussianHMM, "fit", boom)
    res = detect_regime(_calm_then_storm(1))
    assert res.method == "fallback"
    assert res.fallback_reason is not None and "degenerate covariance" in res.fallback_reason


def test_short_series_uses_the_fallback() -> None:
    rng = np.random.default_rng(5)
    res = detect_regime(_frame(rng.normal(0.0, 0.01, 120), 5))
    assert res.method == "fallback"
    assert res.fallback_reason is not None and "min_bars" in res.fallback_reason


def test_fallback_sees_a_fresh_storm() -> None:
    """The rule ranks the current vol within the last vol_percentile_lookback bars, so a storm that
    started 40 bars ago is high_vol and dated from where the rolling vol crossed the percentile."""
    rng = np.random.default_rng(2)
    r = np.concatenate([rng.normal(0.0, 0.005, 460), rng.normal(0.0, 0.02, 40)])
    params = RegimeParams.from_defaults().model_copy(update={"min_bars": 10_000})
    res = detect_regime(_frame(r, 2), params)
    assert res.method == "fallback"
    assert res.label == "high_vol"
    assert 30 <= res.bars_in_regime <= 40


def test_too_short_raises() -> None:
    with pytest.raises(ValueError):
        detect_regime(_frame(np.full(10, 0.001)))


def test_naive_index_raises() -> None:
    df = _calm_then_storm(0)
    df.index = df.index.tz_localize(None)
    with pytest.raises(ValueError):
        detect_regime(df)
