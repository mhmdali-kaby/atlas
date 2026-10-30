# ATL-143 — the Executor: approved risk check → OKX order with a confirmed server-side stop

For the kill switch, reconciliation, circuit breakers, position Close, the approvals UI/API and the
Orchestrator (patch I), which all either call the Executor or have to agree with the rows it writes.

## Two-minute version

- `trading_core.execution.executor.execute(risk_check_id, approved_by=None, *, engine, redis_client=None,
  client_factory=None, app_key="", active_env=None, sleep=asyncio.sleep, monotonic=time.monotonic,
  now=None) -> ExecutionResult` is the **only** caller of `place_order`/`place_batch_orders`/
  `place_algo_order` in the repo (`tests/test_executor.py::test_only_execution_calls_the_order_wrappers`
  scans every `services/*/src` file; add your module under `trading_core/execution/` if it must place
  orders, e.g. the kill-switch flatten).
- Its only input is a `risk_checks` row. It never reads a proposal's size or leverage: size =
  `risk_checks.final_size`, leverage = `risk_checks.leverage`.
- It refuses — with **no HTTP request** — unless: result `approved|resized`; decision `send_now`, or
  `awaiting_owner` with `approved_by == "owner"` (`advise_only` never); the check is the newest for its
  proposal; proposal status `risk_checked|awaiting_owner`; settings `version` equals the check's
  `settings_version`; `trading_state.paused` false and no active breaker; a verified OKX connection
  (live + withdraw permission refused); the instrument exists; the proposal has a stop.
- **An owner approval that arrives after any settings save is refused with `settings_changed`.** The
  approval flow must re-run `risk.engine.evaluate` and execute the new check.
- Client ids are derived from the risk check UUID: `derived_id(letter, rc_id)` = letter + last 31 hex.
  `e` entry · `s` attached SL/TP (`attachAlgoClOrdId`, becomes the algo's `algoClOrdId` on fill) ·
  `f` bare entry after attach refused · `o` stand-alone OCO/SL · `c` emergency reduce-only close.
- The OKX client is built with `RetryPolicy(max_attempts=1)`; the Executor retries itself
  (`executor.order_max_retries`, backoff `retry_backoff_base_seconds` × 2^n capped at
  `retry_backoff_max_seconds`) and does `GET trade/order?clOrdId=` before every retry. 51016 →
  lookup and adopt.

## HTTP

`POST /internal/execute/{risk_check_id}` — HMAC-signed like every `/internal` route (401 otherwise).

Request body (optional):

```json
{"approved_by": "owner"}
```

Response (all statuses use this body; 200 sent · 409 refused · 422 bad body · 502 failed/unprotected):

```json
{"ok": true, "status": "sent", "reason": "sent", "detail": "Order sent; stop confirmed on OKX.",
 "risk_check_id": "6b1f…", "order_id": "0d3c…", "cl_ord_id": "eb1f…(32)", "ord_id": "900001",
 "state": "live", "stop_verified": true, "okx_code": null}
```

`status` / `reason` pairs:

| status | reason |
| --- | --- |
| refused | `risk_check_not_found`, `env_mismatch`, `risk_rejected`, `advise_only`, `owner_approval_required`, `decision_unknown`, `size_missing`, `superseded`, `already_executed` (carries order_id/cl_ord_id/ord_id/state), `proposal_not_found`, `proposal_not_executable`, `stop_missing`, `settings_missing`, `settings_changed`, `trading_paused`, `breaker_active`, `connection_not_verified`, `withdraw_permission_enabled`, `instrument_unknown`, `entry_missing` |
| sent | `sent` |
| failed | `okx_refused` (okx_code + human message in `detail`), `okx_unreachable`, `entry_canceled` |
| unprotected_closed | `stop_unverified` |

## What it writes

- `orders`: one entry row (`purpose=entry`, `origin=atlas`, proposal_id + risk_check_id) states
  `new → sending → live|partially_filled|filled|canceled|rejected|failed`; `last_error_code` =
  sCode, `last_error_msg` = human message. On attach refusal the same row's `cl_ord_id` changes
  from `e…` to `f…` and `raw.attach_refused` holds the code. Emergency close: a second row
  `purpose=close`, `reduce_only=true`, `cl_ord_id=c…`, `raw.reason=stop_unverified`.
- `algo_orders`: the attached stop as one row (`algo_cl_ord_id=s…`, kind `oco` when there is a TP1,
  else `sl` with `trigger_px`); state `new → live` (or `failed` when attach was refused). A
  stand-alone protection row `o…` the same way.
- `proposals.status → sent` once OKX accepted the entry.

## Events

All §3.2 envelopes, `correlation_id` = proposal.cycle_id or proposal id.

- `atlas.orders` `order.state_changed` (source `service:trading_core.executor`) — same base shape as
  ATL-142's private feed plus `purpose`, `reduce_only`, `proposal_id`, `risk_check_id`,
  `from_state`, `to_state` (`from_state` null on insert).
- `atlas.orders` `algo_order.state_changed` — `algo_order_id, order_id, algo_id, algo_cl_ord_id,
  inst_id, kind, side, sl_trigger_px, tp_trigger_px, sz, state, origin, from_state, to_state`.
- `atlas.orders` `order.refused` — the ExecutionResult payload, for the record (rule 9).
- `atlas.system` `system.alert` — `{"code": "stop_unverified", "detail": "...", "closed_sz": "250",
  inst_id, side, sz, stop, ...}`.
- `atlas.warroom` `warroom.message`, source `system:executor`, author `executor`, Main thread:
  kind `order` (result; card has order_id, proposal_id, risk_check_id, inst_id, side, ord_type, sz,
  px, stop, tp1, leverage, td_mode, cl_ord_id, ord_id, state, stop_verified / okx_code / error), and
  kind `system_alert` with the same detail as the system alert.

## Stop verification

Polls every `executor.stop_verify_poll_seconds` until `stop_verify_seconds` (settings, default 3):
confirmed when a pending `conditional,oco` algo for the instrument has our `s…`/`o…` id (or the
entry's ordId) and an `slTriggerPx`, or the entry is unfilled/partially filled and its
`attachAlgoOrds` shows an SL with no fail code. Not confirmed → cancel the entry (51400/51401/51402
ignored), re-read it, close `accFillSz` reduce-only market, alert. An entry OKX cancelled with no fill
ends `failed / entry_canceled` without an alert.

## Not built here

- Entry timeout (cancel unfilled limit after `entry_timeout_minutes`), resizing protection on partial
  fills, and the TP2/TP3 split — the attached algo covers the whole size at TP1.
- Who calls `execute`: the approval endpoint and the Orchestrator. Laravel calls the route with
  `App\Support\TradingCore` signing.
- Reconciliation of an order left in `sending` after a crash: calling `execute` again resumes (looks
  the `e…`/`f…` id up first and only sends if OKX has nothing).
