# ATL-11 — bills, deposits and withdrawals sync

Code: `services/trading_core/src/trading_core/sync/bills.py` (job) and
`services/trading_core/src/trading_core/sync/bill_types.py` (what each bill field means).
Registered in `trading_core.app.default_scheduler` as job **`bills`** every
`sync.bills_interval_seconds` (300 s). Tests: `services/trading_core/tests/test_bills_sync.py`.

## When it runs

Same guard as the account sync (ATL-10): an `okx` connection for `ATLAS_ENV` with
`last_verified_at` set, looked up every run. None → nothing called or written, one INFO line,
`{"env": "demo", "skipped": true, "reason": "no_verified_connection"}`.

## One run

1. `account/bills` (last 7 days), `limit=100`, paging with `after` = the last `billId` of the
   previous page until a page has fewer than 100 rows.
2. **First run only** (no `sync_status(env, 'bills').last_success_at` yet): also `account/bills-archive`
   (last 3 months), paged the same way. Later runs only read the 7-day endpoint.
3. `asset/deposit-history` and `asset/withdrawal-history`, `limit=100`, paging with `after` = the
   oldest `ts` (epoch ms) of the previous page. Read-only history; there is no withdrawal or transfer
   action anywhere.
4. One transaction:
   - `bills`: insert, `ON CONFLICT (env, bill_id) DO NOTHING` (bills never change). `bal_chg`, `fee`,
     `pnl` are the exact OKX decimals; OKX `""` → NULL (so a transfer bill has `fee` NULL, not 0).
     `type` and `sub_type` are stored raw.
   - `deposits_withdrawals`: upsert on `(env, kind, external_id)`; `kind` = `deposit` (external_id =
     `depId`) or `withdrawal` (external_id = `wdId`); `state` is the **raw OKX code** (`"2"` = success),
     updated on every run so a pending deposit becomes successful in place. `amount` = `amt`.
     The withdrawal's own network fee is not stored (no column).
   - `sync_status` (env, stream **`bills`**): `last_error = NULL`, `last_success_at` = `reconciled_at` = run time.
5. Any failure (OKX error, 5xx after the client's retries, network, decrypt) writes nothing but
   `sync_status.last_error` = `"<ExceptionClass>: <message>"`; `last_success_at` keeps the last good run.

## Bill semantics (`bill_types.py`, from the OKX v5 "Get bills details" docs)

| Figure | How |
| --- | --- |
| Funding net | Σ `bal_chg` where `type = "8"` (`FUNDING_FEE`); positive = received |
| Fees paid | −Σ `fee` over **all** bills (fee is a field, not a type); positive `fee` = rebate |
| Realised PnL | Σ `pnl` over all bills (excludes fees) |

These match `App\Support\Portfolio\BillType` (ATL-12): `1` Transfer, `2` Trade, `8` FundingFee.

## Endpoint

`POST /internal/sync/bills` — HMAC-signed, empty body; runs the job now (waits for a scheduled run in flight).

200:
```json
{"ok": true, "env": "demo", "synced_at": "2026-10-28T17:40:00.123456Z", "archive": false,
 "bills_fetched": 106, "bills_new": 0, "deposits": 1, "withdrawals": 1, "skipped": false}
```
`bills_fetched` = unique bills OKX returned this run; `bills_new` = rows actually inserted.
409: `{"ok": false, "error": "no_verified_connection", "env": "demo"}`
502: `{"ok": false, "error": "bills_sync_failed", "detail": "OkxHttpError: ... 503 ..."}`

## Not built

- No Laravel "Re-sync now" wiring (call it through `App\Support\TradingCore` like `/internal/sync/account`).
- No `bills-archive` re-read after the first success: if the first run fails midway nothing is
  written, so the next run retries the archive.
- No events on Redis Streams (Phase 1).
