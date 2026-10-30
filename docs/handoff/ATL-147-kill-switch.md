# ATL-147 — kill switch, pause/resume, one-position close, one-order cancel (trading_core)

For ATL-148 (Laravel system API, order cancel, position close), ATL-149 (kill-switch sheet and
paused/breaker banner), ATL-152 (demo integration: kill-switch flatten) and ATL-163 (slash commands).

## Two-minute version

- Code, under `trading_core/execution/`: `control_api.py` (routes), `trading_state.py` (pause,
  resume, `set_paused`), `kill.py` (the kill switch), `close_cancel.py` (one-position close, one-order
  cancel), `control.py` (the OKX reads/cancels/close with retries, the report, `ControlError`) and
  `okx_access.py` (stored connection, executor settings, single-attempt OKX client, bus publishers —
  shared with the Executor plan, `EntryLifecycle` and the recorder).
- After the first cancel pass the kill reads `orders-pending` **once more** and cancels any order that
  was not in the first read: the Executor checks paused only when it plans, so an execute that planned
  before the pause can place its entry while the kill is running.
- All five routes are under `/internal`, HMAC-signed exactly like `/internal/execute/{id}`
  (`X-Atlas-Timestamp`, `X-Atlas-Signature`; 401 otherwise). Laravel calls them with
  `App\Support\TradingCore` signing. They act on the env trading_core runs for (`ATLAS_ENV`).
- **Decision #251 is what kill does**: pause first; cancel every pending order and every algo order
  that has **no** stop leg (TP-only conditionals, trigger orders); **keep** SL conditionals and OCOs.
  With `flatten`, close each position (`trade/close-position`, `autoCxl: true`) and, only once that
  close is accepted, cancel that position's remaining algos. A position whose close failed keeps its stop.
- The kill reads what to cancel **from OKX** (orders-pending, orders-algo-pending for
  `conditional,oco` and `trigger`, account/positions), not from our DB, so it also catches orders the
  DB does not know. It marks matching `orders` / `algo_orders` rows `canceled`; the private feed
  overwrites them with OKX's final state.
- Retries: each failing request is tried up to settings `order_max_retries` (default 3) with the
  Executor's backoff. OKX's "already gone" answers (51400, 51401, 51402 for cancels; 51023 for a
  close) count as `already_done`, not failed.
- Kill **always** returns 200 with a report (even with failures: `ok: false`), because the pause has
  already happened and the owner needs the per-item outcome.
- Resume clears every **owner-clearable** breaker (all but `data_stale`, which clears itself after
  60 s of fresh data) and then unpauses.

## Routes

### `POST /internal/system/pause`

Body optional: `{"reason": "lunch"}` (string ≤ 500; default "Paused by the owner."). 200:

```json
{"ok": true, "paused": true, "reason": "lunch", "paused_at": "2031-03-12T15:00:00Z",
 "by": "owner", "active_breakers": [], "breakers_cleared": []}
```

### `POST /internal/system/resume`

No body. 200:

```json
{"ok": true, "paused": false, "reason": null, "paused_at": null, "by": "owner",
 "active_breakers": ["data_stale"], "breakers_cleared": ["okx_errors"]}
```

`active_breakers` non-empty after resume means a self-clearing breaker is still on: new orders stay
refused until it clears. The banner (ATL-149) should say so.

### `POST /internal/system/kill`

Body `{"flatten": false}` (bool; missing = false; anything else → 422 `invalid_flatten`). 200:

```json
{"ok": true, "env": "demo", "flatten": false, "at": "2031-03-12T15:00:00Z", "paused": true,
 "cancelled": 4, "cancelled_orders": 3, "cancelled_algos": 1, "already_done": 0,
 "closed": 0, "stops_kept": 2,
 "failed": [],
 "items": [
   {"kind": "order", "inst_id": "BTC-USDT-SWAP", "ref": "1001", "action": "cancelled", "attempts": 1},
   {"kind": "algo", "inst_id": "BTC-USDT-SWAP", "ref": "7001", "action": "cancelled", "attempts": 1, "algo_kind": "tp"},
   {"kind": "algo", "inst_id": "BTC-USDT-SWAP", "ref": "7002", "action": "kept", "attempts": 0, "algo_kind": "sl"},
   {"kind": "algo", "inst_id": "XAU-USDT-SWAP", "ref": "7003", "action": "kept", "attempts": 0, "algo_kind": "oco"}
 ]}
```

Item fields: `kind` order|algo|position|read, `inst_id`, `ref` (ordId / algoId / the close's clOrdId /
for `read` what could not be read), `action` cancelled|closed|kept|already_done|failed, `attempts`,
optional `code` (OKX sCode), `message` (OKX error in plain English, `trading_core.okx.error_messages`),
`pos_side` (positions), `algo_kind` sl|tp|oco|trigger. A failed row:

```json
{"kind": "order", "inst_id": "BTC-USDT-SWAP", "ref": "2002", "action": "failed", "attempts": 3,
 "code": "51410", "message": "<human message for 51410>"}
```

A `read` failure (OKX could not list orders/algos/positions after every retry) or no stored
connection is also a `failed` row; the env stays paused either way. With `flatten: true` the
`closed` count is the number of positions closed and `stops_kept` only counts stops of positions
that were not closed.

### `POST /internal/positions/{inst_id}/close`

Body optional: `{"pos_side": "net"|"long"|"short"}` (else 422 `invalid_pos_side`; omitted = every
position of that instrument). Closes with `trade/close-position` (reduce-only by nature), then cancels
that instrument+side's pending algos (SL, TP, OCO, trigger). Does **not** pause.

- 200 `{...report fields, "inst_id": "...", "status": "closed"}` (closed = 1, cancelled_algos = n)
- 404 `{"ok": false, "error": "no_position", "detail": "There is no open X position."}`
- 409 `{"ok": false, "error": "connection_missing", ...}`
- 502 `{... "status": "failed", "failed": [...]}`

### `POST /internal/orders/{order_id}/cancel`

`order_id` is our `orders.id` UUID.

An Atlas **entry** (`origin=atlas`, `purpose=entry`) is handed to `EntryLifecycle.cancel` (the app's
`app.state.entry_lifecycle`, so it shares the per-order lock with fills and the timeout sweep). It is
the same flow as the entry timeout: cancel by clOrdId (retried `order_max_retries` times), read the
order back, then
- nothing filled → the stand-alone protection pending on OKX is cancelled, every stored protection row
  (`s…`/`o…`/`p…`, including an attached `s…` row with no algoId) is marked `canceled`, the proposal
  goes to `cancelled`, and `atlas.orders` gets `order.cancelled`;
- a partial fill → exactly one stop is kept and amended to the filled size (duplicates cancelled), the
  proposal goes to `partially_filled`, and `order.cancelled` carries `protection` (`amended`, `in_sync`,
  `placed`, …).
Either way the War Room gets an `order` message, card `{"type": "entry_cancelled", ...}`.

Any other order is a plain `trade/cancel-order` by ordId (or clOrdId) with the same retries.

- 200 `{...report fields, "order_id": "...", "inst_id": "...", "status": "cancelled", "filled_sz": "2",
  "protection": {"status": "amended", "target_sz": "2", "algo_cl_ord_id": "s…", "canceled": ["o…"]}}`
  (`filled_sz`/`protection` are null for a non-entry order and for an unfilled entry's protection)
- 404 `order_not_found` (unknown id, other env, not a UUID) · 409 `order_not_open` (state not
  new/sending/live/partially_filled) or `connection_missing` · 502 `status: failed`.

## Events

All §3.2 envelopes, `env` = the env.

- `atlas.system` `system.trading_state` (source `service:trading_core.kill`) — the pause/resume
  payload above (without `ok`). Published by pause, resume and kill (kill's comes first, before any
  OKX request).
- `atlas.system` `system.kill` — the full kill report (with `paused: true`).
- `atlas.system` `system.position_close` / `system.order_cancel` — the close/cancel report.
- `atlas.warroom` `warroom.message`, source `system:kill_switch`, author `kill_switch`, Main thread:
  - kill: kind `system_alert`, `content_md` e.g.
    `**Kill switch.** Trading is paused.\nCancelled 3 order(s) and 1 take-profit/trigger order(s); kept 2 stop-loss order(s) on OKX.`
    (+ `Closed N position(s).` with flatten, + up to 10 failed lines), `card` =
    `{"type": "kill_switch", "code": "kill_switch", ...the report}`.
  - position close: kind `text` "Closed the X position at market on the owner's request." or, on
    failure, kind `system_alert` with card `{"type": "position_close", "code": "position_close_failed", ...}`.
  - order cancel: only on failure, kind `system_alert`, card `{"type": "order_cancel", "code": "order_cancel_failed", ...}`.
- Breaker clears on resume announce themselves (ATL-145's `system.circuit_breaker` cleared + War Room text).

## Not built here

- The Laravel side (ATL-148): the routes, audit log, its own rate limit for kill, and Portfolio orders.
- Spot holdings are not sold by flatten: `trade/close-position` is for margin/derivative positions.
- No `orders` row is written for the close-position order (it has no size in its answer); the private
  feed imports it as it fills.
- Nothing here places an opening order; `test_only_execution_calls_the_order_wrappers` still passes.
