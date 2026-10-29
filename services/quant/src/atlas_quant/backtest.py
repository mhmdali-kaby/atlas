"""Rule backtests with costs and walk-forward splits (ATL-82, brief §7.3 ``backtest_rule``).

The fills are simulated by vectorbt (``Portfolio.from_signals``); this module builds the signals from a
small typed rule, adds funding, and computes the metrics from the resulting equity curve. Semantics,
pinned by ``quant/tests/test_backtest_golden.py`` and its independent loop reference
(``fixtures/make_backtest_golden.py``):

- Indicators are computed on the whole frame (they only look back, so bars before the period are
  warm-up). A condition is evaluated on bar t's close and its order fills at bar t+1's **open**; a
  signal on the last bar of a run is dropped. Entry = all entry conditions true; exit = any exit
  condition true (or a stop / take-profit). Entry and exit on the same bar cancel out (vectorbt's
  default conflict rule). One position at a time, the whole equity in it, no leverage.
- Costs: every fill pays ``slippage_bps`` against us (buy at price x (1 + s), sell at price x (1 - s))
  and the taker fee on its value. A buy of cash C gets C / (p (1 + s) (1 + f)) units.
- Stops are percent of the fill price (vectorbt ``stop_entry_price="fillprice"``), checked from the bar
  after the entry on high/low, and filled at the stop price with slippage (``stopmarket``), or at the
  open when the bar gaps through it. A stop and a take-profit hit in the same bar resolve as the stop.
- Any position still open on the last bar of a run is closed at that bar's close (with slippage and
  fee), so every trade in the report is closed.
- Funding (perps, when a funding series is given): at each funding time t inside the run, the bar
  opening at ``slot(bar, t)`` pays ``units x open x rate`` (signed: longs pay a positive rate, shorts
  receive it). It is subtracted from the equity curve and from the trade it falls in. It is not
  reinvested into the sizing of later trades (vectorbt cannot charge it mid-simulation), which slightly
  overstates the size of trades after funding was paid.
- Metrics on the bar equity curve E (starting at 1.0): total return E_end / E_0 - 1; CAGR with
  ``periods_per_year(bar, asset_class)`` bars a year over n - 1 bar returns; Sharpe = mean / std
  (ddof 1) x sqrt(P); Sortino = mean / sqrt(mean(min(r, 0)²)) x sqrt(P); max drawdown on E (≤ 0); win
  rate and profit factor over trade PnL after fees and funding.
- Walk-forward: the period's bars are cut into ``splits + 1`` equal blocks (numpy ``array_split``).
  Fold j (1..splits) is in-sample on blocks 0..j-1 and out-of-sample on block j; each run starts flat
  with fresh equity. ``overfit_warning`` is true when the mean IS Sharpe is positive and the mean OOS
  Sharpe is below ``quant.backtest.overfit_sharpe_fraction`` of it (a flat OOS run, Sharpe None,
  counts as 0).
- ``data_gap`` is true when the run's bars have an interior hole (consecutive bars further apart than
  one bar) or a passed ``DataQuality`` reports an interior gap that overlaps the period. The Historian
  must then answer ``does_not_support`` for any claim based on the backtest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, cast

import numpy as np
import pandas as pd
import vectorbt as vbt
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas_quant.data import DataQuality
from atlas_quant.event_study import infer_bar
from atlas_quant.grid import bar_delta, slot
from atlas_quant.stats import periods_per_year

CLOSE = "close"


class Indicator(BaseModel):
    """A named indicator over closes: SMA, EMA (span, ``adjust=False``) or Wilder RSI."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["sma", "ema", "rsi"]
    window: int = Field(ge=1)


class Condition(BaseModel):
    """``left op right``; ``left`` names an indicator or ``close``, ``right`` a name or a level."""

    model_config = ConfigDict(frozen=True)

    left: str
    op: Literal["crosses_above", "crosses_below", "above", "below"]
    right: str | float


class RuleSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "rule"
    side: Literal["long", "short"] = "long"
    indicators: dict[str, Indicator] = Field(default_factory=dict)
    entry: list[Condition] = Field(min_length=1)
    exit: list[Condition] = Field(default_factory=list)
    stop_pct: float | None = Field(default=None, gt=0, lt=100)  # percent of the fill price
    take_profit_pct: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _names_exist(self) -> RuleSpec:
        if CLOSE in self.indicators:
            raise ValueError(f"{CLOSE!r} is reserved for the close price")
        known = set(self.indicators) | {CLOSE}
        for cond in [*self.entry, *self.exit]:
            names = [cond.left] + ([cond.right] if isinstance(cond.right, str) else [])
            for name in names:
                if name not in known:
                    raise ValueError(f"condition uses unknown indicator {name!r}")
        return self


@dataclass(frozen=True)
class Costs:
    """``taker_fee_rate`` is a fraction (0.0005 = 0.05 %), from trading_core's trade-fee read."""

    taker_fee_rate: float
    slippage_bps: float | None = None  # None -> quant.backtest.slippage_bps
    funding: Mapping[str, pd.Series[float]] = field(default_factory=dict)  # inst_id -> rate by time


class Metrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime | None
    end: datetime | None
    bars: int
    total_return: float | None
    cagr: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    n_trades: int
    win_rate: float | None
    profit_factor: float | None  # None when there are no losing trades
    fees_paid: float  # fraction of starting equity
    funding_paid: float  # fraction of starting equity; negative = received


class Fold(BaseModel):
    model_config = ConfigDict(frozen=True)

    fold: int
    in_sample: Metrics
    out_of_sample: Metrics


class InstrumentBacktest(BaseModel):
    model_config = ConfigDict(frozen=True)

    inst_id: str
    bar: str
    data_gap: bool
    gap_notes: list[str]
    full: Metrics
    folds: list[Fold]
    is_sharpe_mean: float | None
    oos_sharpe_mean: float | None
    overfit_warning: bool


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: RuleSpec
    period_start: datetime | None
    period_end: datetime | None
    taker_fee_rate: float
    slippage_bps: float
    splits: int
    instruments: list[InstrumentBacktest]
    n_trades: int
    data_gap: bool  # any instrument
    overfit_warning: bool  # any instrument


def _cfg() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["quant"]["backtest"]
    return cfg


def _rsi(c: pd.Series[float], window: int) -> pd.Series[float]:
    delta = c.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = gain / loss
    return cast("pd.Series[float]", 100.0 - 100.0 / (1.0 + rs))


def indicator_frame(rule: RuleSpec, c: pd.Series[float]) -> dict[str, pd.Series[float]]:
    out: dict[str, pd.Series[float]] = {CLOSE: c}
    for name, ind in rule.indicators.items():
        if ind.kind == "sma":
            out[name] = c.rolling(ind.window, min_periods=ind.window).mean()
        elif ind.kind == "ema":
            out[name] = c.ewm(span=ind.window, adjust=False, min_periods=ind.window).mean()
        else:
            out[name] = _rsi(c, ind.window)
    return out


def _condition(cond: Condition, ind: Mapping[str, pd.Series[float]]) -> pd.Series[bool]:
    left = ind[cond.left]
    right: pd.Series[float] = (
        ind[cond.right] if isinstance(cond.right, str) else pd.Series(float(cond.right), index=left.index)
    )
    valid = left.notna() & right.notna()
    if cond.op == "above":
        return valid & (left > right)
    if cond.op == "below":
        return valid & (left < right)
    prev_l, prev_r = left.shift(1), right.shift(1)
    valid = valid & prev_l.notna() & prev_r.notna()
    if cond.op == "crosses_above":
        return valid & (left > right) & (prev_l <= prev_r)
    return valid & (left < right) & (prev_l >= prev_r)


def rule_signals(rule: RuleSpec, c: pd.Series[float]) -> tuple[pd.Series[bool], pd.Series[bool]]:
    """Raw (unshifted) entry and exit signals on each bar's close."""
    ind = indicator_frame(rule, c)
    entry = pd.Series(True, index=c.index)
    for cond in rule.entry:
        entry = entry & _condition(cond, ind)
    exit_ = pd.Series(False, index=c.index)
    for cond in rule.exit:
        exit_ = exit_ | _condition(cond, ind)
    return entry, exit_


def _sharpe(r: np.ndarray[Any, np.dtype[np.float64]], ppy: float) -> float | None:
    if len(r) < 2:
        return None
    sd = float(np.std(r, ddof=1))
    if sd == 0.0 or not np.isfinite(sd):
        return None
    return float(np.mean(r)) / sd * float(np.sqrt(ppy))


def _sortino(r: np.ndarray[Any, np.dtype[np.float64]], ppy: float) -> float | None:
    if len(r) < 2:
        return None
    down = float(np.sqrt(np.mean(np.minimum(r, 0.0) ** 2)))
    if down == 0.0:
        return None
    return float(np.mean(r)) / down * float(np.sqrt(ppy))


def _run(
    df: pd.DataFrame,
    entry_raw: pd.Series[bool],
    exit_raw: pd.Series[bool],
    rule: RuleSpec,
    fee: float,
    slip: float,
    funding: pd.Series[float] | None,
    bar: str,
    ppy: float,
) -> Metrics:
    n = len(df)
    if n < 2:
        return Metrics(
            start=None if n == 0 else df.index[0].to_pydatetime(),
            end=None if n == 0 else df.index[-1].to_pydatetime(),
            bars=n, total_return=None, cagr=None, sharpe=None, sortino=None, max_drawdown=0.0,
            n_trades=0, win_rate=None, profit_factor=None, fees_paid=0.0, funding_paid=0.0,
        )  # fmt: skip
    o = df["o"].astype("float64")
    c = df["c"].astype("float64")
    entries = entry_raw.shift(1, fill_value=False).astype(bool)
    exits = exit_raw.shift(1, fill_value=False).astype(bool)
    price = o.copy()
    entries.iloc[-1] = False
    if not bool(exits.iloc[-1]):
        exits.iloc[-1] = True
        price.iloc[-1] = float(c.iloc[-1])
    kwargs: dict[str, Any] = {
        "price": price,
        "open": o,
        "high": df["h"].astype("float64"),
        "low": df["l"].astype("float64"),
        "fees": fee,
        "slippage": slip,
        "init_cash": 1.0,
        "freq": bar_delta(bar),
        "stop_entry_price": "fillprice",
        "stop_exit_price": "stopmarket",
    }
    if rule.stop_pct is not None:
        kwargs["sl_stop"] = rule.stop_pct / 100.0
    if rule.take_profit_pct is not None:
        kwargs["tp_stop"] = rule.take_profit_pct / 100.0
    if rule.side == "long":
        pf = vbt.Portfolio.from_signals(c, entries=entries, exits=exits, **kwargs)
    else:
        pf = vbt.Portfolio.from_signals(c, short_entries=entries, short_exits=exits, **kwargs)

    value = np.asarray(pf.value(), dtype="float64")
    trades: pd.DataFrame = pf.trades.records
    pnl = trades["pnl"].to_numpy(dtype="float64").copy()
    fees_paid = float((trades["entry_fees"] + trades["exit_fees"]).sum())

    funding_cost = np.zeros(n)
    if funding is not None and not funding.empty:
        assets = np.asarray(pf.assets(), dtype="float64")
        pos = {int(ts.value): i for i, ts in enumerate(pd.DatetimeIndex(df.index))}
        for ts, rate in funding.items():
            t = pd.Timestamp(cast(Any, ts)).to_pydatetime()
            if t.tzinfo is None:
                raise ValueError("funding index must be timezone-aware")
            i = pos.get(pd.Timestamp(slot(bar, t)).value)
            if i is None or assets[i] == 0.0:
                continue
            funding_cost[i] += assets[i] * float(o.iloc[i]) * float(rate)
        entry_idx = trades["entry_idx"].to_numpy(dtype="int64")
        exit_idx = trades["exit_idx"].to_numpy(dtype="int64")
        for k in range(len(pnl)):
            pnl[k] -= float(funding_cost[entry_idx[k] : exit_idx[k]].sum())
    equity = value - np.cumsum(funding_cost)

    r = equity[1:] / equity[:-1] - 1.0
    total = float(equity[-1] / equity[0] - 1.0)
    years = (n - 1) / ppy
    cagr = float((equity[-1] / equity[0]) ** (1.0 / years) - 1.0) if equity[-1] > 0 else -1.0
    running = np.maximum.accumulate(equity)
    max_dd = float(np.min(equity / running - 1.0))
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    return Metrics(
        start=df.index[0].to_pydatetime(),
        end=df.index[-1].to_pydatetime(),
        bars=n,
        total_return=total,
        cagr=cagr,
        sharpe=_sharpe(r, ppy),
        sortino=_sortino(r, ppy),
        max_drawdown=min(max_dd, 0.0),
        n_trades=len(pnl),
        win_rate=float(len(wins) / len(pnl)) if len(pnl) else None,
        profit_factor=float(wins.sum() / -losses.sum()) if len(losses) else None,
        fees_paid=fees_paid,
        funding_paid=float(funding_cost.sum()),
    )


def _gap_notes(
    df: pd.DataFrame, bar: str, period: tuple[datetime, datetime] | None, quality: DataQuality | None
) -> list[str]:
    notes: list[str] = []
    step = bar_delta(bar)
    index = pd.DatetimeIndex(df.index)
    if len(index) >= 2:
        ns = index.to_numpy(dtype="datetime64[ns]").astype("int64")
        step_ns = int(step.total_seconds()) * 1_000_000_000
        for i in np.flatnonzero(np.diff(ns) > step_ns).tolist():
            notes.append(f"missing bars between {index[i].isoformat()} and {index[i + 1].isoformat()}")
    if quality is not None:
        for gap in quality.gaps:
            if gap.position != "interior":
                continue
            if period is not None and (gap.to_ts < period[0] or gap.from_ts >= period[1]):
                continue
            note = f"unrepaired gap {gap.from_ts.isoformat()}..{gap.to_ts.isoformat()}"
            if note not in notes:
                notes.append(note)
    return notes


def _in_bounds(
    index: pd.DatetimeIndex, bounds: tuple[datetime, datetime]
) -> np.ndarray[Any, np.dtype[np.bool_]]:
    lo, hi = pd.Timestamp(bounds[0]), pd.Timestamp(bounds[1])
    return np.asarray([lo <= ts < hi for ts in index], dtype=bool)


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("period bounds must be timezone-aware")
    return ts.astimezone(UTC)


def backtest_rule(
    rule_spec: RuleSpec,
    frames: Mapping[str, pd.DataFrame],
    period: tuple[datetime, datetime] | None,
    costs: Costs,
    *,
    splits: int | None = None,
    quality: Mapping[str, DataQuality] | None = None,
    bar: str | None = None,
    asset_class: str | None = None,
) -> BacktestResult:
    """Backtest ``rule_spec`` on each frame over ``period`` (``start <= ts < end``; None = all bars)."""
    cfg = _cfg()
    k = int(cfg["walk_forward_splits"]) if splits is None else splits
    if k < 1:
        raise ValueError("splits must be >= 1")
    slip_bps = float(cfg["slippage_bps"]) if costs.slippage_bps is None else costs.slippage_bps
    slip = slip_bps / 10_000.0
    fraction = float(Decimal(str(cfg["overfit_sharpe_fraction"])))
    bounds = None if period is None else (_aware(period[0]), _aware(period[1]))

    results: list[InstrumentBacktest] = []
    for inst_id, raw in frames.items():
        if not isinstance(raw.index, pd.DatetimeIndex) or raw.index.tz is None:
            raise ValueError(f"{inst_id}: frame index must be a timezone-aware DatetimeIndex")
        df = raw.sort_index()
        df.index = pd.DatetimeIndex(df.index).tz_convert(UTC)
        inst_bar = bar or infer_bar(pd.DatetimeIndex(df.index))
        ppy = periods_per_year(inst_bar, asset_class)
        entry_raw, exit_raw = rule_signals(rule_spec, df["c"].astype("float64"))
        if bounds is not None:
            mask = _in_bounds(pd.DatetimeIndex(df.index), bounds)
            df, entry_raw, exit_raw = df[mask], entry_raw[mask], exit_raw[mask]
        funding = costs.funding.get(inst_id)
        if funding is not None and bounds is not None:
            funding = funding[_in_bounds(pd.DatetimeIndex(funding.index), bounds)]

        def run(lo: int, hi: int) -> Metrics:
            return _run(
                df.iloc[lo:hi], entry_raw.iloc[lo:hi], exit_raw.iloc[lo:hi], rule_spec,  # noqa: B023
                costs.taker_fee_rate, slip, funding, inst_bar, ppy,  # noqa: B023
            )  # fmt: skip

        blocks = np.array_split(np.arange(len(df)), k + 1)
        if any(len(b) < 2 for b in blocks):
            raise ValueError(f"{inst_id}: {len(df)} bars are too few for {k} walk-forward splits")
        folds = [
            Fold(
                fold=j,
                in_sample=run(0, int(blocks[j][0])),
                out_of_sample=run(int(blocks[j][0]), int(blocks[j][-1]) + 1),
            )
            for j in range(1, k + 1)
        ]
        is_s = [f.in_sample.sharpe for f in folds if f.in_sample.sharpe is not None]
        oos_s = [f.out_of_sample.sharpe if f.out_of_sample.sharpe is not None else 0.0 for f in folds]
        is_mean = float(np.mean(is_s)) if is_s else None
        oos_mean = float(np.mean(oos_s)) if oos_s else None
        overfit = is_mean is not None and is_mean > 0 and (oos_mean or 0.0) < fraction * is_mean
        notes = _gap_notes(df, inst_bar, bounds, (quality or {}).get(inst_id))
        results.append(
            InstrumentBacktest(
                inst_id=inst_id,
                bar=inst_bar,
                data_gap=bool(notes),
                gap_notes=notes,
                full=run(0, len(df)),
                folds=folds,
                is_sharpe_mean=is_mean,
                oos_sharpe_mean=oos_mean,
                overfit_warning=overfit,
            )
        )

    return BacktestResult(
        rule=rule_spec,
        period_start=None if bounds is None else bounds[0],
        period_end=None if bounds is None else bounds[1],
        taker_fee_rate=costs.taker_fee_rate,
        slippage_bps=slip_bps,
        splits=k,
        instruments=results,
        n_trades=sum(r.full.n_trades for r in results),
        data_gap=any(r.data_gap for r in results),
        overfit_warning=any(r.overfit_warning for r in results),
    )
