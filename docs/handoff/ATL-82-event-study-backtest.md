# ATL-82 — atlas_quant: event_study and backtest_rule

Two pure functions in `services/quant` (`atlas_quant`), exported from the package root. Config:
`config/defaults.yaml` → `quant.event_study` and `quant.backtest` (regenerate with
`cd backend && php artisan atlas:defaults`). Tests: `quant/tests/test_event_study.py`,
`test_backtest.py`, `test_backtest_golden.py`.

## The two-minute version

- `backtest_rule` simulates fills with **vectorbt 1.2.0** (`Portfolio.from_signals`); the module builds the
  signals, adds funding and computes every metric itself from the equity curve. The golden file
  (`fixtures/make_backtest_golden.py`, plain-Python loops) reproduces the SMA 10/30 result to 1e-9.
- No look-ahead: a condition is read on bar t's close and fills at bar t+1's **open**. event_study's price
  "at" time x is the close of the last bar closed by x (`ts + bar <= x`, same rule as `load_candles`).
- For the Historian (ATL-87): **`data_gap=true` → answer `does_not_support` for any backtest claim**; also
  read `overfit_warning`. Pass `quality={inst_id: DataQuality}` from `load_candles` so unrepaired interior
  gaps are caught even if the frame you pass looks complete. Leading/trailing gaps do not set data_gap.
- The fee comes from the caller (`Costs(taker_fee_rate=...)`, fraction: 0.0005 = 0.05 %), from
  trading_core's trade-fee read. Never hard-code it. Slippage defaults to `quant.backtest.slippage_bps` (2).
- vectorbt is in the mypy `ignore_missing_imports` override (services/pyproject.toml); first import costs a
  few seconds (numba).

## event_study

```python
from atlas_quant import event_study
res = event_study(df_or_dict, [e1, e2, ...], window=(timedelta(hours=-24), timedelta(hours=72)), bar=None)
```

- `prices`: one candles frame (tz-aware index, `h, l, c` floats) or `{inst_id: frame}` for an asset class
  (every (instrument, event) pair is one case). `events`: tz-aware datetimes — the `scheduled_at` of
  `economic_events` rows or the earnings date/time. `window` defaults to `quant.event_study.window_hours`.
  `bar` is inferred from the median spacing when omitted.
- `EventStudyResult`: `bar`, `window_hours (lo, hi)`, `inst_ids` (empty for a single frame), `n_events`,
  `n_cases`, `dropped_missing_bars`, `dropped_out_of_range`, `dropped_short_history` (< ATR window bars
  closed before the event), `offsets: list[OffsetStat(offset_hours, n, mean, median, p10, p90)]` — every bar
  step in the window, returns are `P(e+k)/P(e) - 1` as fractions — and
  `atr_moves: list[AtrMove(horizon_hours, n, mean_abs_move_atr)]` for 1/4/24 h, |ΔP| / Wilder ATR(14) of the
  bars closed by the event (same ATR as `compute_stats`).

## backtest_rule

```python
from atlas_quant import RuleSpec, Indicator, Condition, Costs, backtest_rule
rule = RuleSpec(
    name="sma_10_30_cross", side="long",          # or "short"
    indicators={"sma10": Indicator(kind="sma", window=10), "sma30": Indicator(kind="sma", window=30)},
    entry=[Condition(left="sma10", op="crosses_above", right="sma30")],   # ALL must hold
    exit=[Condition(left="sma10", op="crosses_below", right="sma30")],    # ANY closes
    stop_pct=2.0, take_profit_pct=4.0,             # percent of the fill price, optional
)
res = backtest_rule(rule, {"BTC-USDT-SWAP": df}, (start, end), Costs(taker_fee_rate=0.0005,
                    slippage_bps=None, funding={"BTC-USDT-SWAP": funding_series}),
                    splits=None, quality={"BTC-USDT-SWAP": dq}, bar=None, asset_class="crypto")
```

- Indicator kinds: `sma`, `ema` (span, adjust=False), `rsi` (Wilder). `close` is the reserved name for the
  price; `right` may be a name or a number (price vs level, RSI threshold). Ops: `crosses_above`,
  `crosses_below`, `above`, `below`. Unknown names are a pydantic ValidationError.
- `period` is `start <= ts < end` (None = all bars); bars before `start` still warm the indicators.
- Funding series: index = funding times (tz-aware), values = rate (fraction). Charged as
  units × the bar's open × rate on the bar whose slot contains the time; not reinvested into later sizing.
- `BacktestResult`: `rule`, `period_start/end`, `taker_fee_rate`, `slippage_bps`, `splits`, `n_trades`,
  `data_gap` (any instrument), `overfit_warning` (any instrument), `instruments: list[InstrumentBacktest]`.
- `InstrumentBacktest`: `inst_id`, `bar`, `data_gap`, `gap_notes: list[str]`, `full: Metrics`,
  `folds: list[Fold(fold, in_sample, out_of_sample)]`, `is_sharpe_mean`, `oos_sharpe_mean`, `overfit_warning`.
- `Metrics`: `start`, `end`, `bars`, `total_return`, `cagr`, `sharpe`, `sortino`, `max_drawdown` (≤ 0),
  `n_trades`, `win_rate`, `profit_factor` (None with no losers), `fees_paid`, `funding_paid` — all fractions
  of a starting equity of 1.0. `sharpe`/`sortino` are None when undefined (flat run).
- Walk-forward: bars cut into `splits + 1` equal blocks (default `quant.backtest.walk_forward_splits` = 3);
  fold j is IS on blocks 0..j-1, OOS on block j, each run starting flat. `overfit_warning` = mean IS Sharpe
  > 0 and mean OOS Sharpe < `overfit_sharpe_fraction` (0.5) × it. Raises ValueError if a block has < 2 bars.
- Fill semantics (full list in the module docstring): whole equity, no leverage, one position; slippage
  against us on every fill, taker fee on fill value; stops/TPs checked from the bar after entry on high/low,
  filled at the stop with slippage (or the open on a gap); a same-bar stop and TP resolve as the stop; an
  open position is closed at the last bar's close.

## Not built

- No MCP tool wrapper or DB queries for events (ATL-87 loads candles/events and calls these).
- Multi-instrument results are per instrument, not a combined portfolio.
