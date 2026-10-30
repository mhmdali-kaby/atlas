# ATL-142 — the private OKX WebSocket (`trading_core.ws.private`)

For the Executor, reconciliation, circuit-breaker and kill-switch tasks.

## Two-minute version

- `PrivateFeed(env, engine, redis, *, credentials, publish=None, connect=private_connector, clock=None,
  url=None, rand=random.random)`; `default_private_feed(env, engine, app_key)` is what `create_app`
  starts (lifespan, only with the real scheduler; `TRADING_CORE_PRIVATE_WS=0` turns it off).
  `app.state.private_feed` holds it.
- `credentials` is a callable returning `OkxCredentials | None`; the default
  (`connection_credentials`) re-reads the env's **verified** connection on every connect, so a new key
  is picked up on the next reconnect and a cleared one stops the feed. With none it writes
  `connected=0` and retries every `private_ws.no_connection_retry_seconds` (60).
- Login: `{"op":"login","args":[{apiKey, passphrase, timestamp, sign}]}`, timestamp = Unix epoch
  **seconds**, sign = Base64(HMAC-SHA256(secret, ts + "GET" + "/users/self/verify")) — `login_args()`.
  Subscribe is sent only after `{"event":"login","code":"0"}`; a refused login closes and backs off.
- **websockets logs every frame at DEBUG, and the login frame holds the key and passphrase.**
  `private_connector` gives the socket its own protocol logger
  (`trading_core.ws.private.protocol`) filtered to INFO and up. If you open a private socket
  anywhere else, use `private_connector`, never `feed.default_connector`.
- Reconnect/ping/staleness come from `market_ws` (25 s text ping, jittered backoff, 30 s max age).
- **Reconciliation hook:** `feed.on_reconnected(cb)` — `cb(payload)` (sync or async) after every
  successful re-login on a new socket (not the first connect). Same payload is published as
  `ws.private.reconnected` on `atlas.system`: `{"connects": 2, "logged_in_at": "…Z"}`.

## Subscriptions (`private_ws.channels`, config/defaults.yaml)

`account`, `positions` (instType ANY), `balance_and_position`, `orders` (ANY), `orders-algo` (ANY),
`liquidation-warning` (ANY). `account` is extra to the task list: it is the only private channel
with `eqUsd`/`totalEq`, which the balance snapshot needs.

## What each push does

| channel | DB | events |
| --- | --- | --- |
| orders | upsert `orders` (via `order_store`, below): match `(env, cl_ord_id)`, then `(env, ord_id)`; unknown → insert `origin='external'`, `purpose='entry'`. Updates `ord_id, state, filled_sz (=accFillSz), sz, avg_px, fee, fee_ccy, lever, raw`. `fillSz>0` + `tradeId` → insert `fills` ON CONFLICT (env, inst_id, trade_id) DO NOTHING | `account.order` every push; `account.fill` only for a new fill row; `order.state_changed` on `atlas.orders` when the state moved (insert counts, from_state null) |
| orders-algo | upsert `algo_orders`: match `(env, algo_cl_ord_id)`, then `(env, algo_id)`; unknown → insert external. Existing rows get `algo_id, state, raw` only | `account.algo_order`; `algo_order.state_changed` on `atlas.orders` |
| positions | in-memory, first push per connection = full snapshot; pos 0 drops it | `account.position` `{channel:"positions", positions:[…]}` |
| account | in-memory latest balance | `account.balance` `{channel:"account", total_eq, upl, u_time, balances:[{ccy, eq, cash_bal, avail_bal, frozen_bal, eq_usd, upl}]}` |
| balance_and_position | none (posData has no mark/upl) | `account.balance` `{channel:"balance_and_position", event_type, balances:[{ccy, cash_bal}]}` and `account.position` `{…, positions:[{inst_id, inst_type, pos_side, mgn_mode, pos, avg_px}]}` |
| liquidation-warning | none | `system.liquidation_warning` on `atlas.system` `{positions:[position payload]}` |

OKX state mapping: `mmp_canceled` → `canceled`; anything not in the orders CHECK leaves `state`
unchanged. Algo kind: `oco` (or `conditional` with both legs) → oco; `conditional` with sl only → sl
(`trigger_px = slTriggerPx`, `ord_px = slOrdPx`, `-1` → null); tp only → tp; `trigger` → trigger.
Other algo types (move_order_stop, twap, iceberg) are published but not stored.

All envelopes: brief §3.2 shape, `source = "service:trading_core.private_ws"`, money as decimal
strings, ts ISO UTC. The OKX `uid` in the push `arg` is never forwarded.

Order payload (`account.order`, and the base of `order.state_changed`):

```json
{"order_id":"<uuid>","cl_ord_id":"atl…","ord_id":"900001","inst_id":"BTC-USDT-SWAP","side":"buy",
 "pos_side":"net","ord_type":"limit","state":"partially_filled","okx_state":"partially_filled",
 "px":"100.5","sz":"1","filled_sz":"0.1","avg_px":"100.4","fee":"-0.005","fee_ccy":"USDT",
 "origin":"atlas","u_time":"2026-10-30T09:00:00Z"}
```

`order.state_changed` adds `"from_state":"live","to_state":"partially_filled"`.
`account.fill`: `fill_id, order_id, trade_id, ord_id, cl_ord_id, inst_id, side, pos_side, fill_px,
fill_sz, fee, fee_ccy, exec_type, fill_pnl, ts`.

## Order persistence is shared: `trading_core.execution.order_store`

**Reconciliation should call these, not write its own upsert.** Plain functions, one transaction each:

- `upsert_order(engine, env, okx_row, now) -> OrderUpsert | None` — matches `(env, cl_ord_id)`, then
  `(env, ord_id)`; unknown → insert `origin='external'`; `fillSz>0` + `tradeId` → one `fills` row
  (deduped). None only for an unknown row with no size. `OrderUpsert`: `order_id, prev_state, state,
  filled_sz, origin, late, new_fill, state_changed`.
- `upsert_algo(engine, env, okx_row, now) -> AlgoUpsert` — `(env, algo_cl_ord_id)`, then `(env, algo_id)`.
  `algo_order_id` is None for types not stored.
- `order_payload(u)`, `fill_payload(u)`, `algo_payload(u)` build the event payloads (now with a
  `"late"` flag).
- **Late pushes:** a row whose `uTime` is older than the stored `raw.uTime` changes nothing but
  `ord_id`/`updated_at` (no state or filled_sz regression, `state_changed` false); its fill is still
  inserted. A REST row from `orders-history` goes through the same rule.
- **clOrdId is unique across envs** in the schema. An imported row whose clOrdId the other env
  already holds is inserted with `cl_ord_id` null (the id stays in `raw.clOrdId`); the other env's row
  is never touched.

## Snapshot ticks

Once a connection has both an `account` push and its first `positions` push, every change writes one
tick through **the REST sync's writer**, `trading_core.sync.account.write_tick(engine, env, now,
balance, positions, funding_rows=None, reconciled=False)`: trading `balances_snapshots`, the newest
stored **funding** rows re-stamped (`funding_rows=None`), `positions_snapshots`, `equity_curve`
(`upl_usd` from `sync.account.upl_usd` — account upl, else USD-settled positions only), and
`sync_status(stream=account).last_success_at` (not `reconciled_at`: `reconciled=False`). At most once
per `private_ws.snapshot_min_interval_seconds` (30); a skipped change is flushed by the 5 s stale check.
The events carry the live values in between.

## Freshness (Redis)

Hash `atlas:pws:<env>`: `last_msg_ms` (any frame, pongs included), `login_ms`, `connected` ("1"/"0"),
`<channel>_ms`. Read with `atlas_shared.market_data.read_private(redis, env)` →
`PrivateFeedSnapshot.is_stale(now)` (not connected, or silent > 30 s). The feed also publishes
`ws.private.stale` / `ws.private.fresh` on `atlas.system`.

## Not built here

Reconciliation itself, the Executor's own transitions (it should insert its row before sending so
the push finds it by clOrdId), linking a new algo row to its entry order (`order_id` stays null for
imports), and splitting a multi-message initial positions snapshot (assumed one message).
