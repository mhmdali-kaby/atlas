"""ATL-132: ta.table over fixture frames for all five timeframes, and look-ahead safety."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from atlas_agents import ta
from atlas_agents.ta import compute, indicator_frame
from atlas_agents.ta.table import MOMENTUM_KEYS, TREND_KEYS, VOLATILITY_KEYS
from atlas_quant.grid import bar_delta
from hypothesis import given, settings
from hypothesis import strategies as st

END = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # close time of every frame's last bar
TIMEFRAMES = ("1D", "4H", "1H", "15m", "5m")


def random_walk(bar: str, n: int, seed: int, end: datetime = END) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    step = bar_delta(bar)
    idx = pd.DatetimeIndex([end - step * (n - i) for i in range(n)], tz=UTC, name="ts")
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    o = np.concatenate(([100.0], c[:-1]))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0.0, 0.003, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0.0, 0.003, n)))
    return pd.DataFrame({"o": o, "h": h, "l": lo, "c": c, "vol": rng.uniform(100, 200, n)}, index=idx)


def test_table_returns_every_key_for_all_five_timeframes() -> None:
    frames = {bar: random_walk(bar, 300, seed) for seed, bar in enumerate(TIMEFRAMES)}
    result = ta.table("BTC-USDT-SWAP", frames)
    assert result["inst_id"] == "BTC-USDT-SWAP"
    assert result["data_as_of"] == END
    assert list(result["timeframes"]) == list(TIMEFRAMES)
    trend_keys = {f"ema{s}" for s in (20, 50, 200)} | {f"ema{s}_slope_pct" for s in (20, 50, 200)}
    trend_keys |= set(TREND_KEYS) | {"swings"}
    for bar in TIMEFRAMES:
        tf = result["timeframes"][bar]
        assert tf["bar"] == bar and tf["bars"] == 300 and tf["note"] is None
        assert tf["as_of"] == END
        assert set(tf["trend"]) == trend_keys
        assert set(tf["momentum"]) == set(MOMENTUM_KEYS)
        assert set(tf["volatility"]) == set(VOLATILITY_KEYS)
        # 300 bars define every numeric value (EMA200, the 180-bar width percentile, ADX...).
        for group in ("trend", "momentum", "volatility"):
            for key, value in tf[group].items():
                if key in {"swing_high_label", "swing_low_label"}:
                    continue
                assert value is not None, f"{bar} {group}.{key}"
        assert -3 <= tf["trend"]["alignment"] <= 3
        assert tf["volatility"]["keltner_breakout"] in {"up", "down", "none"}
        assert isinstance(tf["momentum"]["bullish_divergence"], bool)
        assert isinstance(tf["volatility"]["squeeze"], bool)
        assert tf["trend"]["swings"], f"{bar}: a 300-bar random walk has swings"
        assert len(tf["trend"]["swings"]) <= 10
        assert tf["last_close"] == pytest.approx(float(frames[bar]["c"].iloc[-1]))


def test_data_as_of_is_the_latest_last_bar_close() -> None:
    frames = {
        "1H": random_walk("1H", 260, 1, end=datetime(2026, 10, 30, 10, tzinfo=UTC)),
        "5m": random_walk("5m", 260, 2, end=datetime(2026, 10, 30, 11, 55, tzinfo=UTC)),
    }
    result = ta.table("XAU-USDT-SWAP", frames)
    assert result["data_as_of"] == datetime(2026, 10, 30, 11, 55, tzinfo=UTC)
    assert result["timeframes"]["1H"]["as_of"] == datetime(2026, 10, 30, 10, tzinfo=UTC)
    missing = result["timeframes"]["1D"]
    assert missing["bars"] == 0 and missing["note"] == "no frame given" and missing["as_of"] is None
    assert missing["momentum"]["rsi"] is None and missing["trend"]["swings"] == []


def test_naive_index_is_refused() -> None:
    df = random_walk("1H", 50, 3)
    df.index = df.index.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        ta.table("BTC-USDT-SWAP", {"1H": df})


@settings(max_examples=40, deadline=None)
@given(
    returns=st.lists(st.floats(-0.04, 0.04, allow_nan=False), min_size=30, max_size=260),
    wicks=st.floats(0.0, 0.01),
    cut=st.floats(0.05, 0.95),
)
def test_appending_bars_never_changes_earlier_values(returns: list[float], wicks: float, cut: float) -> None:
    n = len(returns)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    c = 100.0 * np.exp(np.cumsum(returns))
    o = np.concatenate(([100.0], c[:-1]))
    up = np.array([wicks * ((i * 7) % 5) / 4 for i in range(n)])
    df = pd.DataFrame(
        {"o": o, "h": np.maximum(o, c) * (1 + up), "l": np.minimum(o, c) * (1 - up[::-1]), "c": c}, index=idx
    )
    k = max(1, min(n - 1, int(n * cut)))
    full, full_swings = compute(df)
    prefix, prefix_swings = compute(df.iloc[:k])
    pd.testing.assert_frame_equal(full.iloc[:k], prefix, check_exact=True)
    assert [s for s in full_swings if s.confirmed_pos < k] == prefix_swings


def test_indicator_frame_has_one_row_per_bar() -> None:
    df = random_walk("4H", 120, 9)
    assert len(indicator_frame(df)) == 120
