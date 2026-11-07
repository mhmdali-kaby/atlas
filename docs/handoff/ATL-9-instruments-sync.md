# ATL-9 — instrument universe sync and the shared scheduler

## The scheduler (`trading_core/scheduler.py`) — ATL-10 adds its jobs here

```python
from trading_core.scheduler import Scheduler

scheduler.add("balances", DEFAULTS["sync"]["account_interval_seconds"], async_fn)  # fn() takes no args
await scheduler.run_now("balances")   # for a signed "sync now" route; returns fn's result, re-raises
```

- Each job runs once when the scheduler starts, then sleeps `interval_seconds`, forever. A failing
  run is logged and the loop carries on; the job itself must record the error in `sync_status`.
- A job never overlaps itself: the loop and `run_now` share a per-job `asyncio.Lock`, so a manual
  run waits for an in-flight scheduled one.
- Production jobs are registered in `trading_core.app.default_scheduler(settings)`. Add yours there.
  It builds one `make_engine()` (reads `DB_*`); reuse it rather than making another.
- The FastAPI lifespan starts it and stops it on shutdown. `TRADING_CORE_SCHEDULER=0` serves the API
  without the loops. Tests pass `create_app(settings, scheduler=my_scheduler)` with
  `Settings(..., run_scheduler=False)` and call the route; the route still uses `run_now`.
- DB writes are sync SQLAlchemy; run them with `asyncio.to_thread` so the loop isn't blocked
  (see `sync_instruments`).

## Endpoint

`POST /internal/sync/instruments` — HMAC-signed like every /internal route, empty body. Runs the
sync for the active env (`ATLAS_ENV`) and answers when it is done (~2 s against demo).

200:
```json
{"ok": true, "env": "demo", "fetched": 886, "upserted": 882, "delisted": 0,
 "by_type": {"SPOT": 569, "SWAP": 188, "FUTURES": 129},
 "by_asset_class": {"commodity": 17, "crypto": 752, "etf": 4, "index": 2, "other": 1, "stock": 106},
 "synced_at": "2026-10-28T17:29:02.870172Z"}
```
502: `{"ok": false, "error": "instruments_sync_failed", "detail": "OkxError: ..."}`.

## What is stored

- `instruments` upserted on (env, inst_id) for SPOT, SWAP and FUTURES (`instruments.inst_types` in
  `config/defaults.yaml`; OPTION is off in Phase 0). `created_at` is set once; `updated_at` and
  `last_seen_at` every run. Sizes and prices go in as the Decimals OKX sent (numeric(38,18); "0.0001"
  reads back equal to `Decimal("0.0001")`, padded with zeros).
- `asset_class`: `instruments.category_asset_class` maps instCategory ("1" crypto, "3" stock,
  "4" commodity); any other or missing category → `other`. A stock whose base symbol (baseCcy, or the
  first part of instFamily since OKX leaves baseCcy empty on swaps) is in
  `instruments.stock_class_overrides` gets that class (SPY/QQQ/IWM/TQQQ/SQQQ → etf, US100 → index).
- Rows of the fetched types that OKX no longer lists get `state = "delisted_local"`; nothing is
  deleted, and the row id is kept. If it comes back, the next sync restores OKX's state.
- If any type's fetch fails, nothing is written or delisted; `sync_status` (env, stream
  `instruments`) gets `last_error` and keeps the previous `last_success_at`. Success clears
  `last_error`. `reconciled_at` is left alone (it belongs to the account streams).
- Instruments with no lotSz/minSz/tickSz are skipped with a warning (the columns are NOT NULL). On
  demo these are OKX's `TESTING*` preopen placeholders: 4 of 886, one SWAP, so demo shows
  187 SWAP rows (OKX lists 188).
