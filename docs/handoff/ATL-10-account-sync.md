# ATL-10 — account sync: balances, positions and the equity curve

Code: `services/trading_core/src/trading_core/sync/account.py`, registered in
`trading_core.app.default_scheduler` as job `account` every `sync.account_interval_seconds` (60 s).
Tests: `services/trading_core/tests/test_account_sync.py`.

## When it runs

- Every run looks up `connections` where `exchange = 'okx'`, `env = ATLAS_ENV` and
  `last_verified_at IS NOT NULL`. None → it writes nothing, logs **one INFO line**
  (`account sync skipped: no verified connection`) and returns `{"skipped": true}`. Withdraw-enabled
  keys never have `last_verified_at` (ATL-8, decision #237), so they are never synced.
- The lookup happens every tick, so a key verified in Settings starts syncing within a minute, and
  one whose secret is changed (ATL-4 clears the verification columns) stops.
- Credentials are decrypted with `APP_KEY` (decision #235). Missing or wrong APP_KEY → recorded as
  `last_error` (`AppKeyMissing: …` / `DecryptError: …`), nothing else written.

## One run

1. Reads, in order: `account/balance`, `asset/balances`, `account/positions`, and only if the funding
   account holds a non-zero coin that is not USDT/USDC, `market/tickers?instType=SPOT`.
2. Writes, in **one transaction**, all with the same `taken_at` (the run's UTC time, µs precision):
   - `balances_snapshots`: one row per `account/balance.details[]` entry (`account = trading`) and
     one per `asset/balances[]` entry (`account = funding`). Zero balances that OKX lists are kept.
   - `positions_snapshots`: one row per `account/positions[]` entry. No open positions → no rows
     for that tick (see the two-minute version below). Unique key is
     `(env, inst_id, pos_side, mgn_mode, taken_at)`: in net mode one instrument can hold a cross
     and an isolated position at once, so readers must not assume one row per inst_id per tick.
   - `equity_curve`: upsert on `(env, ts)` with `ts` = `taken_at` truncated to the minute. A second
     run in the same minute overwrites that minute's point (latest wins); it never adds a row.
   - `sync_status` (env, stream **`account`**): `last_error = NULL`, `last_success_at` and
     `reconciled_at` = `taken_at`.
3. Any failure (OKX error code, HTTP error, network error after the client's retries, missing
   `totalEq`, decrypt) → nothing from that run is written; `sync_status.last_error` =
   `"<ExceptionClass>: <message>"`, e.g. `OkxError: OKX error 51000 on account/positions: …`
   (the OKX code is always in the text). `last_success_at`/`reconciled_at` keep the last good run.

## Field mapping (compare like for like)

| Atlas column | OKX source |
| --- | --- |
| trading `eq`, `avail_bal`, `frozen_bal`, `upl` | `account/balance` `details[].eq`, `availBal`, `frozenBal`, `upl` (row ccy) |
| trading `eq_usd` | `details[].eqUsd` — OKX's own figure, **never recomputed** |
| funding `eq`, `avail_bal`, `frozen_bal` | `asset/balances[]` `bal`, `availBal`, `frozenBal`; `upl` NULL |
| funding `eq_usd` | **computed** (OKX gives none): see assumption below |
| positions `pos`, `pos_side`, `avg_px`, `mark_px`, `liq_px`, `lever`, `upl`, `upl_ratio`, `margin`, `mgn_mode`, `c_time` | `account/positions[]` `pos`, `posSide`, `avgPx`, `markPx`, `liqPx`, `lever`, `upl`, `uplRatio`, `margin`, `mgnMode`, `cTime` (OKX `""` → NULL) |
| `equity_curve.total_eq_usd` | `account/balance.totalEq` **+** Σ funding `eq_usd` |
| `equity_curve.upl_usd` | `account/balance.upl` if OKX fills it; else Σ `positions[].upl` for USD-settled positions (settle ccy = position `ccy`, else 2nd segment of instId, in USDT/USDC/USD); `0` with no positions |

So for QA's real-demo check: `total_eq_usd − Σ funding eq_usd` (same tick) must equal OKX
`account/balance.totalEq`. With an empty funding account (the demo usually has none), the latest
`total_eq_usd` equals `totalEq` exactly.

### Phase 0 assumption: valuing the funding account in USD

- **USDT and USDC count 1:1 with USD.** No depeg adjustment in Phase 0
  (`account_sync.usd_stablecoins` in `config/defaults.yaml`).
- Any other coin = `bal × last` of SPOT ticker `<CCY>-USDT`, falling back to `<CCY>-USDC`
  (`account_sync.price_quotes`); the quote is valued 1:1.
- A coin with no such ticker gets `eq_usd = NULL`, is **left out** of `total_eq_usd`, is listed in the
  run's `unpriced` result and logged as a warning. It is still stored with its quantity.

## Endpoint

`POST /internal/sync/account` — HMAC-signed like every /internal route, empty body. Runs the job now
for `ATLAS_ENV` (waits for an in-flight scheduled run).

200:
```json
{"ok": true, "env": "demo", "taken_at": "2026-10-28T17:36:12.345000Z", "equity_ts": "2026-10-28T17:36:00Z",
 "trading_balances": 4, "funding_balances": 2, "positions": 2,
 "total_eq_usd": "100073.63620", "trading_eq_usd": "98702.43", "funding_eq_usd": "1371.20620",
 "upl_usd": "2.177", "unpriced": [], "skipped": false}
```
409 (no verified connection): `{"ok": false, "error": "no_verified_connection", "env": "demo"}`
502: `{"ok": false, "error": "account_sync_failed", "detail": "OkxError: OKX error 50113 on account/balance: Invalid Sign"}`

Decimals are strings. Laravel's "Re-sync now" button should call this through `App\Support\TradingCore`.

## Not built / left for others

- Bills, deposits and withdrawals (ATL-11; `bills_interval_seconds`). This job only stamps the
  `account` stream; ATL-11 should use its own stream name.
- No WebSocket or Redis Streams events (Phase 1).
- Old snapshot rows are never pruned (retention is a later Settings → Data feature).

## The two-minute version

A tick with zero positions writes no `positions_snapshots` rows, so "latest positions tick" can be
older than the latest sync. Readers should use positions at `max(taken_at)` only if that equals
`sync_status(stream 'account').last_success_at`; otherwise there are no open positions now.
`PortfolioService::positions()` on develop (ATL-12) uses `max(taken_at)` alone, so a position the
owner closes on OKX keeps showing until another position opens — flagged to the Tech Lead.
