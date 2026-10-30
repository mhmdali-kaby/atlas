# ATL-127 — Risk Engine: `evaluate(proposal_id)` runs the nine §10 checks

## Two-minute version

- `from trading_core.risk.engine import evaluate` →
  `evaluate(proposal_id, *, engine, redis_client=None, now=None) -> RiskDecision`.
  `engine` is a SQLAlchemy `Engine`; `redis_client` defaults to `atlas_shared.bus.make_client()`;
  `now` is a zero-arg callable (tests pass a fixed clock).
- One call = one transaction: lock the proposal row (`FOR UPDATE`), re-read the `settings` row, load
  everything, run the checks, insert **one** `risk_checks` row, set `proposals.status = 'risk_checked'`,
  commit, then publish **one** `risk.decision` envelope on `atlas.risk`.
- It never talks to OKX. Inputs: Postgres snapshot tables (sync loops) + the ATL-125 Redis hash.
- `run_checks(ctx: RiskContext) -> Outcome` (in `trading_core/risk/engine.py`) is the pure core;
  `load_context(session, rds, proposal, now)` in `trading_core/risk/context.py` does all the I/O (the
  `RiskContext`/`ProposalInfo`/`InstrumentInfo` dataclasses, `ensure_trading_state` and
  `RiskEngineError` live there too; `trading_core.risk.engine` re-exports them). Build a `RiskContext` by hand to test a rule without a DB (see
  `trading_core/tests/test_risk_engine.py::ctx`).
- Only `kind = 'entry'` proposals are evaluated; anything else (amend/close from the PM) raises
  `RiskEngineError`, as do an unknown id and a missing settings row. Those write nothing.
- Only proposals whose status is in `risk_rules.evaluable_statuses` (`passed`, `risk_checked`) are
  evaluated; `risk_checked` is accepted so an owner Edit can re-run the check (brief §12). Any other
  status (cancelled, rejected, expired, sent, filled ...) raises `RiskEngineError` before anything is
  written or published, so a finished proposal cannot be pulled back into approval.
- Reading OKX trade data outside risk/ and execution/: use `trading_core.okx.trade_read.OkxTradeReadClient`
  (fills, orders, algo orders, max-size; GET only). `OkxTradeClient` (in `okx/trade.py`) subclasses it
  and adds the writes; `test_only_risk_and_execution_import_the_okx_trade_endpoints` fails if any
  module outside risk/, execution/ and okx/ imports `trading_core.okx.trade` / `OkxTradeClient` or
  names a write path (trade/order, batch-orders, amend-order, cancel-order, close-position, order-algo,
  cancel-algos, account/set-leverage).
- Check 6 records `values.base_unknown` (`["day"]`, `["week"]`, both or `[]`) when there is no equity
  point since the period began; the loss then reads 0% for that period.
- `trading_state` (env row) is created here if absent (`paused=false`, `breakers={}`). A breaker is
  active when `breakers[<name>]["active"]` is truthy; any active breaker rejects at check 1.

## The checks (`risk_checks.checks`, always 9 entries, in this order)

| n | check | hard-fail codes |
|---|---|---|
| 1 | `env_and_kill_switch` | `trading_paused`, `circuit_breaker_active`, `connection_not_verified`, `market_data_stale` |
| 2 | `instrument_allowed` | `instrument_unknown`, `market_disabled`, `blocklisted`, `instrument_not_live`, `outside_us_hours` |
| 3 | `proposal_valid` | `entry_unknown`, `atr_unavailable` + ATL-126 codes (`stop_missing`, `stop_wrong_side`, `stop_too_tight`, `stop_too_wide`, `expected_r_below_min`) |
| 4 | `position_size` | `equity_unknown`, `sizing_input_missing` + ATL-126 codes (`no_capacity`, `below_min_size`, `margin_insufficient`, `stop_wrong_side`) |
| 5 | `portfolio` | `max_trades_per_day`, `max_open_positions`, `correlated_exposure`, `asset_class_exposure` |
| 6 | `loss_limits` | `daily_loss_limit`, `weekly_loss_limit`, `consecutive_losses` |
| 7 | `liquidation_buffer` | `liquidation_too_close` |
| 8 | `event_blackout` | `event_blackout` |
| 9 | `autonomy` | never fails; sets the decision |

Each entry:

```json
{"n": 1, "check": "env_and_kill_switch", "status": "failed", "passed": false,
 "code": "trading_paused", "detail": "Trading is paused: kill switch.",
 "values": {"env": "demo", "paused": true, "active_breakers": [], "connection_verified": true,
            "market_data_age_seconds": 2.0, "market_data_max_age_seconds": 30}}
```

After the first failure the rest are
`{"n": k, "check": "...", "status": "not_run", "passed": null, "code": null, "detail": "Not run: ...", "values": {}}`.
`values` are JSON-safe (Decimals as strings). Check 3's values add `atr_1h` and `entry_used`;
check 4's add `binding_cap`, `leverage_binding`, `equity`, `fee_rate`, `fee_source`.

## Row and result

- `result`: `rejected` on any failure; else `resized` when ATL-126's `binding_cap` is anything but
  `strategist_suggestion`; else `approved`.
- `decision`: `advise_only` (autonomy 1), `awaiting_owner` (2, and 3–4 while
  `autonomy.max_enabled_level` is 2), `send_now` (3–4 once enabled). `null` when rejected.
  **`advise_only` is new** — migration `2026_10_30_500000_add_advise_only_to_risk_decisions`.
- `final_size` (contracts), `final_notional_usd`, `leverage`: null when rejected.
- `settings_version`: the `settings.version` read in this call.

## `risk.decision` envelope (`atlas.risk`)

```json
{"id": "…uuid…", "type": "risk.decision", "ts": "2031-03-12T15:00:00Z", "env": "demo",
 "source": "trading_core:risk", "correlation_id": "<cycle_id or proposal_id>",
 "payload": {"proposal_id": "…", "risk_check_id": "…", "settings_version": 7,
             "result": "resized", "decision": "awaiting_owner", "reason": "Resized by max_usdt_per_trade: 250 contracts, 1000.000 USD.",
             "failed_check": null, "final_size": "250", "final_notional_usd": "1000.000", "leverage": 3}}
```

## Where each input comes from

- Freshness: `read_snapshot(...).channel_at["tickers"]` vs settings `market_data_max_age_seconds`
  (TL's note: not `is_stale`, which counts candle pushes). Mark: `mark.markPx`, else `ticker.last`.
- Connection: an `okx` row for the env with `last_verified_at` set.
- ATR(1H): `atlas_quant.load_candles` (closed 1H bars, 120 back) + `wilder_atr_values(…, 14)`.
- Equity: newest `equity_curve.total_eq_usd`. Available: newest trading `balances_snapshots` row in
  the settle ccy (`avail_bal` × eq_usd/eq for non-USD). USDC/USD rate: the USDC balance row.
- Positions: the newest `positions_snapshots.taken_at`, `pos != 0`; notional = |pos| × ctVal ×
  ctMult × mark (inverse: no × mark).
- Used today / trades today: latest risk_check per proposal since Baghdad midnight, result not
  rejected, proposal status in `risk_rules.used_statuses` (sent and later).
- Loss limits: first equity point since Baghdad midnight / Monday + net deposits − current equity.
- Events: `economic_events` with importance ≥ 5 between now and now + blackout minutes.
- Fees: `risk_rules.fallback_taker_fee_rate` (no trade-fee storage yet; `fee_source` says so).

## Not built here

- The bot capital cap and whitelist (decision #250) — ATL-156 passes sizing equity and adds the
  whitelist to check 2.
- The `account/max-size` cross-check (needs OKX) and real trade-fee rates.
- Setting/clearing circuit breakers — this only reads `trading_state.breakers`.
- The internal HTTP `POST /risk/check` route and any caller that triggers `evaluate`.

`docs/DECISIONS.md` ("Risk Engine check rules the brief leaves open") lists every judgement call.
