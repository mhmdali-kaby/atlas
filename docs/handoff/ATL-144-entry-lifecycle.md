# ATL-144 — after the entry: protection sized to each fill, the entry timeout, fill status

For reconciliation (ATL-146), the kill switch (ATL-147), the demo integration suite (ATL-152), the
Portfolio Manager (ATL-169) and the War Room cards.

## Two-minute version

- `trading_core.execution.lifecycle.EntryLifecycle(engine, env, *, redis_client=None,
  client_factory=None, app_key="", now=None)`. One instance per process (`app.state.entry_lifecycle`);
  its per-order `asyncio.Lock`s serialise the WebSocket callback and the sweep.
- **Fills:** `PrivateFeed.on_order(cb)` is new — `cb(OrderUpsert)` (sync or async) after every stored
  `orders` push, once its events are out. `create_app` registers `lifecycle.on_order` on the real feed.
- **Sweep:** scheduler job `execution.lifecycle` every `executor.lifecycle_sweep_seconds` (30):
  `lifecycle.sweep() -> {"expired": [order_id…], "settled": [order_id…]}` (plus `"error":
  "connection_not_verified"` when there is no key). It (1) cancels limit/post_only Atlas entries still
  `live|partially_filled` `entry_timeout_minutes` after `orders.created_at`, and (2) re-settles any
  filled Atlas entry whose **stored** protection is not exactly one live row of `sz = filled_sz` —
  that is the restart recovery. With nothing to do it makes no HTTP request at all.
- `settle(order_id, trade=None) -> ProtectResult | None` and `expire(order_id, trade) -> bool` are public
  so reconciliation can call them with its own `OkxTradeClient`. `ProtectResult(status, target_sz,
  algo_cl_ord_id, canceled, detail)`; status `nothing | in_sync | amended | placed | skipped | failed`.
- New client call: `OkxTradeClient.amend_algo(inst_id, *, algo_id=|algo_cl_ord_id=, new_sz, req_id=None)`
  → `POST trade/amend-algos` with only `newSz` (no price fields, so stop/TP never move). Rate limit
  `trade/amend-algos` 20/2 s in `okx_rest.rate_limits`.

## Protection rules (what settle does)

1. target = `orders.filled_sz` (DB, monotonic — a late/replayed push never lowers it). 0 → nothing.
2. `GET trade/orders-algo-pending?ordType=conditional,oco&instId=` — ours = state `live|partially_effective`,
   has `slTriggerPx`, and `algoClOrdId` ∈ {`s`,`o`,`p`} + last 31 hex of the risk check id, or `ordId` = the entry's.
3. Keep the first by s → o → p → (ordId match). `sz != target` → amend. Already equal → **no write
   request** (a replayed fill sends nothing). Any other one of ours → `cancel-algos`, DB row `canceled`.
4. None on OKX → place a stand-alone stop `p` + 31 hex (`oco` when the stored row has a TP, else
   `conditional`), reduce-only, `sz = target`, at the stored s/o row's stop and TP. **Not** when the
   stored rows show the stop fired (`effective`/`partially_effective`), when the proposal has a
   `close`/`kill` order, or when every stored row is gone and one was `canceled` (someone removed it on
   purpose, e.g. flatten).
5. Proposal: entry `filled` → `filled`; filled_sz > 0 → `partially_filled` (only forward from
   risk_checked/awaiting_owner/sent/partially_filled).

A refused amend/place raises `system.alert` + War Room `system_alert` with code
`protection_resize_failed` / `protection_place_failed` / `protection_missing`; the sweep retries.

## Entry timeout (expire)

`cancel-order` by clOrdId (51400/51401/51402 = already gone, fine) → `GET trade/order` → DB synced
(`order.state_changed`). Still open on OKX → retried next sweep. Then:
- filled > 0: settle (stop stays at the filled size), proposal `partially_filled` (or `filled`).
- nothing filled: our pending s/o/p algos cancelled, stored rows `canceled`, proposal `expired`.

## Events

- `atlas.orders` `algo_order.resized` — `{algo_order_id, order_id, proposal_id, algo_id, algo_cl_ord_id,
  inst_id, from_sz, to_sz, sl_trigger_px, tp_trigger_px, filled_sz, order_sz}`.
- `atlas.orders` `algo_order.state_changed` (new `p` row, cancelled duplicates) and
  `order.state_changed` — the Executor's shapes (ATL-143).
- `atlas.orders` `order.expired` — `{order_id, proposal_id, cl_ord_id, ord_id, inst_id, side, sz,
  filled_sz, state, timeout_minutes}`.
- `atlas.proposals` `proposal.status_changed` — `{proposal_id, order_id, from_status, to_status,
  filled_sz, order_sz}`.
- `atlas.warroom` `warroom.message`, author `executor`, source `system:executor`:
  - kind `fill`, **one per new `fills` row** (any order, Atlas or external; dedupe is the fills
    unique key, so a replayed push posts nothing). Card:

```json
{"type": "fill", "fill_id": "…", "order_id": "…", "proposal_id": "…", "trade_id": "t1",
 "inst_id": "BTC-USDT-SWAP", "side": "buy", "size": "0.4", "price": "4000", "fee": "-0.002",
 "fee_ccy": "USDT", "exec_type": "M", "filled_sz": "0.4", "order_sz": "1", "purpose": "entry",
 "origin": "atlas", "ts": "2026-10-30T09:00:00Z"}
```

  - kind `order` with card `{"type": "entry_expired", …order.expired payload}` when an entry times out.

## Not built / for you

- I could not check OKX's exact behaviour for attached TP/SL on a partial fill against the live docs.
  The code does not depend on it: whatever OKX shows (attached `s` sized to the order, nothing yet, or
  both `s` and our `p`) converges on one stop of the filled size. ATL-152 should confirm on the demo.
- TP2/TP3 split: still one OCO at TP1 for the filled size.
- Reconciliation (ATL-146) should call `lifecycle.settle(order_id, trade)` for Atlas entries it repairs
  rather than placing its own stop for them, and must not treat `p…` algos as `external`.
