"""ATL-82: event_study on synthetic prices with a planted +2 % jump 1h after each event."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from atlas_quant import EventStudyResult, OffsetStat, compute_stats, event_study

T0 = datetime(2026, 1, 1, tzinfo=UTC)
N_EVENTS = 26
SPACING_H = 120  # events 5 days apart, so windows of -24h..+72h never overlap
FIRST_EVENT_H = 48


def planted(seed: int = 82) -> tuple[pd.DataFrame, list[datetime]]:
    """Hourly bars, tiny noise; the bar opening at each event time closes 2 % above the previous close."""
    n = FIRST_EVENT_H + SPACING_H * N_EVENTS + 24
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 0.0002, n)
    events = [T0 + timedelta(hours=FIRST_EVENT_H + SPACING_H * i) for i in range(N_EVENTS)]
    for e in events:
        r[int((e - T0).total_seconds() // 3600)] += math.log(1.02)
    c = 100.0 * np.exp(np.cumsum(r))
    o = np.concatenate(([100.0], c[:-1]))
    idx = pd.date_range(T0, periods=n, freq="1h", name="ts")
    df = pd.DataFrame(
        {"o": o, "h": np.maximum(o, c) * 1.0005, "l": np.minimum(o, c) * 0.9995, "c": c}, index=idx
    )
    return df, events


def _offset(result: EventStudyResult, hours: float) -> OffsetStat:
    return next(o for o in result.offsets if o.offset_hours == hours)


def test_planted_jump_and_dropped_window() -> None:
    df, events = planted()
    # Remove one bar inside the last event's window: that event is dropped and counted.
    hole = events[-1] + timedelta(hours=10)
    holed = df.drop(pd.Timestamp(hole))
    result = event_study(holed, events)

    assert result.bar == "1H"
    assert result.window_hours == (-24.0, 72.0)
    assert result.n_events == N_EVENTS
    assert result.n_cases == 25
    assert result.dropped_missing_bars == 1
    assert result.dropped_out_of_range == 0

    plus1 = _offset(result, 1.0)
    assert plus1.n == 25
    assert plus1.median == pytest.approx(0.02, abs=0.001)
    assert plus1.p10 < plus1.median < plus1.p90
    zero = _offset(result, 0.0)
    assert zero.median == 0.0
    before = _offset(result, -24.0)
    assert abs(before.median) < 0.005
    after = _offset(result, 72.0)
    assert after.median == pytest.approx(0.02, abs=0.005)
    assert len(result.offsets) == 97  # -24..+72 in 1h steps

    horizons = {m.horizon_hours: m for m in result.atr_moves}
    assert sorted(horizons) == [1.0, 4.0, 24.0]
    assert all(m.n == 25 for m in result.atr_moves)
    assert horizons[1.0].mean_abs_move_atr is not None and horizons[1.0].mean_abs_move_atr > 1.0


def test_atr_move_matches_compute_stats_atr() -> None:
    df, events = planted()
    e = events[3]
    result = event_study(df, [e])
    anchor = e - timedelta(hours=1)  # the last bar closed by the event time
    atr = compute_stats(df.loc[:anchor], "1H").atr
    assert atr is not None
    p0 = float(df.loc[anchor, "c"])
    p1 = float(df.loc[e, "c"])  # closed at e + 1h
    move = next(m for m in result.atr_moves if m.horizon_hours == 1.0)
    assert move.mean_abs_move_atr == pytest.approx(abs(p1 - p0) / atr, rel=1e-12)


def test_event_inside_a_bar_uses_only_closed_bars() -> None:
    df, events = planted()
    # 30 min after the jump bar opened: that bar has not closed yet, so the +0h price is the
    # pre-jump close and the +1h price (still inside the jump bar's successor) shows the jump.
    e = events[0] + timedelta(minutes=30)
    result = event_study(df, [e])
    assert _offset(result, 0.0).median == 0.0
    assert _offset(result, 1.0).median == pytest.approx(0.02, abs=0.001)


def test_asset_class_dict_pools_cases() -> None:
    a, events = planted(1)
    b, _ = planted(2)
    result = event_study({"AAA-USDT-SWAP": a, "BBB-USDT-SWAP": b}, events)
    assert result.inst_ids == ["AAA-USDT-SWAP", "BBB-USDT-SWAP"]
    assert result.n_cases == 2 * N_EVENTS
    assert _offset(result, 1.0).n == 2 * N_EVENTS


def test_events_outside_data_and_short_history_are_counted() -> None:
    df, events = planted()
    late = df.index[-1].to_pydatetime() + timedelta(days=10)
    result = event_study(df, [events[0], late])
    assert result.n_cases == 1
    assert result.dropped_out_of_range == 1
    early = event_study(df, [T0 + timedelta(hours=10)], window=(timedelta(hours=-4), timedelta(hours=4)))
    assert early.dropped_short_history == 1  # only 10 bars closed before the event, ATR needs 14
    assert early.n_cases == 0
    assert all(o.n == 0 and o.median is None for o in early.offsets)


def test_naive_event_time_is_rejected() -> None:
    df, _ = planted()
    with pytest.raises(ValueError, match="timezone-aware"):
        event_study(df, [datetime(2026, 1, 3)])  # noqa: DTZ001 - naive on purpose
