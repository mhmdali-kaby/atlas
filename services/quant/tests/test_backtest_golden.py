"""ATL-82: backtest_rule's SMA 10/30 crossover against the hand calculation in make_backtest_golden.py."""

from __future__ import annotations

import json
import math
import runpy
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from atlas_quant import Condition, Costs, Indicator, RuleSpec, backtest_rule

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REL = 1e-9

SMA_CROSS = RuleSpec(
    name="sma_10_30_cross",
    indicators={"sma10": Indicator(kind="sma", window=10), "sma30": Indicator(kind="sma", window=30)},
    entry=[Condition(left="sma10", op="crosses_above", right="sma30")],
    exit=[Condition(left="sma10", op="crosses_below", right="sma30")],
)


def golden_frame() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "backtest_1h.csv")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), utc=True), name="ts")
    return df.astype("float64")


def _expected() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "backtest_1h_expected.json").read_text())
    return data


def _run(fee: float, slippage_bps: float) -> Any:
    result = backtest_rule(
        SMA_CROSS,
        {"GOLD-1H": golden_frame()},
        None,
        Costs(taker_fee_rate=fee, slippage_bps=slippage_bps),
        asset_class="crypto",
    )
    return result.instruments[0].full


def test_golden_files_are_reproducible(tmp_path: Path) -> None:
    before = {p.name: p.read_bytes() for p in FIXTURES.glob("backtest_1h_*")}
    runpy.run_path(str(FIXTURES / "make_backtest_golden.py"), run_name="__main__")
    after = {p.name: p.read_bytes() for p in FIXTURES.glob("backtest_1h_*")}
    assert before == after, "make_backtest_golden.py output drifted from the committed golden files"


def test_zero_cost_reproduces_known_result_within_one_percent() -> None:
    exp = _expected()["zero_cost"]
    m = _run(0.0, 0.0)
    assert m.n_trades == exp["n_trades"]
    assert abs(m.n_trades - exp["n_trades"]) <= 0.01 * exp["n_trades"]
    assert m.total_return is not None
    assert abs(m.total_return - exp["total_return"]) <= 0.01 * abs(exp["total_return"])
    # It is in fact the same figure to float precision.
    assert math.isclose(m.total_return, exp["total_return"], rel_tol=REL)
    assert m.sharpe is not None and math.isclose(m.sharpe, exp["sharpe"], rel_tol=1e-6)
    assert math.isclose(m.max_drawdown, exp["max_drawdown"], rel_tol=REL)
    assert m.win_rate == pytest.approx(exp["wins"] / exp["n_trades"], rel=REL)
    assert m.fees_paid == 0.0


def test_costs_give_exactly_the_golden_after_cost_figure() -> None:
    exp = _expected()
    m = _run(0.0005, 2.0)
    assert exp["after_cost"]["fee_rate"] == 0.0005 and exp["after_cost"]["slippage"] == 0.0002
    assert m.n_trades == exp["after_cost"]["n_trades"]
    assert m.total_return is not None
    assert math.isclose(m.total_return, exp["after_cost"]["total_return"], rel_tol=REL)
    assert m.total_return < exp["zero_cost"]["total_return"]
    assert m.fees_paid > 0.0


def test_default_slippage_comes_from_config() -> None:
    exp = _expected()["after_cost"]
    result = backtest_rule(SMA_CROSS, {"GOLD-1H": golden_frame()}, None, Costs(taker_fee_rate=0.0005))
    assert result.slippage_bps == 2.0
    full = result.instruments[0].full
    assert full.total_return is not None
    assert math.isclose(full.total_return, exp["total_return"], rel_tol=REL)
