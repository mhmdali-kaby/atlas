# ATL-145 — circuit breakers: `trip()` / `clear()`, the 15 s monitor, the OKX error hook

For the kill switch and resume (ATL-147), reconciliation (ATL-144, trips `reconciliation_mismatch`),
the War Room/notifications UI, and anyone who reads `trading_state.breakers`.

## Two-minute version

- `from trading_core.risk.breakers import Breakers` →
  `Breakers(engine, env, *, redis_client=None, now=None)`. `now` is a zero-arg clock.
  - `trip(name, reason, detail=None, *, key=None) -> bool` — True when it newly activated (and
    announced). False when already active, or when `key` equals the key it was last tripped with.
  - `clear(name, *, by="owner") -> bool` — False when it was not active. Clearing `okx_errors` also
    deletes the error counter (`atlas:breakers:<env>:okx_errors`).
  - `clear_all(*, by="owner") -> list[str]` — **the resume endpoint calls this** (ATL-147): it clears
    every active breaker and returns the names. It does not touch `trading_state.paused`; resume sets
    that itself.
  - `active() -> {name: entry}`, `entries()` (active and cleared).
- Names (`NAMES`): `daily_loss`, `weekly_loss`, `consecutive_losses`, `okx_errors`, `data_stale`,
  `volatility_halt`, `reconciliation_mismatch`, `agent_budget`. Any other name raises `ValueError`.
  `SELF_CLEARING = {"data_stale"}`; `LABELS[name]` is the plain English label.
- Any active breaker → Risk Engine check 1 `circuit_breaker_active` (values.active_breakers lists
  them) and Executor refusal `breaker_active`. Both already read the column; nothing else to wire.
- Reconciler: `Breakers(engine, env).trip("reconciliation_mismatch", "<plain reason>", {...})`.
  Every trip, wherever from, announces itself (below).
- The app (`create_app`) builds a `BreakerMonitor(Breakers(db, env))` when no scheduler is injected,
  and starts it with the scheduler. Tests pass `breaker_monitor=`. While running it registers
  `Breakers.okx_error_hook` with `trading_core.okx.trade_read.add_error_hook`.

## `trading_state.breakers` entry

```json
{"okx_errors": {"active": true, "since": "2033-01-13T12:04:50Z",
  "reason": "3 OKX errors in the last 5 minutes; the latest: Insufficient balance.",
  "detail": {"count": 3, "window_seconds": 300, "last_code": "51008", "last_path": "trade/order",
             "last_message": "Insufficient balance."},
  "key": null, "self_clearing": false, "fresh_since": null,
  "cleared_at": null, "cleared_by": null}}
```

After a clear: `active: false`, `cleared_at`, `cleared_by` (`owner` or `system`) are set; the entry is
kept (with its `key`). `fresh_since` is only used by `data_stale`. Decimals in `detail` are strings,
datetimes ISO `…Z`.

## Events

`atlas.system`, type `system.circuit_breaker`, source `service:trading_core.breakers`, one per trip and
one per clear:

```json
{"name": "data_stale", "label": "Stale market data", "state": "tripped",
 "reason": "No market data for more than 30 s on BTC-USDT-SWAP.",
 "detail": {"instruments": {"BTC-USDT-SWAP": 45.0}, "max_age_seconds": 30},
 "since": "2033-01-13T12:00:00Z", "self_clearing": true, "cleared_at": null, "cleared_by": null}
```

`state` is `tripped` or `cleared`. An instrument age of `null` means no ticker ever arrived.

`atlas.warroom` `warroom.message`, source `system:circuit_breaker`, author `circuit_breaker`, Main
thread:

- on trip, kind `system_alert`, content_md
  ``**Circuit breaker: OKX errors** (`okx_errors`). <reason> New orders are paused. It stays on until you resume trading.``
  (data_stale says "It clears itself once data has been fresh for 60 s."), card =
  `{"type": "circuit_breaker", "code": "<name>", ...the system.circuit_breaker payload}`.
- on clear, kind `text`, no card: ``Circuit breaker Stale market data (`data_stale`) is off: market data has been fresh again.``
  or ``… is off: cleared by the owner.``

## What trips each one

| name | when | key (no re-trip after the owner clears) |
|---|---|---|
| daily_loss | check-6 day loss % ≥ `max_daily_loss_pct` (every 15 s) | `day:<Baghdad midnight>` |
| weekly_loss | week loss % ≥ `max_weekly_loss_pct` | `week:<Monday Baghdad>` |
| consecutive_losses | losing type-2 bills in a row ≥ `max_consecutive_losses` | `loss:<newest losing bill ts>` |
| okx_errors | ≥ 3 trade-client errors in 300 s (Redis ZSET), at the hook | none |
| data_stale | a held or today's-watchlist instrument's `tickers` older than `market_data_max_age_seconds` | none; clears itself after 60 s fresh |
| volatility_halt | held instrument's newest closed 1m log return > `volatility_halt_sigma` × σ of the prior 24 h of 1m returns | `<inst>:<bar ts>` |
| agent_budget | `system.agent_state` with `reason: over_budget` on atlas.system | `<agent>:<Baghdad date>` |
| reconciliation_mismatch | the reconciler calls `trip()` | whatever it passes |

Losses use `trading_core.risk.context.loss_figures(session, env, now)` — the same function check 6
now uses, so the two cannot disagree. Held = `trading_core.ws.subscriptions.held_instruments`,
watchlist = `watchlist_instruments` (split out of `desired_instruments`, unchanged behaviour).

Monitor triggers besides the 15 s loop: `candle.closed` with bar `1m` on a held instrument
(volatility for that instrument), `market.stale` / `market.fresh` (stale check), and the agent state
event. The consumer reads from `$`, so events from before a restart are not replayed; the loop
covers them.

## OKX error hook

`trading_core.okx.trade_read.add_error_hook(fn)` / `remove_error_hook(fn)`; `fn(env, error)` with
`error` an `OkxClientError` (`OkxTradeError` for OKX refusals, also `OkxNetworkError`/`OkxHttpError`).
Called once per failed `OkxTradeReadClient`/`OkxTradeClient` call after the client's own retries, and
once per refused row of `place_batch_orders` / `cancel_algos` (those still return the rows). A hook
that raises is logged and ignored. The breaker ignores `circuit_breakers.okx_errors.ignored_codes`
(51016, 51400, 51401, 51402) and errors from another env's client.

## Config (`config/defaults.yaml` → `circuit_breakers`)

`evaluate_interval_seconds: 15`, `okx_errors.{threshold: 3, window_seconds: 300, ignored_codes}`,
`data_stale.clear_after_fresh_seconds: 60`,
`volatility.{bar: 1m, lookback_hours: 24, min_returns: 60, max_bar_age_seconds: 180}`,
`stream_block_ms: 1000`. Owner limits stay in Settings (`volatility_halt_sigma`, loss limits,
`max_consecutive_losses`, `market_data_max_age_seconds`).

## Not built here

- The resume/pause endpoints (ATL-147) — call `clear_all(by="owner")`.
- Owner notifications beyond the War Room message (brief §17 is a later task).
- Check 6 keeps rejecting while a loss is over its limit even after the owner clears the breaker;
  that is ATL-127's rule, unchanged.
