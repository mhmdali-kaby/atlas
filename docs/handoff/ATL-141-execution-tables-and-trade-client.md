# ATL-141 — orders, algo_orders, fills, trading_state + the typed OKX trade client

For every Executor task in patch 23 (Executor state machine, protection/stop check, reconciliation,
circuit breakers, kill switch, Orders page, position Close, demo integration suite).

## Two-minute version

- Migration `backend/database/migrations/2026_10_30_400000_create_execution_tables.php`; Python models
  `Order`, `AlgoOrder`, `Fill`, `TradingState` in `atlas_shared.db`. Prices/sizes/fees are
  `numeric(38,18)` → `Decimal`. Every state/kind/origin/purpose list is a Postgres CHECK.
- **Two CHECKs carry the brief's rules at the database**: an `origin='atlas'` order must have a
  `cl_ord_id` (rule 8), and an `origin='atlas'` **entry** must have both `proposal_id` and
  `risk_check_id` (rule 1). Protect/close/kill orders and external imports may leave them null.
- `trading_core.okx.trade.OkxTradeClient(okx_client)` wraps an authenticated `OkxClient` (same env,
  key, rate limiter, retries, demo header). Inputs are frozen dataclasses with `Decimal` fields
  (`OrderRequest`, `AttachedTpSl`, `AlgoOrderRequest`); a `float` raises `TypeError`.
- OKX refusals raise `OkxTradeError` (a subclass of `OkxError`) with `s_code`, `s_msg`,
  `human_message`, `cl_ord_id`, `ord_id`, `algo_id`. Show `human_message` in the War Room.
  Batch calls (`place_batch_orders`, `cancel_algos`) **do not raise per row**: they return every row;
  call `ack_error(row, path)` to get the typed error for a refused one.
- **Retries resend the identical body** (same `clOrdId`). If the first try reached OKX and the answer
  was lost, the retry comes back sCode `51016`; `err.duplicate_cl_ord_id` is True — then
  `get_order(inst_id, cl_ord_id=...)` instead of treating it as a failure. Build the `OkxClient` with
  `RetryPolicy(max_attempts=settings.order_max_retries, ...)` to honour the setting; the default is
  `okx_rest.max_attempts` (3).
- Network errors stay `OkxNetworkError` (not converted): the order may or may not exist on OKX —
  look it up by `clOrdId` before anything else.
- `ATLAS_OKX_FIXTURES=1` fixture mode still answers every POST with 405, so the Playwright/demo
  fixture runs can never place an order. Trade fixtures for tests live in
  `services/trading_core/tests/fixtures/okx_trade/`, deliberately outside the fixture-mode folder.

## Tables

All have `id uuid pk`, `env` (demo|live), `created_at`, `updated_at` (timestamptz(6)).

### orders

| column | type | notes |
| --- | --- | --- |
| proposal_id | uuid NULL → proposals (SET NULL) | required for atlas entries |
| risk_check_id | uuid NULL → risk_checks (SET NULL) | required for atlas entries |
| cl_ord_id | varchar(32) NULL **UNIQUE** | required when origin = atlas |
| ord_id | varchar(64) NULL | UNIQUE (env, ord_id) |
| inst_id | varchar(64) | |
| side | `buy, sell` | |
| pos_side | `net, long, short` NULL | |
| ord_type | `market, limit, post_only, fok, ioc, optimal_limit_ioc, mmp, mmp_and_post_only, op_fok, elp` | the extra OKX types are for imported external orders |
| td_mode | `isolated, cross, cash, spot_isolated` | |
| px | numeric NULL | null for market |
| sz | numeric | CHECK > 0; contracts (base ccy for spot) |
| filled_sz | numeric default 0 | CHECK 0 ≤ filled_sz ≤ sz |
| avg_px, fee | numeric NULL | fee in OKX sign (negative = paid) |
| lever | int NULL | CHECK ≥ 1 |
| fee_ccy | varchar(32) NULL | |
| state | `new, sending, live, partially_filled, filled, canceled, rejected, failed`, default `new` | |
| reduce_only | bool default false | |
| origin | `atlas, external`, default `atlas` | |
| purpose | `entry, protect, close, kill`, default `entry` | |
| last_error_code | varchar(16) NULL | OKX sCode |
| last_error_msg | text NULL | store `human_message` here |
| raw | jsonb default `{}` | last OKX order payload seen |

Indexes: (env, state), (env, inst_id, created_at), proposal_id.

### algo_orders

| column | type | notes |
| --- | --- | --- |
| order_id | uuid NULL → orders (SET NULL) | the entry it protects; null for external |
| algo_id | varchar(64) NULL | UNIQUE (env, algo_id) |
| algo_cl_ord_id | varchar(32) NULL **UNIQUE** | required when origin = atlas |
| inst_id | varchar(64) | |
| kind | `sl, tp, oco, trigger` | |
| side / pos_side | as orders, NULL | |
| trigger_px | numeric NULL | required unless kind = oco |
| sl_trigger_px, tp_trigger_px | numeric NULL | both required when kind = oco |
| ord_px | numeric NULL | null = market on trigger (OKX "-1") |
| sz | numeric NULL | CHECK > 0; null = whole position |
| state | `new, sending, live, pause, effective, partially_effective, canceled, order_failed, partially_failed, failed`, default `new` | ours + OKX's algo states |
| reduce_only | bool default **true** | |
| origin, last_error_code, last_error_msg, raw | as orders | |

An attached TP/SL (`attachAlgoOrds`) is one `algo_orders` row with `order_id` = the entry and
`algo_cl_ord_id` = the `attachAlgoClOrdId` you sent; fill `algo_id` when OKX reports it.

### fills

`order_id` uuid NULL → orders (SET NULL), `trade_id`, `ord_id`, `cl_ord_id` NULL, `bill_id` NULL,
`inst_id`, `side`, `pos_side` NULL, `fill_px`, `fill_sz` (CHECK > 0), `fee` NULL (negative = paid),
`fee_ccy`, `exec_type` (T/M) NULL, `ts` timestamptz, `raw` jsonb. **UNIQUE (env, inst_id, trade_id)**
— upsert on that. Indexes (env, ts), (env, ord_id).

### trading_state

Created here only if ATL-127 had not created it (`Schema::hasTable` guard). `env` UNIQUE, `paused`
bool default false, `reason` text NULL, `breakers` jsonb default `{}` (suggested
`{"daily_loss": {"active": true, "since": "…Z", "detail": "…"}}`), `paused_at` timestamptz NULL. No row
is seeded: treat a missing row as "not paused".

## Python models (`atlas_shared.db`)

`Order`, `AlgoOrder`, `Fill`, `TradingState` — field names = column names, `Decimal` for numerics,
server defaults for state/origin/purpose/filled_sz/reduce_only/raw. Test:
`services/shared/tests/test_execution_models.py`.

## Trade client (`trading_core.okx.trade`)

```python
trade = OkxTradeClient(client)          # client = await client_for_connection(...)  / OkxClient(env, creds)
trade.env                               # "demo" | "live"

await trade.place_order(OrderRequest) -> OrderAck                     # raises OkxTradeError
await trade.place_batch_orders(Sequence[OrderRequest]) -> list[OrderAck]   # 1..20, no raise per row
await trade.amend_order(inst_id, *, ord_id=None, cl_ord_id=None, new_sz=None, new_px=None,
                        req_id=None, cxl_on_fail=False) -> AmendAck
await trade.cancel_order(inst_id, *, ord_id=None, cl_ord_id=None) -> OrderAck
await trade.close_position(inst_id, mgn_mode, *, pos_side=None, ccy=None, auto_cxl=False,
                           cl_ord_id=None, tag=None) -> ClosePositionAck
await trade.get_order(inst_id, *, ord_id=None, cl_ord_id=None) -> OrderDetail
await trade.get_orders_pending(*, inst_type=None, inst_id=None, ord_type=None, state=None,
                               after=None, before=None, limit=None) -> list[OrderDetail]
await trade.get_orders_history(inst_type, *, inst_id=None, ord_type=None, state=None,
                               after=None, before=None, begin=None, end=None, limit=None) -> list[OrderDetail]
await trade.place_algo_order(AlgoOrderRequest) -> AlgoAck              # raises
await trade.cancel_algos(Sequence[tuple[inst_id, algo_id]]) -> list[AlgoAck]   # 1..10, no raise per row
await trade.get_algo_orders_pending(ord_type, *, inst_type=None, inst_id=None, algo_id=None,
                                    algo_cl_ord_id=None, after=None, before=None, limit=None) -> list[AlgoOrderDetail]
await trade.get_fills(*, inst_type=None, inst_id=None, ord_id=None, after=None, before=None,
                      begin=None, end=None, limit=None) -> list[FillDetail]          # last 3 days
await trade.get_fills_history(inst_type, *, ...same...) -> list[FillDetail]          # last 3 months
await trade.set_leverage(inst_id, lever: int, mgn_mode, *, pos_side=None) -> LeverageResult
await trade.get_max_size(inst_id, td_mode, *, ccy=None, px=None, leverage=None) -> MaxSize
```

Exactly one of `ord_id` / `cl_ord_id` where both appear (else `ValueError`). `limit` is checked
against `okx_rest.page_limits` (100 for the trade reads). `begin`/`end` take aware datetimes or epoch
ms. `get_algo_orders_pending` needs `ord_type` ("conditional", "oco", "trigger", or "conditional,oco").

Requests:

```python
OrderRequest(inst_id, td_mode, side, ord_type, sz: Decimal, cl_ord_id: str, px: Decimal | None = None,
             pos_side=None, reduce_only=False, tag=None, attach_algo_ords=(AttachedTpSl(...),))
AttachedTpSl(sl_trigger_px=None, tp_trigger_px=None, sl_ord_px=None, tp_ord_px=None,
             attach_algo_cl_ord_id=None, sl_trigger_px_type="last", tp_trigger_px_type="last", sz=None)
AlgoOrderRequest(inst_id, td_mode, side, ord_type: "conditional"|"oco"|"trigger", algo_cl_ord_id,
                 sz=None | close_fraction=None (exactly one), pos_side=None, reduce_only=True,
                 sl_trigger_px, sl_ord_px, tp_trigger_px, tp_ord_px, trigger_px, order_px, *_px_type, tag)
new_cl_ord_id(prefix="atl") -> str     # 32 alphanumerics; mint once per order, persist, reuse on retry
```

`*_ord_px=None` is sent as `"-1"` (market on trigger). Real wire body of the AC2 test:

```json
{"instId":"BTC-USDT-SWAP","tdMode":"isolated","side":"buy","posSide":"net","ordType":"limit",
 "sz":"0.12","px":"68000.5","clOrdId":"atlb1946ac9b2c64f2c8d0b6a1f3e9c7",
 "attachAlgoOrds":[{"attachAlgoClOrdId":"atlslatt01","slTriggerPx":"66900","slOrdPx":"-1",
   "slTriggerPxType":"last","tpTriggerPx":"70500.0","tpOrdPx":"-1","tpTriggerPxType":"last"}]}
```

Responses (`trading_core.okx.trade_models`, all Decimal): `OrderAck(cl_ord_id, ord_id, tag, s_code,
s_msg, ts, ok)`, `AmendAck(+req_id)`, `AlgoAck(algo_id, algo_cl_ord_id, cl_ord_id, s_code, s_msg, ok)`,
`ClosePositionAck(inst_id, pos_side, cl_ord_id)`, `OrderDetail(inst_type, inst_id, ord_id, cl_ord_id,
px, sz, ord_type, side, pos_side, td_mode, acc_fill_sz, fill_px, fill_sz, avg_px, state, lever, fee,
fee_ccy, pnl, reduce_only, cancel_source, attach_algo_ords: list[AttachedAlgo], c_time, u_time)`,
`AlgoOrderDetail(algo_id, algo_cl_ord_id, inst_id, ord_type, side, pos_side, sz, close_fraction,
state, sl/tp/trigger px fields, ord_id, …)`, `FillDetail(inst_id, trade_id, ord_id, cl_ord_id,
bill_id, side, pos_side, fill_px, fill_sz, fee, fee_ccy, exec_type, ts)`, `LeverageResult(lever,
mgn_mode, inst_id, pos_side)`, `MaxSize(inst_id, ccy, max_buy, max_sell)`.

## Error messages (`trading_core.okx.error_messages`)

`human_message(code, msg) -> str`; `MESSAGES` holds 59 codes (general 500xx, placement 510xx–512xx,
cancel 514xx, amend 515xx, query 51603, leverage 59000/59102). Unknown: `"OKX error <code>: <msg>"`.
`OkxTradeError.human_message` is already filled in.

## Settings keys (GET/PATCH `/api/settings`, defaults in `config/defaults.yaml` → `executor`)

| key | type | default | range |
| --- | --- | --- | --- |
| entry_timeout_minutes | int | 30 | 1..1440 |
| td_mode_perps | string | "isolated" | one of `executor.td_modes_perps` = isolated, cross |
| stop_verify_seconds | int | 3 | 1..60 |
| order_max_retries | int | 3 | 1..10 |
| emergency_stop_atr_mult | decimal string | "2.0" | > 0, ≤ 20 |

`td_mode_perps` is the first "choice" setting: `SettingsSchema::CHOICE_FIELDS` (field → config key of
its allowed list) is how to add another. In Python read `settings.data[key]`, falling back to
`DEFAULTS["executor"][key]` for rows saved before ATL-141. Spot always uses tdMode `cash` (brief §11).

Rate limits for every trade/account-write endpoint are in `okx_rest.rate_limits` (GET and POST
`trade/order` share one 60/2 s bucket).

## Not built here

The Executor itself, the order state-machine transitions and their `atlas.orders` events, the private
WebSocket, reconciliation, circuit-breaker logic, the kill switch, any Laravel Eloquent model or API for
these tables, and no `trading_state` row seeding.
