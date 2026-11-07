"""ATL-132: ZigZag swings, divergence and squeeze flags on synthetic series."""

from __future__ import annotations

import itertools

import pandas as pd
import pytest
from atlas_agents.ta import compute, indicator_frame

SPREAD = 0.2  # h = c + SPREAD, l = c - SPREAD


def frame_from_closes(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-09-01", periods=len(closes), freq="1h", tz="UTC")
    opens = [closes[0], *closes[:-1]]
    return pd.DataFrame(
        {
            "o": opens,
            "h": [max(o, c) + SPREAD for o, c in zip(opens, closes, strict=True)],
            "l": [min(o, c) - SPREAD for o, c in zip(opens, closes, strict=True)],
            "c": closes,
        },
        index=idx,
    )


def path(waypoints: list[tuple[int, float]]) -> list[float]:
    """Closes moving linearly between (bar, price) waypoints."""
    out: list[float] = []
    for (b0, p0), (b1, p1) in itertools.pairwise(waypoints):
        step = (p1 - p0) / (b1 - b0)
        out.extend(p0 + step * k for k in range(b1 - b0))
    out.append(waypoints[-1][1])
    return out


def test_zigzag_finds_exactly_the_planted_swings() -> None:
    planted = [(0, 100.0), (20, 120.0), (30, 110.0), (50, 130.0), (62, 118.0), (75, 125.0), (90, 105.0)]
    closes = path([*planted, (100, 112.0)])
    # A shallow 1-bar pullback inside the 30 -> 50 leg: it reverses 1.4 < 1.5 x ATR, so it is no swing.
    closes[41] = closes[40] - 1.0
    _, swings = compute(frame_from_closes(closes))
    assert [s.pos for s in swings] == [bar for bar, _ in planted]
    kinds = ["low", "high"] * 4
    assert [s.kind for s in swings] == kinds[: len(planted)]
    for s, (_, price) in zip(swings, planted, strict=True):
        expected = price + SPREAD if s.kind == "high" else price - SPREAD
        assert s.price == pytest.approx(expected)
    assert [s.label for s in swings] == [None, None, "HL", "HH", "HL", "LH", "LL"]
    assert all(s.confirmed_pos > s.pos for s in swings)


def test_shallow_pullback_is_a_swing_once_it_exceeds_the_threshold() -> None:
    # Same series with a 4-point pullback: now it is deeper than 1.5 x ATR and becomes a swing pair.
    planted = [(0, 100.0), (20, 120.0), (30, 110.0), (50, 130.0), (62, 118.0), (75, 125.0), (90, 105.0)]
    closes = path([*planted, (100, 112.0)])
    closes[41] = closes[40] - 4.0
    _, swings = compute(frame_from_closes(closes))
    assert 40 in [s.pos for s in swings if s.kind == "high"]
    assert 41 in [s.pos for s in swings if s.kind == "low"]


def bullish_divergence_closes() -> list[float]:
    closes = [100.0 + (0.5 if i % 2 else 0.0) + i * 0.25 for i in range(40)]  # warm-up drift up
    closes += path([(0, closes[-1]), (10, 90.0)])[1:]  # fast drop: RSI near its floor
    closes += path([(0, 90.0), (8, 98.0)])[1:]  # rebound
    p = 98.0
    while p > 89.0:  # slow, choppy decline to a lower low: losses mixed with gains, RSI higher
        p = round(p - 0.6, 10)
        closes.append(p)
        if p > 89.0:
            p = round(p + 0.3, 10)
            closes.append(p)
    closes += path([(0, closes[-1]), (6, 95.0)])[1:]  # rebound confirms the second low
    return closes


def test_bullish_divergence_sets_the_flag() -> None:
    closes = bullish_divergence_closes()
    frame, swings = compute(frame_from_closes(closes))
    lows = [s for s in swings if s.kind == "low"]
    first, second = lows[-2], lows[-1]
    assert second.price < first.price  # price: lower low
    assert frame["rsi"].iloc[second.pos] > frame["rsi"].iloc[first.pos]  # RSI: higher low
    assert frame["bullish_divergence"].iloc[-1] is True
    assert frame["bearish_divergence"].iloc[-1] is False
    # Not set before the second low is confirmed.
    assert frame["bullish_divergence"].iloc[second.confirmed_pos - 1] is False


def test_no_divergence_when_rsi_confirms_the_lower_low() -> None:
    closes = bullish_divergence_closes()
    frame = indicator_frame(
        frame_from_closes(closes[:40] + path([(0, closes[39]), (30, 80.0), (40, 90.0)])[1:])
    )
    assert frame["bullish_divergence"].iloc[-1] is False


def test_squeeze_after_constant_volatility_contracts() -> None:
    steady = [100.0 if i % 2 == 0 else 101.0 for i in range(220)]
    contracted = [100.0 if i % 2 == 0 else 100.2 for i in range(10)]
    frame = indicator_frame(frame_from_closes(steady + contracted))
    before = len(steady) - 1
    assert frame["bb_width_percentile"].iloc[before] == pytest.approx(100.0)
    assert frame["squeeze"].iloc[before] is False
    assert frame["bb_width_percentile"].iloc[-1] < 10.0
    assert frame["squeeze"].iloc[-1] is True


def test_squeeze_is_none_until_the_lookback_is_full() -> None:
    frame = indicator_frame(frame_from_closes([100.0 + (i % 3) for i in range(150)]))
    assert frame["squeeze"].iloc[-1] is None


def test_keltner_breakout_and_alignment_on_a_rally() -> None:
    closes = [100.0 + (0.3 if i % 2 else 0.0) for i in range(230)] + [100.0 + 3.0 * k for k in range(1, 6)]
    frame = indicator_frame(frame_from_closes(closes))
    assert frame["keltner_breakout"].iloc[-1] == "up"
    assert frame["alignment"].iloc[-1] == 3.0
    assert frame["ema20_slope_pct"].iloc[-1] > 0
