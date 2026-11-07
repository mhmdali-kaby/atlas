# ATL-185 — the Executor carries out approved PM position actions

For ATL-170 (daily report: which rule closed a trade), ATL-178 (War Room cards) and anyone touching
the Executor or EntryLifecycle.

## Two-minute version

- Nothing new to call. `execute(risk_check_id, approved_by)` (and so `POST /internal/execute/{rc}` and
  the owner's `POST /internal/proposals/{id}/execute`) now looks at the proposal's kind first: an
  `amend_stop`, `amend_targets`, `partial_close` or `close` goes to
  `trading_core/execution/position_action.py::execute_position_action`. Entries are unchanged.
- **The approval route re-runs `evaluate` first (ATL-162), then the Executor re-runs the same seven
  ATL-184 checks again on what OKX returns right then** (`GET account/positions` and
  `orders-algo-pending` for the instrument). The DB can lag OKX; OKX wins.
- **A re-check failure sends nothing**: a new `risk_checks` row (`result: rejected`, `decision: null`,
  the seven checks with the failing one's `code`, e.g. `stop_widens`), proposal → `rejected` /
  `status_reason: risk_rejected`, `proposal.rejected` on atlas.proposals, a War Room `order` line.
  The ExecutionResult is `{"status": "refused", "reason": "risk_rejected"}` → HTTP 409.
- **Done = proposal `filled`** (amend_stop, amend_targets, and a close/partial OKX reports filled).
  A close OKX accepted but has not reported filled within `executor.stop_verify_seconds` stays `sent`.
  A PM `partial_close` with `invalidation = "tp1_partial"` in `sent|filled` is ATL-169's tp1_filled;
  there is no separate flag.
- **A failed send leaves the proposal `risk_checked`**; the approval route then closes it `rejected` /
  `execution_okx_refused` (or `execution_okx_unreachable`). OKX's old stop is still in place in every
  failure path (the close/amend did not happen, so nothing was cancelled).

## What each kind sends

| kind | OKX calls, in order | notes |
| --- | --- | --- |
| amend_stop | `trade/amend-algos {algoId, newSlTriggerPx, newSlOrdPx: "-1"}` per live SL/OCO on the position | price rounded to tickSz toward the position (never wider than checked). No live stop on OKX → one `trade/order-algo` conditional SL for the whole position, `algoClOrdId = "n" + 31 hex of rc id` |
| amend_targets | OCO present: `amend-algos {newTpTriggerPx, newTpOrdPx: "-1"}`; none: `order-algo` TP-only, `"t"+31hex`; then `cancel-algos` for older TP-only orders | only the first target is used, like entries. The stop is never touched |
| partial_close | `trade/order {side: closing, ordType: market, sz, reduceOnly: true, clOrdId: "x"+31hex}`, then `amend-algos {newSz: remainder}` for every algo on the position larger than the remainder | sz = contracts from the OKX-side re-check (= rc.final_size when nothing moved) |
| close | `trade/close-position {mgnMode, posSide?, autoCxl: true, clOrdId: "x"+31hex}`, then `cancel-algos` for every algo on the position | a refused close cancels nothing |

Retries: up to `executor.order_max_retries`, Executor backoff, **same clOrdId**; before each retry
`GET trade/order?clOrdId=` and adopt it if OKX has it. A later run whose `orders` row is still
`new`/`sending` (a crash) looks the order up first and, if it landed, only finishes the protection
work (resizes from `raw.position_contracts`).

`OkxTradeClient.amend_algo` gained `new_sl_trigger_px` / `new_tp_trigger_px` (market on trigger);
`new_sz` is now optional. The size-only call EntryLifecycle makes is unchanged.

## Rows written

- `orders` (partial_close/close): `purpose: close`, `ord_type: market`, `reduce_only: true`,
  `origin: atlas`, `proposal_id` = the PM proposal, `risk_check_id` = its rc,
  `raw: {"position_action": "partial_close", "reduces_order_id": "<entry order uuid or null>",
  "position_contracts": "10"}`. `reduces_order_id` is the newest filled Atlas entry on the instrument
  in the position's direction (null for an external position).
- `EntryLifecycle` now targets `filled_sz − reduced_sz` (`lifecycle.reduced_sz()`: filled size of those
  close orders, or their whole size while `live`/`partially_filled`), so its sweep keeps the resized
  stop and stops re-settling an entry Atlas closed in full.
- `algo_orders`: amended rows get the new `trigger_px` (sl) / `sl_trigger_px` (oco) / `tp_trigger_px`
  / `sz`. Placed rows: kind `sl` (trigger_px = stop) or `tp` (trigger_px = tp_trigger_px = target),
  `raw.placed_as = "position_action:<kind>"`. Cancelled rows → `canceled`.

## Events

- atlas.orders `algo_order.amended`:
  `{"algo_order_id": "…", "proposal_id": "…", "algo_id": "900001", "algo_cl_ord_id": "s…",
  "inst_id": "XAU-USDT-SWAP", "kind": "conditional", "changes": {"trigger_px": "3950.01"}}`
- atlas.orders `order.state_changed` / `algo_order.state_changed` as for entries.
- atlas.proposals `proposal.status_changed`:
  `{"proposal_id": "…", "kind": "partial_close", "from_status": "risk_checked", "to_status": "filled",
  "order_id": "…"}` (no order_id for amends).
- atlas.proposals `proposal.rejected` (re-check): common proposal payload +
  `decided_by, risk_check_id, result: "rejected", reason, failed_check, code`.
- atlas.system `system.alert` codes: `position_unprotected` (partial close found no stop on OKX;
  reconciliation places one), `protection_resize_failed` (stop kept at the old size, still
  reduce-only), `position_action_cancel_failed` (closed, but an algo could not be cancelled).

## War Room card (kind `order`, author `executor`)

```json
{"type": "position_action", "proposal_id": "…", "risk_check_id": "…", "kind": "partial_close",
 "inst_id": "XAU-USDT-SWAP", "order_id": "…", "cl_ord_id": "x…", "sz": "5", "state": "filled",
 "resized": ["900001"], "remaining": "5", "ord_id": "700001"}
```

Other keys by kind: `new_stop` + `amended` (amend_stop), `new_tp` + `amended` + `cancelled`
(amend_targets), `placed` (a new SL/TP id), `cancelled` (close). Failure: `state: "failed"`, `okx_code`,
`error`. Re-check rejection: `state: "rejected"`, `code`, `failed_check`, `risk_check_id`.

## Not built

- `send_now` for position actions only happens at autonomy 3–4, which stay disabled; the path is the
  same.
- No new stop is placed after a partial close that found none: an alert is raised and reconciliation
  (ATL-146) places the emergency stop on its next run.
