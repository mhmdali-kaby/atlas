"""ATL-82: backtest_rule stops, funding, RSI, walk-forward, overfit_warning and data_gap."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from atlas_quant import (
    Condition,
    Costs,
    DataQuality,
    GapRange,
    Indicator,
    Metrics,
    RuleSpec,
    backtest_rule,
)
from atlas_quant.backtest import indicator_frame
from pydantic import ValidationError

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parent / "fixtures"

SMA_CROSS = RuleSpec(
    indicators={"sma10": Indicator(kind="sma", window=10), "sma30": Indicator(kind="sma", window=30)},
    entry=[Condition(left="sma10", op="crosses_above", right="sma30")],
    exit=[Condition(left="sma10", op="crosses_below", right="sma30")],
)


def golden_frame() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "backtest_1h.csv")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True), name="ts")
    return df.astype("float64")


def frame(o: list[float], h: list[float], lo: list[float], c: list[float]) -> pd.DataFrame:
    idx = pd.date_range(T0, periods=len(o), freq="1h", name="ts")
    return pd.DataFrame({"o": o, "h": h, "l": lo, "c": c}, index=idx, dtype="float64")


def random_walk(seed: int, n: int = 1200, drift: float = 0.0, sigma: float = 0.004) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(drift, sigma, n)))
    o = np.concatenate(([100.0], c[:-1]))
    idx = pd.date_range(T0, periods=n, freq="1h", name="ts")
    return pd.DataFrame(
        {"o": o, "h": np.maximum(o, c) * 1.001, "l": np.minimum(o, c) * 0.999, "c": c}, index=idx
    )


# Enters once: the close crosses above 100.2 on bar 1, so the buy fills at bar 2's open.
CROSS_LEVEL = [Condition(left="close", op="crosses_above", right=100.2)]
F, S = 0.0005, 0.0002


def _full(rule: RuleSpec, df: pd.DataFrame, costs: Costs | None = None) -> Metrics:
    costs = costs or Costs(taker_fee_rate=F, slippage_bps=2.0)
    return backtest_rule(rule, {"X": df}, None, costs, splits=1).instruments[0].full


def test_long_stop_fills_at_stop_price_with_slippage() -> None:
    df = frame(
        o=[100, 100, 101, 101, 101, 101],
        h=[100.5, 100.6, 101.5, 101.2, 101.2, 101.2],
        lo=[99.5, 99.9, 100.5, 97.0, 100.5, 100.5],
        c=[100, 100.5, 101, 101, 101, 101],
    )
    rule = RuleSpec(entry=CROSS_LEVEL, stop_pct=3.0)
    m = _full(rule, df)
    fill = 101 * (1 + S)
    stop = fill * 0.97
    expected = stop * (1 - S) * (1 - F) / (fill * (1 + F)) - 1
    assert m.n_trades == 1
    assert math.isclose(m.total_return, expected, rel_tol=1e-9)


def test_long_stop_gapped_through_fills_at_open() -> None:
    df = frame(
        o=[100, 100, 101, 95, 95, 95],
        h=[100.5, 100.6, 101.5, 95.5, 95.5, 95.5],
        lo=[99.5, 99.9, 100.5, 94.0, 94.5, 94.5],
        c=[100, 100.5, 101, 95, 95, 95],
    )
    m = _full(RuleSpec(entry=CROSS_LEVEL, stop_pct=3.0), df)
    fill = 101 * (1 + S)
    expected = 95 * (1 - S) * (1 - F) / (fill * (1 + F)) - 1
    assert math.isclose(m.total_return, expected, rel_tol=1e-9)


def test_take_profit_fills_at_target() -> None:
    df = frame(
        o=[100, 100, 101, 101, 101, 101],
        h=[100.5, 100.6, 101.5, 104.0, 101.2, 101.2],
        lo=[99.5, 99.9, 100.5, 100.5, 100.5, 100.5],
        c=[100, 100.5, 101, 101, 101, 101],
    )
    m = _full(RuleSpec(entry=CROSS_LEVEL, stop_pct=3.0, take_profit_pct=2.0), df)
    fill = 101 * (1 + S)
    expected = fill * 1.02 * (1 - S) * (1 - F) / (fill * (1 + F)) - 1
    assert m.n_trades == 1
    assert math.isclose(m.total_return, expected, rel_tol=1e-9)


def test_short_stop_is_above_the_fill() -> None:
    df = frame(
        o=[100, 100, 99, 99, 99, 99],
        h=[100.5, 100.1, 99.5, 103.0, 99.5, 99.5],
        lo=[99.5, 99.4, 98.5, 98.8, 98.5, 98.5],
        c=[100, 99.5, 99, 99, 99, 99],
    )
    rule = RuleSpec(
        side="short",
        entry=[Condition(left="close", op="crosses_below", right=99.8)],
        stop_pct=3.0,
    )
    m = _full(rule, df, Costs(taker_fee_rate=0.0, slippage_bps=0.0))
    stop = 99 * 1.03
    assert m.n_trades == 1
    # Short of 1/99 units: PnL = (99 - stop) / 99 of the starting equity.
    assert math.isclose(m.total_return, (99 - stop) / 99, rel_tol=1e-9)


def test_funding_is_charged_on_open_positions() -> None:
    df = frame(
        o=[100, 100, 101, 102, 103, 104, 105],
        h=[100.5, 100.6, 101.5, 102.5, 103.5, 104.5, 105.5],
        lo=[99.5, 99.9, 100.5, 101.5, 102.5, 103.5, 104.5],
        c=[100, 100.5, 101, 102, 103, 104, 105],
    )
    rule = RuleSpec(entry=CROSS_LEVEL)
    base = _full(rule, df, Costs(taker_fee_rate=0.0, slippage_bps=0.0))
    funding = pd.Series(
        [0.001, 0.002, 0.001],
        index=pd.DatetimeIndex([T0 + timedelta(hours=1), T0 + timedelta(hours=3, minutes=0),
                                T0 + timedelta(hours=4, minutes=30)]),
    )  # fmt: skip
    with_funding = _full(rule, df, Costs(taker_fee_rate=0.0, slippage_bps=0.0, funding={"X": funding}))
    units = 1 / 101  # bought at bar 2's open; flat at hour 1, so that funding is not paid
    paid = units * 102 * 0.002 + units * 103 * 0.001  # bar 3 open, and bar 4 open (04:30 is in its slot)
    assert math.isclose(with_funding.funding_paid, paid, rel_tol=1e-9)
    assert math.isclose(
        with_funding.total_return,
        base.total_return - paid,
        rel_tol=1e-9,
    )


def test_rsi_is_wilder() -> None:
    c = golden_frame()["c"]
    spec = RuleSpec(
        indicators={"rsi": Indicator(kind="rsi", window=14)},
        entry=[Condition(left="rsi", op="below", right=30)],
    )
    rsi = indicator_frame(spec, c)["rsi"]
    closes = c.tolist()
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    # pandas ewm(alpha=1/14, adjust=False) seeds with the first change, then Wilder-smooths.
    g, lo = gains[0], losses[0]
    for i in range(1, len(gains)):
        g = (g * 13 + gains[i]) / 14
        lo = (lo * 13 + losses[i]) / 14
    assert math.isclose(float(rsi.iloc[-1]), 100 - 100 / (1 + g / lo), rel_tol=1e-9)
    assert rsi.iloc[:14].isna().all()


def test_rsi_and_level_rules_trade() -> None:
    rule = RuleSpec(
        indicators={"rsi": Indicator(kind="rsi", window=14), "ema": Indicator(kind="ema", window=20)},
        entry=[Condition(left="rsi", op="below", right=40), Condition(left="close", op="below", right="ema")],
        exit=[Condition(left="rsi", op="above", right=60)],
    )  # fmt: skip
    m = _full(rule, golden_frame())
    assert m.n_trades > 0


def test_unknown_indicator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuleSpec(entry=[Condition(left="sma5", op="above", right="close")])
    with pytest.raises(ValidationError):
        RuleSpec(indicators={"close": Indicator(kind="sma", window=3)},
                 entry=[Condition(left="close", op="above", right=1.0)])  # fmt: skip


def test_walk_forward_blocks_are_anchored() -> None:
    result = backtest_rule(SMA_CROSS, {"X": golden_frame()}, None, Costs(taker_fee_rate=0.0), splits=3)
    inst = result.instruments[0]
    assert [f.fold for f in inst.folds] == [1, 2, 3]
    assert [f.in_sample.bars for f in inst.folds] == [100, 200, 300]
    assert [f.out_of_sample.bars for f in inst.folds] == [100, 100, 100]
    assert all(f.in_sample.start == inst.full.start for f in inst.folds)
    assert inst.folds[0].out_of_sample.start == T0.replace(month=9, day=1) + timedelta(hours=100)


def test_period_bounds_the_bars() -> None:
    start = datetime(2026, 9, 3, tzinfo=UTC)
    end = datetime(2026, 9, 10, tzinfo=UTC)
    result = backtest_rule(SMA_CROSS, {"X": golden_frame()}, (start, end), Costs(taker_fee_rate=0.0))
    full = result.instruments[0].full
    assert full.start == start and full.end == end - timedelta(hours=1)
    assert full.bars == 7 * 24


def test_rule_tuned_to_random_walk_noise_is_flagged_overfit() -> None:
    df = random_walk(4)
    best = None
    for fast in range(2, 21, 2):
        for slow in range(fast + 4, 81, 8):
            rule = RuleSpec(
                indicators={"f": Indicator(kind="sma", window=fast), "s": Indicator(kind="sma", window=slow)},
                entry=[Condition(left="f", op="crosses_above", right="s")],
                exit=[Condition(left="f", op="crosses_below", right="s")],
            )  # fmt: skip
            inst = backtest_rule(rule, {"RW": df}, None, Costs(taker_fee_rate=0.0005), splits=1)
            is_sharpe = inst.instruments[0].is_sharpe_mean
            if is_sharpe is not None and (best is None or is_sharpe > best[0]):
                best = (is_sharpe, inst)
    assert best is not None
    is_sharpe, tuned = best
    assert is_sharpe > 1.0
    assert tuned.overfit_warning is True
    assert tuned.instruments[0].overfit_warning is True


def test_rule_that_works_in_both_halves_is_not_flagged() -> None:
    df = random_walk(7, drift=0.0015, sigma=0.003)
    rule = RuleSpec(
        indicators={"sma50": Indicator(kind="sma", window=50)},
        entry=[Condition(left="close", op="above", right="sma50")],
        exit=[Condition(left="close", op="below", right="sma50")],
    )
    result = backtest_rule(rule, {"UP": df}, None, Costs(taker_fee_rate=0.0005), splits=1)
    inst = result.instruments[0]
    assert inst.is_sharpe_mean is not None and inst.is_sharpe_mean > 0
    assert inst.overfit_warning is False


def test_missing_bar_in_window_sets_data_gap() -> None:
    df = golden_frame()
    holed = df.drop(df.index[150])
    result = backtest_rule(SMA_CROSS, {"X": holed}, None, Costs(taker_fee_rate=0.0))
    assert result.data_gap is True
    assert result.instruments[0].data_gap is True
    assert "2026-09-07T05:00:00+00:00" in result.instruments[0].gap_notes[0]
    clean = backtest_rule(SMA_CROSS, {"X": df}, None, Costs(taker_fee_rate=0.0))
    assert clean.data_gap is False


def _quality(position: str, start: datetime, end: datetime) -> DataQuality:
    return DataQuality(
        inst_id="X", bar="1H", env="demo", window_start=start, window_end=end, as_of=None,
        expected_bars=400, returned_bars=397, missing_bars=3,
        gaps=[GapRange(from_ts=start + timedelta(hours=40), to_ts=start + timedelta(hours=42),
                       missing_bars=3, position=position)],  # type: ignore[arg-type]
        duplicate_count=0, zero_volume_count=0, gap_in_window=True,
        interior_gap=position == "interior",
    )  # fmt: skip


def test_data_quality_interior_gap_sets_data_gap() -> None:
    df = golden_frame()
    start, end = df.index[0].to_pydatetime(), df.index[-1].to_pydatetime() + timedelta(hours=1)
    flagged = backtest_rule(SMA_CROSS, {"X": df}, None, Costs(taker_fee_rate=0.0),
                            quality={"X": _quality("interior", start, end)})  # fmt: skip
    assert flagged.data_gap is True
    leading = backtest_rule(SMA_CROSS, {"X": df}, None, Costs(taker_fee_rate=0.0),
                            quality={"X": _quality("leading", start, end)})  # fmt: skip
    assert leading.data_gap is False
    later = (start + timedelta(hours=100), end)
    outside = backtest_rule(SMA_CROSS, {"X": df}, later, Costs(taker_fee_rate=0.0),
                            quality={"X": _quality("interior", start, end)})  # fmt: skip
    assert outside.data_gap is False


def test_too_few_bars_for_splits_raises() -> None:
    with pytest.raises(ValueError, match="too few"):
        backtest_rule(SMA_CROSS, {"X": golden_frame().iloc[:5]}, None, Costs(taker_fee_rate=0.0), splits=3)
