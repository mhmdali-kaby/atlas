"""ATL-133: TA structure precompute: zones, volume profile, pattern flags, book walk, no look-ahead."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from atlas_agents.ta._structure import SwingPoint
from atlas_agents.ta.levels import build_zones, prior_period_levels, round_numbers
from atlas_agents.ta.microstructure import spread_bps, summarise_book, walk_book
from atlas_agents.ta.patterns import (
    breakout_retest,
    detect_patterns,
    double_top_bottom,
    failed_breakout,
    flag_pennant,
    trading_range,
)
from atlas_agents.ta.volume import (
    anchored_vwap,
    obv_trend,
    relative_volume,
    session_vwap,
    volume_profile,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

START = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday


def frame(
    closes: Sequence[float],
    *,
    highs: dict[int, float] | None = None,
    lows: dict[int, float] | None = None,
    vols: Sequence[float] | None = None,
    spread: float = 0.1,
    step: timedelta = timedelta(hours=1),
) -> pd.DataFrame:
    c = np.asarray(closes, dtype="float64")
    h, lo = c + spread, c - spread
    for i, v in (highs or {}).items():
        h[i] = v
    for i, v in (lows or {}).items():
        lo[i] = v
    index = pd.DatetimeIndex([START + step * i for i in range(len(c))])
    vol = np.asarray(vols if vols is not None else [100.0] * len(c), dtype="float64")
    return pd.DataFrame({"o": c, "h": h, "l": lo, "c": c, "vol": vol}, index=index)


def ts(i: int, step: timedelta = timedelta(hours=1)) -> datetime:
    return START + step * i


def swing(i: int, price: float, kind: str, lag: int = 2) -> SwingPoint:
    return SwingPoint(ts=ts(i), price=price, kind=kind, confirmed_at=ts(i + lag))  # type: ignore[arg-type]


# -- criterion 1: zones ----------------------------------------------------------------------------


def test_three_close_swing_highs_merge_and_one_atr_away_stays_separate() -> None:
    atr = 10.0
    bars = frame([100.0] * 50)
    swings = [
        swing(10, 150.0, "high"),
        swing(20, 151.0, "high"),
        swing(30, 152.5, "high"),  # 2.5 <= 0.3 x 10 above the zone's low
        swing(40, 162.5, "high"),  # 1 x ATR above the third
    ]
    zones = build_zones(bars, swings, atr, timeframe="1H")
    assert [z.touches for z in zones] == [3, 1]
    first, second = zones
    assert (first.id, first.low, first.high) == ("1H:z0", 150.0, 152.5)
    assert first.mid == pytest.approx(151.1666666667)
    assert first.last_touch == ts(30) and first.bars_since_touch == 19
    assert first.role == "resistance"
    # strength = 0.6 x min(3 / 4, 1) + 0.4 x (1 - 19 / 120)
    assert first.strength == pytest.approx(0.6 * 0.75 + 0.4 * (1 - 19 / 120))
    assert second.low == 162.5 and second.touches == 1


def test_zones_ignore_swings_outside_lookback_and_unconfirmed() -> None:
    bars = frame([100.0] * 200)
    swings = [swing(10, 150.0, "high"), swing(150, 150.5, "high"), swing(198, 150.2, "high", lag=5)]
    zones = build_zones(bars, swings, 10.0)
    assert [z.touches for z in zones] == [1]  # bar 10 is outside the last 120, bar 198 unconfirmed


def test_prior_day_and_week_levels() -> None:
    closes = [100.0 + i for i in range(24 * 9)]  # 9 days of hourly bars from Monday
    levels = prior_period_levels(frame(closes))
    day = levels.prior_day
    week = levels.prior_week
    assert day is not None and week is not None
    assert day.start == START + timedelta(days=7)
    assert (day.high, day.low, day.close) == (100.0 + 191 + 0.1, 100.0 + 168 - 0.1, 100.0 + 191)
    assert week.start == START
    assert (week.high, week.low, week.close) == (100.0 + 167 + 0.1, 100.0 - 0.1, 100.0 + 167)


def test_round_numbers_only_for_stock_and_commodity() -> None:
    assert round_numbers(4062.4, "commodity") == [
        Decimal(3900),
        Decimal(4000),
        Decimal(4100),
        Decimal(4200),
    ]
    assert round_numbers(Decimal("251.3"), "stock") == [
        Decimal(240),
        Decimal(250),
        Decimal(260),
        Decimal(270),
    ]
    assert round_numbers(65000, "crypto") == []


# -- criterion 2: volume profile -------------------------------------------------------------------


def profile_fixture() -> pd.DataFrame:
    # Day 1 (excluded with sessions=1), then day 2: rows [100,101) [101,102) [102,103) [103,104].
    rows = [
        (START, 90.0, 95.0, 92.0, 1000.0),
        (START + timedelta(days=1), 100.0, 104.0, 102.0, 40.0),  # 10 into each row
        (START + timedelta(days=1, hours=1), 101.0, 102.0, 101.5, 30.0),  # row 1
        (START + timedelta(days=1, hours=2), 102.0, 103.0, 102.5, 20.0),  # row 2
        (START + timedelta(days=1, hours=3), 100.0, 101.0, 100.5, 5.0),  # row 0
        (START + timedelta(days=1, hours=4), 103.5, 103.5, 103.5, 5.0),  # high == low: row 3
    ]
    index = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame(
        {
            "o": [r[3] for r in rows],
            "h": [r[2] for r in rows],
            "l": [r[1] for r in rows],
            "c": [r[3] for r in rows],
            "vol": [r[4] for r in rows],
        },
        index=index,
    )


def test_volume_profile_matches_hand_computed_values() -> None:
    profile = volume_profile(profile_fixture(), sessions=1, bins=4)
    assert profile is not None
    assert [r.volume for r in profile.rows] == pytest.approx([15.0, 40.0, 30.0, 15.0])
    # POC = row 1 (40) -> 101.5. Value area: 40, then up (30) beats down (15): 70 of 100 -> stop.
    assert profile.poc == pytest.approx(101.5)
    assert profile.vah == pytest.approx(103.0)
    assert profile.val == pytest.approx(101.0)
    assert profile.total_volume == pytest.approx(100.0)
    assert profile.value_area_pct >= 70.0
    covered = sum(r.volume for r in profile.rows if r.low >= profile.val and r.high <= profile.vah)
    assert covered / profile.total_volume >= 0.70


def test_volume_profile_default_config_covers_seventy_percent() -> None:
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 0.5, 24 * 8))
    profile = volume_profile(frame(closes, vols=rng.uniform(10, 100, len(closes)), spread=0.4))
    assert profile is not None
    assert len(profile.sessions) == 5 and len(profile.rows) == 50
    assert profile.val <= profile.poc <= profile.vah
    assert profile.value_area_volume / profile.total_volume >= 0.70


def test_relative_volume_obv_and_vwaps() -> None:
    vols = [100.0] * 20 + [300.0]
    closes = [100.0 + i for i in range(21)]
    bars = frame(closes, vols=vols)
    assert relative_volume(bars) == pytest.approx(3.0)
    trend = obv_trend(bars)
    assert trend is not None and trend.trend == "up"
    assert trend.obv == pytest.approx(100.0 * 19 + 300.0)
    # session VWAP: all bars are on 5 Jan; typical price == close (h and l symmetric)
    expected = sum(c * v for c, v in zip(closes, vols, strict=True)) / sum(vols)
    assert session_vwap(bars) == pytest.approx(expected)
    anchored = anchored_vwap(bars, [swing(10, 109.9, "low"), swing(15, 115.1, "high")])
    assert anchored is not None and anchored.anchor_ts == ts(15)
    tail = list(zip(closes[15:], vols[15:], strict=True))
    assert anchored.vwap == pytest.approx(sum(c * v for c, v in tail) / sum(v for _, v in tail))


# -- criterion 3: patterns (a positive and a near-miss case each) ----------------------------------

BREAKOUT_CLOSES = [8.0, 8.5, 9.0, 9.5, 9.8, 9.9, 9.5, 9.0, 8.5, 9.2, 9.8, 10.3]
LEVEL_SWING = [swing(5, 10.0, "high", lag=3)]


def test_breakout_retest_positive() -> None:
    bars = frame([*BREAKOUT_CLOSES, 10.6, 10.3, 10.8], highs={5: 10.0}, lows={13: 10.1})
    flag = breakout_retest(bars, LEVEL_SWING, 1.0)
    assert flag.detected and flag.direction == "long" and flag.level == 10.0
    assert flag.details["breakout_ts"] == ts(11) and flag.details["retest_ts"] == ts(13)


def test_breakout_retest_near_miss_close_lost_the_level() -> None:
    bars = frame([*BREAKOUT_CLOSES, 10.6, 9.6, 10.8], highs={5: 10.0})
    assert not breakout_retest(bars, LEVEL_SWING, 1.0).detected


def test_failed_breakout_positive() -> None:
    bars = frame([*BREAKOUT_CLOSES, 9.8], highs={5: 10.0})
    flag = failed_breakout(bars, LEVEL_SWING, 1.0)
    assert flag.detected and flag.direction == "short" and flag.details["failed_ts"] == ts(12)


def test_failed_breakout_near_miss_fell_back_too_late() -> None:
    bars = frame([*BREAKOUT_CLOSES, 10.4, 10.2, 10.1, 9.8], highs={5: 10.0})
    assert not failed_breakout(bars, LEVEL_SWING, 1.0).detected


def range_frame() -> pd.DataFrame:
    return frame([100.0] * 30, highs={5: 101.0, 15: 101.0, 25: 101.0}, lows={10: 99.0, 20: 99.0}, spread=0.5)


def test_range_positive() -> None:
    swings = [
        swing(5, 101.0, "high"),
        swing(10, 99.0, "low"),
        swing(15, 101.0, "high"),
        swing(20, 99.0, "low"),
    ]
    flag = trading_range(range_frame(), swings, 1.0)
    assert flag.detected and flag.direction is None
    assert (flag.details["top"], flag.details["bottom"]) == (101.0, 99.0)


def test_range_near_miss_one_bottom_touch() -> None:
    swings = [
        swing(5, 101.0, "high"),
        swing(10, 99.0, "low"),
        swing(15, 101.0, "high"),
        swing(20, 99.6, "low"),
    ]
    assert not trading_range(range_frame(), swings, 1.0).detected


def test_flag_positive() -> None:
    closes = [100.0] * 5 + [100.0 + 0.5 * i for i in range(1, 9)] + [103.8, 103.6, 103.7, 103.5]
    flag = flag_pennant(frame(closes, spread=0.2), [], 1.0)
    assert flag.detected and flag.direction == "long"
    assert flag.details["pole_move_atr"] >= 3.0
    assert flag.details["shape"] in {"flag", "pennant"}


def test_flag_near_miss_pole_too_short() -> None:
    closes = [100.0] * 5 + [100.0 + 0.25 * i for i in range(1, 9)] + [101.9, 101.8, 101.85, 101.75]
    assert not flag_pennant(frame(closes, spread=0.2), [], 1.0).detected


def double_frame(second_peak: float) -> pd.DataFrame:
    return frame([108.0] * 25, highs={5: 110.0, 15: second_peak}, lows={10: 107.0}, spread=0.5)


def test_double_top_positive() -> None:
    swings = [swing(5, 110.0, "high"), swing(10, 107.0, "low"), swing(15, 110.1, "high")]
    flag = double_top_bottom(double_frame(110.1), swings, 1.0)
    assert flag.detected and flag.direction == "short" and flag.level == 107.0
    assert flag.details["kind"] == "top" and flag.details["confirmed"] is False


def test_double_top_near_miss_peaks_too_far_apart_in_price() -> None:
    swings = [swing(5, 110.0, "high"), swing(10, 107.0, "low"), swing(15, 110.5, "high")]
    assert not double_top_bottom(double_frame(110.5), swings, 1.0).detected


# -- criterion 4: book walk ------------------------------------------------------------------------

BIDS = [["99.9", "50", "0", "3"], ["99.8", "100", "0", "5"]]
ASKS = [["100", "10", "0", "1"], ["100.5", "20", "0", "2"], ["101", "100", "0", "4"]]


def test_walk_book_for_50000_usdt() -> None:
    fill = walk_book(BIDS, ASKS, "buy", 50000, ct_val="10")
    # 10 x 10 x 100 = 10,000; 20 x 10 x 100.5 = 20,100; the last 19,900 at 101.
    qty = Decimal(100) + Decimal(200) + Decimal(19900) / Decimal(101)
    avg = Decimal(50000) / qty
    mid = (Decimal("99.9") + Decimal(100)) / 2
    assert fill.filled and fill.levels_used == 3
    assert fill.base_qty == qty and fill.avg_price == avg
    assert fill.avg_price is not None and round(fill.avg_price, 4) == Decimal("100.5976")
    assert fill.slippage_bps == (avg - mid) / mid * 10000
    assert fill.slippage_bps is not None and round(fill.slippage_bps, 2) == Decimal("64.79")
    assert fill.impact_bps is not None and round(fill.impact_bps, 2) == Decimal("59.76")
    assert round(spread_bps(BIDS, ASKS), 4) == Decimal("10.0050")


def test_walk_book_runs_out_and_imbalance() -> None:
    fill = walk_book(BIDS, ASKS, "sell", 10**7)
    assert not fill.filled and fill.levels_used == 2
    summary = summarise_book(BIDS, ASKS)
    # sizes: bids 150, asks 130 -> (150 - 130) / 280
    assert summary.imbalance[10] == Decimal(20) / Decimal(280)
    assert summary.imbalance[50] == summary.imbalance[10]
    assert summary.spread_bps == spread_bps(BIDS, ASKS)


# -- criterion 5: no look-ahead --------------------------------------------------------------------


@st.composite
def histories(draw: st.DrawFn) -> tuple[pd.DataFrame, list[SwingPoint], int, float]:
    n = draw(st.integers(min_value=40, max_value=160))
    steps = draw(st.lists(st.floats(-1.5, 1.5, allow_nan=False), min_size=n, max_size=n))
    vols = draw(st.lists(st.floats(0.0, 500.0, allow_nan=False), min_size=n, max_size=n))
    closes = 100.0 + np.cumsum(steps)
    bars = frame(closes, vols=vols, spread=0.3, step=timedelta(hours=4))
    picks = draw(st.lists(st.integers(0, n - 1), min_size=0, max_size=25, unique=True))
    swings: list[SwingPoint] = []
    for i in sorted(picks):
        kind = draw(st.sampled_from(["high", "low"]))
        lag = draw(st.integers(0, 6))
        price = float(bars["h"].iloc[i] if kind == "high" else bars["l"].iloc[i])
        swings.append(
            SwingPoint(
                ts=ts(i, timedelta(hours=4)),
                price=price,
                kind=kind,  # type: ignore[arg-type]
                confirmed_at=ts(i + lag, timedelta(hours=4)),
            )
        )
    t = draw(st.integers(min_value=10, max_value=n - 1))
    atr = draw(st.floats(0.2, 3.0))
    return bars, swings, t, atr


def _structure(
    bars: pd.DataFrame, swings: list[SwingPoint], atr: float, at: datetime | None
) -> dict[str, object]:
    return {
        "zones": [z.model_dump() for z in build_zones(bars, swings, atr, at=at)],
        "patterns": {k: v.model_dump() for k, v in detect_patterns(bars, swings, atr, at=at).items()},
        "periods": prior_period_levels(bars, at=at).model_dump(),
        "profile": (p.model_dump() if (p := volume_profile(bars, at=at)) else None),
        "anchored": (a.model_dump() if (a := anchored_vwap(bars, swings, at=at)) else None),
        "session_vwap": session_vwap(bars, at=at),
        "rel_volume": relative_volume(bars, at=at),
    }


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(histories())
def test_structure_at_t_ignores_later_bars_and_swings(
    case: tuple[pd.DataFrame, list[SwingPoint], int, float],
) -> None:
    bars, swings, t, atr = case
    at = bars.index[t].to_pydatetime()
    known_then = [s for s in swings if s.confirmed_at <= at]
    before = _structure(bars.iloc[: t + 1], known_then, atr, None)
    after = _structure(bars, swings, atr, at)
    assert before == after
