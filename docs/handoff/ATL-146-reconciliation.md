# ATL-146 — reconciliation (`trading_core.execution.reconcile`)

For the kill switch (ATL-147), position Close, the Portfolio Orders page, the PM (patch J) and
anyone reading `algo_orders` / `orders` / `sync_status`.

## Two-minute version

- `reconcile(env, *, okx, engine, redis_client=None, now=None, breakers=None) -> ReconcileResult` —
  one run over an authenticated `OkxClient`. `reconcile_job(env, engine, app_key, gate, *,
  client_factory=None, redis_client=None, now=None)` is the scheduler job (`JOB = "reconcile"`,
  every `reconciliation.interval_seconds` = 300, first in `default_scheduler`, so it runs at startup).
- **Startup gate (rule 8):** `ReconcileGate` lives on `app.state.reconcile_gate`. Closed until the
  first run succeeds (or finds no verified connection). Until then `POST /internal/execute/{id}`
  answers **409** `{"ok": false, "status": "refused", "reason": "not_reconciled", "detail": "...",
  "risk_check_id": "..."}` and the owner-approval route `POST /internal/proposals/{id}/execute` answers
  **409** with the same body plus `"proposal_id": "...", "risk_check": null` (checked before the fresh
  risk check), both without touching OKX. It never closes again. An injected test scheduler
  without the job gets an open gate; pass `reconcile_gate=` to `create_app` to test it closed.
  **Any new route that calls `execute()` must check the same gate** with
  `execution.reconcile.not_reconciled_refusal(app.state)` (returns the refusal dict, or None).
- **Reconnect:** `create_app` registers `private_feed.on_reconnected(...)` → `scheduler.trigger("reconcile")`
  (skipped if a run is already in flight).
- Order/algo writes go through `execution.order_store` (same rules as the private WS); fills through
  `sync.history`'s writer (`fill_row` / `_upsert_fills`); balances/positions through
  `sync.account.fetch_account` + `write_snapshot`.
- `OkxTradeReadClient.get_rows(endpoint, params)` is new: a signed GET returning OKX's raw rows.

## One run

1. Balances + positions (account sync writer; stamps `sync_status(account).reconciled_at`).
2. `trade/orders-pending` (paged by ordId) → `upsert_order`; unknown → `origin=external`.
3. `trade/orders-algo-pending` for `conditional,oco` and `trigger` (paged by algoId) → `upsert_algo`.
4. DB orders in `live|partially_filled|new|sending` not pending → `GET trade/order` (ordId, else clOrdId)
   → `upsert_order` (OKX's final state). Not found: left untouched (an unsent entry stays resumable by
   `execute`). DB algos in `live|partially_effective|pause` not pending → `GET trade/order-algo`.
5. `trade/fills-history` per `sync.history_fill_inst_types`, since now − `fills_lookback_days` (3).
   A fill of an unknown order imports that order first (`GET trade/order`, external).
6. Per open position: mismatch check, then protection (below).
7. Mismatches → `Breakers(...).trip("reconciliation_mismatch", reason, {"mismatches": [...]}, key=...)`.
   key = sorted `inst:pos_side:okx_pos:db_pos` list, so the same mismatch never re-trips after the owner
   clears it.
8. `sync_status(env, stream="reconcile")`: `last_success_at` + `reconciled_at` = run time (or
   `last_error` on failure; the exception is re-raised).

## Mismatch rule

DB size = Σ stored fills on the instrument (posSide filter in long/short mode) with
`ts >= cTime − fill_ts_slack_seconds`; buy +, sell − (net); in long/short mode the opening side is +.
Compared with OKX `pos` exactly. Positions opened before the fills window are not compared.
A single fill that flips a net position (close + reopen in one fill) would read as a mismatch;
Atlas never does that (closes are reduce-only).

Mismatch entry: `{"inst_id", "pos_id", "pos_side", "okx_pos": "10", "db_pos": "4", "opened_at": "…Z"}`.

## Protection rule

Protected = a pending `conditional|oco` algo on the instrument with `slTriggerPx`, state
`live|partially_effective`, side = closing side (posSide must match in long/short mode). Skipped while an
Atlas order on the instrument was created, or is `new|sending` and was last updated, within
`protect_grace_seconds` (30). An older stuck `new|sending` row does not block protection.

Stop price (`plan_stop`): the stop of the Atlas proposal whose entry has an opening-side fill since
`cTime` (newest), if still below the mark (long) / above it (short); else
`emergency_stop_atr_mult` (settings, default 2.0) × ATR(1H) (Risk Engine's `_atr_1h`) from the mark
(`markPx`, else `last`, else `avgPx`), capped at `max_stop_pct` (settings, default 8) % of the mark; no
ATR → the cap. Rounded to tickSz toward the mark.

Placed as `trade/order-algo` `{"ordType": "conditional", "side": <close>, "posSide": <pos>, "tdMode":
<mgnMode>, "sz": "|pos|", "reduceOnly": true, "slTriggerPx": "...", "slOrdPx": "-1",
"algoClOrdId": "r" + 31 hex}`. The id is `protect_id(env, position, n)` = sha256 of
`env:inst:posId:cTime:n`; a row still `new|sending` resumes with its own id, used ids are skipped.

Row in `algo_orders`: `origin=atlas, kind=sl, trigger_px=stop, sz, side, pos_side, state
sending → live|failed`, `raw = {"reason": "reconcile_unprotected", "stop_source", "proposal_id",
"pos_id"}`.

## Events (source `service:trading_core.reconcile`, correlation_id null)

- `atlas.orders` `order.state_changed` / `algo_order.state_changed` — same payloads as the private WS
  (`order_store.order_payload` / `algo_payload` + from/to state). The placed stop: `algo_order_id`
  null, `kind sl`, `from_state sending`.
- `atlas.account` `account.fill` — `order_store.fill_payload` shape, one per new fill.
- `atlas.system` `system.alert`:

```json
{"code": "position_unprotected",
 "detail": "RC-USDT-SWAP long 10 had no stop-loss on OKX. Atlas placed one at 3960.01, at the stop of the proposal that opened it.",
 "inst_id": "RC-USDT-SWAP", "pos_id": "77…", "side": "long", "pos_side": "net", "sz": "10",
 "mark_px": "4050", "stop": "3960.01", "stop_source": "proposal", "proposal_id": "…", "atr_1h": "20",
 "algo_cl_ord_id": "r…", "algo_id": "99001", "placed": true, "okx_code": null, "error": null}
```

  `stop_source`: `proposal | emergency_atr | emergency_max_stop_pct`. On a refused placement
  `placed: false` with `okx_code`/`error`, and the detail says "Protect or close it now."
- `atlas.warroom` `warroom.message`, source `system:reconcile`, author `reconcile`, Main thread, kind
  `system_alert`, content = the detail, card = `{"code": "position_unprotected", ...same fields}`.
- The breaker announces itself (ATL-145's `system.circuit_breaker` + War Room message).

## Config (`config/defaults.yaml` → `reconciliation`)

`interval_seconds: 300`, `fills_lookback_days: 3`, `max_pages: 50`, `protect_grace_seconds: 30`,
`fill_ts_slack_seconds: 1`.

## Not built here

- Re-sizing a stop that covers less than the position (ATL-144 sizes protection to fills).
- Closing a position whose proposal stop is already crossed — it gets the emergency stop instead.
- Marking a never-sent `new|sending` order failed; it stays for `execute` to resume.
