# ATL-208 — `/api/backtests` (for the Backtester screens, ATL-209/210)

Code: `backend/routes/api/backtests.php`, `App\Http\Controllers\Backtests\BacktestController`,
`App\Http\Requests\Backtests\StoreBacktestRequest`, `App\Jobs\RunBacktest`, model `App\Models\BacktestRun`
(table `backtest_runs`). Tests: `backend/tests/Feature/Backtests/BacktestApiTest.php`.

All three need the Sanctum SPA session (401 otherwise). Runs belong to the **active env** and to the
**owner who started them**; another env's or another user's run is 404 (not 403).

## POST /api/backtests — start a run

Throttle `backtests-start`: `backtests_api.starts_per_minute` (6) per user; the 7th in a minute is **429**.

Body: exactly trading_core's `/internal/backtest` request (see `docs/handoff/ATL-205-backtest-endpoint.md`):

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

Validated here before anything is queued (422 with Laravel's usual `{"message", "errors": {field: [..]}}`):
unknown top-level or rule_spec keys; `side` long|short; `indicators` ≤ 20, names `^[A-Za-z_][A-Za-z0-9_]{0,63}$`,
`close` reserved, `kind` sma|ema|rsi, `window` 1–5000; `entry` 1–10 and `exit` 0–10 conditions
`{left, op, right}` with `op` crosses_above|crosses_below|above|below, `left` a defined indicator or
`close`, `right` a defined name or a number; `stop_pct` 0<x<100, `take_profit_pct` > 0;
`inst_ids` 1–`backtest.max_inst_ids` (5), distinct, OKX-id shaped; `bar` 1m|5m|15m|1H|4H|1D;
`period.start/end` ISO with an offset (`Z` or `+03:00`; a naive datetime is refused), start < end;
`splits` 1–10; `slippage_bps` 0–1000. Error keys are the dotted paths, e.g. `rule_spec.entry.0.right`,
`period.end`. Bar-count limits (`too_many_bars`) are checked only by trading_core → the run fails (below).

**202**:

```json
{"ok": true, "id": "9d7c1f7e-6a8b-4c55-9a4e-2f1d8c3b7a10", "status": "queued", "created_at": "2026-11-02T08:15:03.123456Z"}
```

Writes a `backtest_runs` row (status `queued`) and an audit_log row `backtest.started`
(subject `backtest_runs`, after = env, rule name, inst_ids, bar, period), then dispatches `RunBacktest`
after commit.

## GET /api/backtests — recent runs (no heavy result)

Newest first, up to `backtests_api.list_limit` (50). No pagination.

```json
{
  "env": "demo",
  "data": [
    {
      "id": "9d7c1f7e-6a8b-4c55-9a4e-2f1d8c3b7a10",
      "env": "demo",
      "status": "done",
      "rule_name": "sma_10_30_cross",
      "side": "long",
      "inst_ids": ["BTC-USDT-SWAP", "XAU-USDT-SWAP"],
      "bar": "1H",
      "period": {"start": "2026-05-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
      "n_trades": 18,
      "overfit_warning": false,
      "data_gap": false,
      "error": null,
      "error_reason": null,
      "created_at": "2026-11-02T08:15:03.123456Z",
      "started_at": "2026-11-02T08:15:03.410000Z",
      "finished_at": "2026-11-02T08:15:09.902000Z"
    }
  ]
}
```

`n_trades`, `overfit_warning`, `data_gap` are null until the run is `done`. `rule_name` is null when the
owner sent no name (trading_core then calls it `"rule"`).

## GET /api/backtests/{id} — one run

The same fields as a list row, plus `request` (the stored body) and `result`:

```json
{
  "id": "9d7c1f7e-…", "env": "demo", "status": "done", "rule_name": "sma_10_30_cross", "side": "long",
  "inst_ids": ["BTC-USDT-SWAP", "XAU-USDT-SWAP"], "bar": "1H",
  "period": {"start": "2026-05-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
  "n_trades": 18, "overfit_warning": false, "data_gap": false, "error": null, "error_reason": null,
  "created_at": "…", "started_at": "…", "finished_at": "…",
  "request": { "rule_spec": {…}, "inst_ids": […], "bar": "1H", "period": {…}, "splits": 3, "slippage_bps": 2 },
  "result": { "ok": true, "env": "demo", "rule": {…}, "instruments": [ {"full": {…}, "folds": […], "equity_curve": […], "trades": […], …} ], "excluded": […], "data_as_of": "…" }
}
```

`result` is trading_core's 200 body **unchanged** — every field is documented in the ATL-205 handoff.
Note: it is stored as jsonb, so object key order is not preserved and `2.0` comes back as `2`.

A failed run:

```json
{ "id": "…", "status": "failed", "error": "too_many_bars",
  "error_reason": "the period is 1100 1H bars per instrument; backtest.max_bars is 500",
  "result": null, "n_trades": null, "…": "…" }
```

## Status and errors

`queued` → `running` → `done` | `failed`. Poll `GET /api/backtests/{id}` (a run takes ≤ ~60 s); there is
no broadcast event.

`error` codes on a failed run:

| error | from | error_reason |
| --- | --- | --- |
| `invalid`, `too_many_bars`, `no_data`, `insufficient_data`, `timeout` | trading_core 422 | trading_core's `reason`, verbatim (English) |
| `trading_core_error` | trading_core non-2xx other than a 422 with a code | its `reason`/`detail`, else a lang line with the HTTP status |
| `trading_core_unavailable` | connection refused / no secret | `backtests.trading_core_unavailable` |
| `job_failed` | the worker killed or crashed the job | `backtests.job_failed` |

Show `error_reason` as-is for the 422 codes; map the code to your own translated copy if you want Arabic.

## Queue

`RunBacktest` runs on its own queue `backtests` (Horizon `supervisor-backtests`, 1 process). Tries 1,
`failOnTimeout`; HTTP wait 70 s < job timeout 80 s < redis `retry_after` 90 s (a test pins this chain).
A failure is stored, never retried. For local dev without Horizon: `php artisan queue:work redis --queue=backtests`.

## Two-minute version

- Nothing here can reach an order: the job only calls the read-only `/internal/backtest`.
- The screens should poll the detail while `queued`/`running`, and keep the list light — it never carries `result`.
