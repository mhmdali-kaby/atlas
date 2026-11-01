# ATL-205 — trading_core `POST /internal/backtest`

For ATL-208 (Laravel `/api/backtests`) and, through it, ATL-209/210 (Backtester screen).

Code: `services/trading_core/src/trading_core/backtest/api.py`. Tests: `services/trading_core/tests/test_backtest_api.py`.

## Call

`POST http://127.0.0.1:8100/internal/backtest`, `Content-Type: application/json`, signed like every
other `/internal` route (decision #235): `X-Atlas-Timestamp`, `X-Atlas-Signature` = hex
HMAC-SHA256(shared secret, ts + "POST" + "/internal/backtest" + body). Unsigned, older than 30 s, or
wrongly signed → **401** from the signature middleware.

It is **synchronous**: the response carries the finished result. A run is bounded by
`backtest.timeout_seconds` (60) and `backtest.max_bars` (50,000 bars per instrument), so give the
Laravel HTTP client a timeout a little above 60 s. If ATL-208 wants "start, then fetch", make this call
from a queued job and store the body.

The env is the one trading_core runs in, as for every other internal route.

### Request

```json
{
  "rule_spec": {
    "name": "sma_10_30_cross",
    "side": "long",
    "indicators": {"sma10": {"kind": "sma", "window": 10}, "sma30": {"kind": "sma", "window": 30}},
    "entry": [{"left": "sma10", "op": "crosses_above", "right": "sma30"}],
    "exit":  [{"left": "sma10", "op": "crosses_below", "right": "sma30"}],
    "stop_pct": 3,
    "take_profit_pct": null
  },
  "inst_ids": ["BTC-USDT-SWAP", "XAU-USDT-SWAP"],
  "bar": "1H",
  "period": {"start": "2026-05-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
  "splits": 3,
  "slippage_bps": 2
}
```

- `rule_spec` is `atlas_quant.RuleSpec` exactly (the Historian's `history.backtest_rule` schema):
  `side` long|short (default long); `indicators` `{name: {kind: sma|ema|rsi, window ≥ 1}}`;
  `entry` ≥ 1 condition (all must hold), `exit` conditions (any closes); a condition is
  `{left, op: crosses_above|crosses_below|above|below, right}` where `left` is an indicator name or
  `"close"`, `right` a name or a number; `stop_pct` 0 < x < 100 and `take_profit_pct` > 0 are optional
  (percent of the fill price). An unknown indicator name is refused.
- `inst_ids`: 1–5 (`backtest.max_inst_ids`), no repeats.
- `bar`: `1m 5m 15m 1H 4H 1D`.
- `period`: timezone-aware ISO datetimes, `start < end`. Bars with `start <= ts < end` that have closed
  by now are used. 250 bars (`backtest.warmup_bars`) before `start` are loaded only to warm the
  indicators.
- `splits` optional 1–10 (default `quant.backtest.walk_forward_splits` = 3). `slippage_bps` optional
  0–1000 (default `quant.backtest.slippage_bps` = 2).
- Unknown top-level keys are refused.

### 200 response

Real output from the test data, with the equity curve cut to 3 points and the trades to 1 row for
the example (the defaults are 1,000 each):

```json
{
  "ok": true,
  "env": "demo",
  "rule": {
    "name": "sma_10_30_cross",
    "side": "long",
    "indicators": {"sma10": {"kind": "sma", "window": 10}, "sma30": {"kind": "sma", "window": 30}},
    "entry": [{"left": "sma10", "op": "crosses_above", "right": "sma30"}],
    "exit": [{"left": "sma10", "op": "crosses_below", "right": "sma30"}],
    "stop_pct": 3.0,
    "take_profit_pct": null
  },
  "bar": "1H",
  "period": {"start": "2030-01-05T04:00:00+00:00", "end": "2030-02-20T00:00:00+00:00"},
  "splits": 2,
  "slippage_bps": 2.0,
  "n_trades": 18,
  "data_gap": false,
  "overfit_warning": false,
  "instruments": [
    {
      "inst_id": "BT8F3B48A-USDT-SWAP",
      "bar": "1H",
      "data_gap": false,
      "gap_notes": [],
      "full": {
        "start": "2030-01-05T04:00:00Z",
        "end": "2030-02-19T23:00:00Z",
        "bars": 1100,
        "total_return": -0.03755582768777466,
        "cagr": -0.2629644470525967,
        "sharpe": -0.7086381681947372,
        "sortino": -0.9959047669002952,
        "max_drawdown": -0.13334310582901965,
        "n_trades": 18,
        "win_rate": 0.3333333333333333,
        "profit_factor": 0.82310376570566,
        "fees_paid": 0.017604938679087842,
        "funding_paid": 0.005543110345149186
      },
      "folds": [
        {
          "fold": 1,
          "in_sample": {
            "start": "2030-01-05T04:00:00Z", "end": "2030-01-20T10:00:00Z", "bars": 367,
            "total_return": -0.01841936995137272, "cagr": -0.359155064682498,
            "sharpe": -1.366358346229036, "sortino": -1.8619499080515094,
            "max_drawdown": -0.06861472747769382, "n_trades": 6, "win_rate": 0.5,
            "profit_factor": 0.5465730720604393, "fees_paid": 0.005985364980968714,
            "funding_paid": 0.0014147223374038557
          },
          "out_of_sample": {
            "start": "2030-01-20T11:00:00Z", "end": "2030-02-04T17:00:00Z", "bars": 367,
            "total_return": -0.06421816807376346, "cagr": -0.795788211146564,
            "sharpe": -4.5318713601031435, "sortino": -5.960956441312888,
            "max_drawdown": -0.06510187793357058, "n_trades": 7, "win_rate": 0.2857142857142857,
            "profit_factor": 0.34143887259449035, "fees_paid": 0.006691221243955736,
            "funding_paid": 0.0015372606785306823
          }
        },
        {
          "fold": 2,
          "in_sample": {
            "start": "2030-01-05T04:00:00Z", "end": "2030-02-04T17:00:00Z", "bars": 734,
            "total_return": -0.08154553070662662, "cagr": -0.638168859607025,
            "sharpe": -3.0519357835033563, "sortino": -4.072627735793967,
            "max_drawdown": -0.12933704908128485, "n_trades": 13, "win_rate": 0.38461538461538464,
            "profit_factor": 0.4024972524706476, "fees_paid": 0.012562804365563654,
            "funding_paid": 0.0029258424398053134
          },
          "out_of_sample": {
            "start": "2030-02-04T18:00:00Z", "end": "2030-02-19T23:00:00Z", "bars": 366,
            "total_return": 0.04774326351309144, "cagr": 2.062788819261663,
            "sharpe": 3.010662853358597, "sortino": 4.5572936244973175,
            "max_drawdown": -0.1013731452604032, "n_trades": 5, "win_rate": 0.2,
            "profit_factor": 1.5801322009597865, "fees_paid": 0.005472370365761098,
            "funding_paid": 0.0028405945644970573
          }
        }
      ],
      "is_sharpe_mean": -2.209147064866196,
      "oos_sharpe_mean": -0.7606042533722732,
      "overfit_warning": false,
      "inst_type": "SWAP",
      "asset_class": "crypto",
      "taker_fee_rate": 0.0005,
      "fee_source": "config",
      "funding_included": true,
      "verdict_cap": null,
      "data_quality": {
        "inst_id": "BT8F3B48A-USDT-SWAP",
        "bar": "1H",
        "env": "demo",
        "window_start": "2029-12-25T18:00:00Z",
        "window_end": "2030-02-20T00:00:00Z",
        "as_of": "2030-02-21T00:00:00Z",
        "expected_bars": 1350,
        "returned_bars": 1200,
        "missing_bars": 150,
        "gaps": [
          {"from_ts": "2029-12-25T18:00:00Z", "to_ts": "2029-12-31T23:00:00Z", "missing_bars": 150, "position": "leading"}
        ],
        "duplicate_count": 0,
        "zero_volume_count": 0,
        "gap_in_window": true,
        "interior_gap": false
      },
      "equity_curve": [
        {"ts": "2030-01-05T04:00:00Z", "equity": 1.0},
        {"ts": "2030-01-28T02:00:00Z", "equity": 0.9379561092551295},
        {"ts": "2030-02-19T23:00:00Z", "equity": 0.9624441723122253}
      ],
      "equity_curve_points": 1100,
      "trades": [
        {
          "side": "long",
          "entry_ts": "2030-01-09T02:00:00Z",
          "exit_ts": "2030-01-10T05:00:00Z",
          "entry_price": 86.574413330382,
          "exit_price": 87.04946930346401,
          "size": 0.011544984383097203,
          "pnl": 0.004173956256001592,
          "return_pct": 0.44845124192003283,
          "fees": 0.0010022425067702215,
          "funding": 0.0003083150275569575
        }
      ],
      "trades_total": 18,
      "trades_truncated": true
    }
  ],
  "excluded": [
    {"inst_id": "NOPE-USDT-SWAP", "reason": "no 1H candles in demo for the period"}
  ],
  "data_as_of": "2030-02-20T00:00:00+00:00"
}
```

Field notes:

- Every metric is a float **fraction of starting equity** (equity starts at 1.0): `total_return`,
  `cagr`, `max_drawdown` (≤ 0), `fees_paid`, `funding_paid` (negative = received). Multiply by 100 for
  a percent. `sharpe`, `sortino`, `win_rate`, `profit_factor`, `cagr`, `total_return` can be `null`
  (too few bars, a flat run). `profit_factor: null` also means no losing trades.
- `full` is the whole period. `folds[j]` is anchored walk-forward: in-sample on blocks 0..j-1,
  out-of-sample on block j, each run starting flat. `overfit_warning` is true when the mean IS Sharpe
  is positive and the mean OOS Sharpe is below `quant.backtest.overfit_sharpe_fraction` (0.5) of it.
- `data_gap` / `gap_notes`: an interior hole in the candles, or an unrepaired interior gap overlapping
  the period. Then `verdict_cap` is `"does_not_support"`: no claim can be based on this run (brief
  §7.3). Show the notes. `data_quality` is the loader's full report for the loaded window, warm-up
  included, so a `leading` gap before the first stored bar is normal and is not a data gap.
- `equity_curve`: at most `backtest.equity_curve_max_points` (1,000) evenly spaced points of the full
  run, first and last always kept; `equity_curve_points` is the original count.
- `trades`: closed trades of the full run, the first `backtest.trades_max_rows` (1,000);
  `trades_total`, `trades_truncated`. Prices are fill prices (slippage included). `pnl`, `fees` and
  `funding` are fractions of starting equity, `pnl` after fees and funding. `return_pct` is vectorbt's
  trade return × 100, before funding. A position still open at the end is closed on the last bar.
- `taker_fee_rate` / `fee_source`: **no trade-fee rate is stored in Postgres yet**, so fees come from
  `backtest.taker_fee_rates` per instType (SPOT 0.001, SWAP 0.0005, FUTURES 0.0005) → `"config"`. An
  instrument missing from `instruments`, or an unlisted instType, uses
  `backtest.fallback_taker_fee_rate` → `"config_fallback"`. Show the rate used.
- `funding_included`: a SWAP instrument with stored `funding_rates` in the window.
- `excluded`: requested instruments with no candles; the others still run.
- `rule` is the parsed rule with its defaults filled in. The top-level `n_trades`, `data_gap` and
  `overfit_warning` are across instruments.

### 422 responses — `{"ok": false, "error": <code>, "reason": "<one line>"}`

| error | when | example reason |
| --- | --- | --- |
| `invalid` | body or rule_spec fails validation; also carries `errors`, pydantic's list of `{loc, msg, type}` | `rule_spec: Value error, condition uses unknown indicator 'sma99'` |
| `too_many_bars` | (end − start) / bar > `backtest.max_bars` | `the period is 1100 1H bars per instrument; backtest.max_bars is 500` |
| `no_data` | none of the instruments has candles in the window | `no 1H candles in demo for NOPE-USDT-SWAP` |
| `insufficient_data` | too few bars for the walk-forward splits | `X: 4 bars are too few for 3 walk-forward splits` |
| `timeout` | the run took longer than `backtest.timeout_seconds` | `the backtest took longer than backtest.timeout_seconds (60 s)` |

## Guarantees

- Read-only: one connection in a `SET TRANSACTION READ ONLY` transaction, only `SELECT`s, rolled
  back. No OKX call. The package imports nothing from `trading_core.execution`, `trading_core.okx` or
  `trading_core.risk` (tested).
- Metrics are exactly what `atlas_quant.backtest_rule` returns for the same candles, fee, funding and
  asset class (tested). Instruments with different fees or asset classes run in separate quant calls,
  so each gets its own fee and bars-per-year.

## What I would tell you in two minutes

- A timeout answers 422, but the worker thread still finishes its run in the background. That is fine
  for one owner; rate-limit the Laravel endpoint so runs cannot pile up.
- atlas_quant gained `backtest_rule_detailed()` (the same `result` plus `details[inst_id]` with
  `equity_curve` and `trades`). `backtest_rule()` now wraps it, so the Historian's output is unchanged.
