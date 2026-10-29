# ATL-46 — Resumable history backfill (candles, funding, open interest)

Code: `services/trading_core/src/trading_core/backfill/` (`plan.py` which jobs exist, `runner.py` the walk,
`api.py` the signed endpoints). Tests: `services/trading_core/tests/test_backfill.py`.
Config: `config/defaults.yaml` → `backfill.depth_days` (shared with the Data API, ATL-48) and `backfill_runner`.

## The two-minute version

- One `backfill_jobs` row per (env, inst_id, dataset). The runner walks OKX history **backwards from now**
  to `target_from`, one page at a time. Each page is upserted **in the same transaction** that moves the
  job's `cursor_ts` to that page's oldest ts and adds the page's distinct row count to `rows_written`.
  Kill the process anywhere and the next run starts at `after = cursor_ts`; no page newer than the cursor
  is ever fetched again.
- `done` = OKX returned no older data, or the walk reached `target_from`. ATL-47's poller keeps done jobs
  fresh from the newest end; the backfill never goes forwards.
- Candle rows are written only for **confirmed** bars (`confirmed = true`); the still-forming newest bar is
  left to ATL-47.
- Runs on the scheduler as job `backfill` (every `backfill_runner.interval_seconds` = 300 s, never
  overlapping itself). Each scheduled run first creates any missing jobs (so the first start of
  trading_core after the instruments sync backfills everything), but does **not** resume paused/failed
  jobs — only `POST /internal/backfill/start` does that.
- `concurrency` (4) jobs at once, all through one `OkxClient`, so one rate limiter: OKX's per-endpoint
  limits hold (history-candles 20 / 2 s). Jobs are claimed in `dataset_order`:
  `candles:1D, candles:1H, candles:4H, funding, open_interest, candles:15m, candles:5m, candles:1m` —
  every instrument's 1D and 1H before anybody's 1m.

## Which jobs exist

For each `instruments` row with `env` = the env, `state = 'live'`, and a market in
`backfill_runner.enabled_markets` (default `spot, crypto_perp, stock_perp, commodity_perp`; brief §14.5 —
dated futures and options are off; SWAPs with `asset_class = other` have no toggle and are skipped):

| dataset | for | `inst_id` | endpoint | depth (`backfill.depth_days`) |
| --- | --- | --- | --- | --- |
| `candles:1D` | every instrument | instId | market/history-candles, 100/page | null = full history |
| `candles:1H`, `candles:4H` | every instrument | instId | " | 730 |
| `candles:15m` | every instrument | instId | " | 180 |
| `candles:5m`, `candles:1m` | every instrument | instId | " | 30 |
| `funding` | SWAP | instId | public/funding-rate-history, `after` cursor, 100/page | null |
| `open_interest` | crypto SWAP/FUTURES | **bare ccy** (`BTC`) | rubik open-interest-volume, `period=1D`, `end` = cursor − 1 ms | null |

`target_from` = job creation time − depth days; a null depth stores `backfill_runner.full_history_floor`
(`2009-01-01T00:00:00Z`), which the Data API hides. Open interest uses OKX's per-currency rubik series (the only
OI history ATL-45 wraps); it is written to `open_interest` with `inst_id = 'BTC'` and only `oi_usd` set. OKX
offers that series for crypto currencies only, so stock and commodity perps have no OI history job.

## Job states

| status | meaning | set by |
| --- | --- | --- |
| `pending` | waiting to be claimed | created; `start` re-queues `paused`/`failed` |
| `running` | claimed by a worker, or left by a killed process (claimable again, resumes from `cursor_ts`) | runner |
| `done` | OKX had nothing older / reached `target_from` | runner |
| `failed` | a page still failed after the client's 3 attempts; `last_error` = `"<ErrorClass>: <message>"` (≤ 500 chars, e.g. `OkxHttpError: OKX HTTP 500 on market/history-candles`). Other jobs carry on. | runner |
| `paused` | `pause` endpoint; a working job stops before its next write (that page is rolled back and refetched on resume) | API |

## Progress

`rows_written` = distinct rows upserted by the backfill for that job (a re-sync after `done` is not
re-counted because done jobs are not re-run). `expected`/`percent` are computed by the Data API (ATL-48):
whole bars between `max(target_from, list_time)` and the job's `created_at`, `percent = rows_written /
expected`; null-depth datasets show no percent until `done`. A forming bar or a bar OKX never had (a gap)
means a done job can sit just under 100 % before the API's "done → 100.0" rule.

## POST /internal/backfill/start · POST /internal/backfill/pause

Signed like every `/internal` route (`X-Atlas-Timestamp`, `X-Atlas-Nonce`, `X-Atlas-Signature`); unsigned or
wrongly signed → 401 from the middleware. Body, both keys optional (what ATL-48 sends):

```json
{"env": "demo", "inst_id": "BTC-USDT-SWAP"}
```

`env` defaults to trading_core's active env; `inst_id` null/absent = every eligible instrument.

start → **202**:

```json
{"ok": true, "env": "demo", "inst_id": null, "instruments": 5, "jobs": 36, "created": 36, "resumed": 0, "started": true}
```

`jobs` = created + resumed (the number ATL-48 relays). `started` is false when a run is already in flight —
it picks up the new jobs itself. Running start twice creates nothing new (ON CONFLICT DO NOTHING on
env, inst_id, dataset). With an `inst_id`, only that instrument's jobs (plus its currency's OI job) are
created/resumed.

pause → **202** `{"ok": true, "env": "demo", "inst_id": "XAU-USDT-SWAP", "jobs": 7}` (jobs paused).

Errors: 422 `{"ok": false, "error": "invalid", "field": "env"|"inst_id"|"body", "detail": "…"}`;
409 `{"ok": false, "error": "env_not_active", "env": "live", "active_env": "demo"}`;
404 `{"ok": false, "error": "unknown_instrument", ...}` (start with an inst_id that is not live/eligible).

## Not built here / for ATL-47

- No forward fill, no quality checks, no repair: ATL-47. Repair can reuse `upsert_rows(conn, Model, rows)` —
  it dedupes on the primary key, sorts by it and chunks under 65,535 bind params.
- No long/short ratio or taker volume jobs (not in this task; their CHECK values exist).
- OKX's "1D" bar is used as-is (OKX aligns it to UTC+8); `1Dutc` would need a new `bar` CHECK value.
- Fixtures are ATL-45's hand-written ones; re-record on a networked run before trusting page shapes at scale.
- `backfill_jobs.rows_written` has no CHECK ≥ 0 (QA note on ATL-44); the runner only ever adds.
