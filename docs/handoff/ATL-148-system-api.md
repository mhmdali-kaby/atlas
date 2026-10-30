# ATL-148: Laravel system API, order cancel, position close, Portfolio orders list

This is for ATL-149 (kill-switch sheet and paused/breaker banner), ATL-150 (Portfolio → Orders page),
ATL-151 (Positions Close action) and ATL-163 (slash commands /pause /resume /kill /kill flatten).

## The short version

- Every route needs the Sanctum SPA session (`auth:sanctum`). A guest gets JSON `401`.
- **Active env.** The routes act on, and list, the env that trading_core runs for. Today that is
  `atlas_defaults.env.default` (`demo`), which is `App\Support\System\ActiveEnv::get()`. No route
  takes `?env=`.
- **The write routes forward to trading_core and return its JSON unchanged.** That covers
  pause/resume/kill, cancel and close, with trading_core's status code when it is 200, 404, 409,
  422 or 502 (ATL-147's shapes, repeated below). There are only two answers of our own:
  - `503 {"ok": false, "error": "trading_core_unavailable", "message": "…"}` when trading_core is
    down. Nothing reached OKX.
  - `502 {"ok": false, "error": "trading_core_error", "message": "…"}` for anything else, such as
    a 401 signature mismatch or a 500.
- **Audit.** Every write that gets past validation and the env lookup writes exactly one
  `audit_log` row, including a 503. The actions are `system.paused`, `system.resumed`,
  `system.killed`, `order.cancel_requested` and `position.close_requested`. A 422 or a 404 writes
  nothing.
- **Rate limits.**
  - Every `/api` route is now behind a shared per-user `throttle:api`, at 1200/min
    (`system_api.api_per_minute`).
  - Pause, resume and kill are **excluded** from it. They have their own `system-control` limiter
    at 30/min per user, so a flood of other requests can never make the kill switch answer 429.
  - Cancel and close use `trades-write`, at 30/min per user.
- **Kill's `confirm: "KILL"` is a constant the sheet sends.** The owner never types it
  (decision #251: Stop is one press, and close-all is a 1.5 s hold). Without it the API answers 422
  and sends nothing.

## `GET /api/system/health`

This route reads Postgres and Redis only and never calls trading_core, so it still answers when
trading_core is down. Poll it for the banner. 200:

```json
{"data": {
  "env": "demo",
  "as_of": "2026-10-31T10:00:00Z",
  "trading": {"paused": true, "reason": "Circuit breaker: okx_errors",
              "paused_at": "2026-10-31T09:55:00Z", "accepting_new_orders": false},
  "breakers": [
    {"name": "okx_errors", "reason": "3 OKX errors in the last 5 minutes; the latest: Insufficient balance.",
     "since": "2026-10-31T09:55:00Z", "self_clearing": false, "detail": {"count": 3}}
  ],
  "market_data": {
    "max_age_seconds": 30, "redis_available": true,
    "stale": ["ETH-USDT-SWAP"],
    "instruments": [
      {"inst_id": "BTC-USDT-SWAP", "last_msg_at": "2026-10-31T09:59:55Z", "age_seconds": 5, "stale": false},
      {"inst_id": "ETH-USDT-SWAP", "last_msg_at": "2026-10-31T09:58:00Z", "age_seconds": 120, "stale": true}
    ]},
  "private_ws": {"redis_available": true, "connected": true, "last_msg_at": "2026-10-31T09:59:58Z",
                 "login_at": "2026-10-31T09:50:00Z", "age_seconds": 2, "stale": false},
  "reconciliation": {"last_success_at": "2026-10-31T09:58:00Z", "reconciled_at": "2026-10-31T09:58:00Z", "last_error": null},
  "account_sync": {"last_success_at": "2026-10-31T09:59:30Z", "reconciled_at": "2026-10-31T09:59:30Z", "last_error": null}
}}
```

- `breakers` lists **active** breakers only, sorted by name. `self_clearing: true` is `data_stale`,
  which clears itself after 60 s of fresh data. Resume does not clear it, so after a resume the
  banner should say that new orders stay refused until data is fresh.
- `accepting_new_orders` = not paused and no active breaker. This is the Risk Engine's check 1.
- `market_data.instruments` covers `market_ws.always_subscribe` plus every instrument with an open
  position, an open order or a pending algo order. An instrument is `stale` when it has had no
  message for `max_age_seconds`, or has never had one.
- On a fresh install every time field is `null`, `paused` is false, `breakers` is `[]`, and
  `private_ws.connected` is false with `stale` true.

## `POST /api/system/pause`

Body (optional): `{"reason": "lunch"}`, a string of at most 500 characters. It is trimmed, and an
empty one is dropped, in which case trading_core uses "Paused by the owner.". 200 (trading_core's
answer):

```json
{"ok": true, "paused": true, "reason": "lunch", "paused_at": "2026-10-31T10:00:00Z",
 "by": "owner", "active_breakers": [], "breakers_cleared": []}
```

## `POST /api/system/resume`

No body. Decision #251 says resume needs a confirmation. That confirmation is the UI's job; the
API does not ask for one. 200:

```json
{"ok": true, "paused": false, "reason": null, "paused_at": null, "by": "owner",
 "active_breakers": ["data_stale"], "breakers_cleared": ["okx_errors"]}
```

## `POST /api/system/kill`

Body: `{"confirm": "KILL", "flatten": false}`.

- `confirm` must be exactly `"KILL"`. Otherwise the answer is 422 with
  `errors.confirm = ["Send confirm \"KILL\" to use the kill switch."]`.
- `flatten` is a boolean. It defaults to false; a non-boolean is 422 on `errors.flatten`.

200 always comes with the report, even when some items failed (`ok: false`), because the pause
has already happened:

```json
{"ok": true, "env": "demo", "flatten": false, "at": "2026-10-31T10:00:00Z", "paused": true,
 "cancelled": 4, "cancelled_orders": 3, "cancelled_algos": 1, "already_done": 0,
 "closed": 0, "stops_kept": 2,
 "failed": [],
 "items": [
   {"kind": "order", "inst_id": "BTC-USDT-SWAP", "ref": "1001", "action": "cancelled", "attempts": 1},
   {"kind": "algo", "inst_id": "BTC-USDT-SWAP", "ref": "7001", "action": "cancelled", "attempts": 1, "algo_kind": "tp"},
   {"kind": "algo", "inst_id": "BTC-USDT-SWAP", "ref": "7002", "action": "kept", "attempts": 0, "algo_kind": "sl"}
 ]}
```

A failed item looks like this:

```json
{"kind": "order", "inst_id": "BTC-USDT-SWAP", "ref": "2002", "action": "failed", "attempts": 3,
 "code": "51410", "message": "<plain-English OKX error>"}
```

When trading_core is down the answer is 503 `trading_core_unavailable`, and the audit row is still
written. In that case **nothing was paused or cancelled**. The sheet must say so plainly and must
not show a success state.

## `GET /api/portfolio/orders?status=open|algo`

Leaving `status` out returns both lists. Any other value is 422.

- `open` = orders in `new|sending|live|partially_filled`.
- `algo` = algo orders in `new|sending|live|pause|partially_effective`.

Both lists are newest first, at most 500 each. Every price and size is a decimal string with no
trailing zeros, never a float. 200:

```json
{"data": {
  "env": "demo",
  "orders": [
    {"id": "9d6c…uuid", "env": "demo", "inst_id": "BTC-USDT-SWAP", "side": "buy", "pos_side": "net",
     "ord_type": "limit", "td_mode": "isolated", "px": "65000.5", "sz": "2", "filled_sz": "0.5",
     "avg_px": null, "lever": 3, "state": "live", "purpose": "entry", "origin": "atlas",
     "reduce_only": false, "cl_ord_id": "e3f…", "ord_id": "123456", "proposal_id": "…uuid",
     "last_error_code": null, "last_error_msg": null, "can_cancel": true,
     "created_at": "2026-10-31T09:40:00Z", "updated_at": "2026-10-31T09:41:00Z"}
  ],
  "algo_orders": [
    {"id": "…uuid", "env": "demo", "order_id": "9d6c…uuid", "inst_id": "BTC-USDT-SWAP", "kind": "sl",
     "protects": true, "side": "sell", "pos_side": "net", "trigger_px": "64000", "sl_trigger_px": null,
     "tp_trigger_px": null, "ord_px": null, "sz": "1.5", "state": "live", "origin": "atlas",
     "reduce_only": true, "algo_id": "7002", "algo_cl_ord_id": "s3f…", "last_error_code": null,
     "last_error_msg": null, "created_at": "…Z", "updated_at": "…Z"}
  ]
}}
```

- `sz` is in contracts. On an algo order, `sz: null` means the whole position and `ord_px: null`
  means a market order on trigger.
- `kind` is one of `sl|tp|oco|trigger`. An OCO carries `sl_trigger_px` and `tp_trigger_px`;
  the other kinds carry `trigger_px`.
- `protects` is true for `sl` and `oco`. Those are the stops the kill switch keeps, so show them
  as protection.
- There is no cancel route for algo orders. Show them read-only.

## `POST /api/orders/{id}/cancel`

`{id}` is `orders.id` (a UUID). There is no body.

- An id that is not a UUID, is unknown, or belongs to **the other env** gets
  `404 {"ok": false, "error": "order_not_found", "message": "There is no such order in this environment."}`.
  Nothing is sent and nothing is audited.
- Otherwise the call is forwarded to `/internal/orders/{id}/cancel`, and trading_core's answer is
  passed through:
  - 200: `{...report fields, "order_id": "…", "inst_id": "…", "status": "cancelled", "filled_sz": "2", "protection": {"status": "amended", "target_sz": "2", "algo_cl_ord_id": "s…", "canceled": ["o…"]}}`.
    `filled_sz` and `protection` are null for a non-entry order. A partly filled entry keeps a stop
    sized to what filled.
  - 409: `{"ok": false, "error": "order_not_open" | "connection_missing", "detail": "…"}`.
  - 404: `order_not_found` from trading_core. This is rare: the order is gone there.
  - 502: `{... "status": "failed", "failed": [...]}`.

## `POST /api/portfolio/positions/{id}/close`

`{id}` is the `id` that `GET /api/portfolio/positions` returns for the item (a
`positions_snapshots` row). The body must be `{"confirm": true}`; without it the answer is 422 on
`errors.confirm`.

- An id from the other env, or an unknown one, gets `404 {"ok": false, "error": "position_not_found", …}`.
  Nothing is sent.
- Otherwise the call is forwarded to `/internal/positions/{inst_id}/close` with
  `{"pos_side": "<snapshot's pos_side>"}`. That closes at market (reduce-only) and cancels that
  side's algo orders. It does **not** pause trading. trading_core's answers:
  - 200 `{...report fields, "inst_id": "XAU-USDT-SWAP", "status": "closed", "closed": 1, "cancelled_algos": n}`
  - 404 `{"ok": false, "error": "no_position", "detail": "There is no open X position."}`: the
    position was already closed.
  - 409 `connection_missing`
  - 502 `status: failed`: the close failed and the stop is still on OKX.

## Code

- `routes/api/system.php`, `routes/api/orders.php`, and `routes/api/portfolio.php` (the orders list
  and the close).
- Controllers: `System\SystemController` (health, pause, resume, kill),
  `System\TradeActionController` (cancel, close) and `Portfolio\OrdersController` (the list).
- `App\Support\System\ControlCall` is the signed POST plus the status mapping.
  `App\Support\System\SystemHealth` builds the health answer. `App\Support\System\ActiveEnv` gives
  the active env.
- New model `App\Models\AlgoOrder`. `Order::OPEN_STATES` and `AlgoOrder::OPEN_STATES` hold the open
  states.
- Authorisation: the gates `control-trading` and `close-position`, and `OrderPolicy::cancel`. Any
  signed-in user is the owner.
- Strings are in `lang/en/system.php`.

## Not built here

- Cancelling a single algo order.
- An `?env=` override.
- Any Reverb broadcast. The kill, pause, close and cancel events already go out on `atlas.system`
  and `atlas.warroom` from trading_core (see ATL-147's handoff). The UI should refresh health and
  orders on those events, or poll.
