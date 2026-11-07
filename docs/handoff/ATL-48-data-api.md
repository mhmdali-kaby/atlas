# ATL-48 — Settings → Data API

Routes: `backend/routes/api/data.php`. Controller `App\Http\Controllers\Data\DataController`; report builders
`App\Support\Data\BackfillReport` and `StorageReport`; issue shape `App\Http\Resources\DataQualityIssueResource`.
Tests: `backend/tests/Feature/Data/DataApiTest.php`.

All four endpoints need the Sanctum SPA session (guest → 401). Reads come from Postgres only.

## The two-minute version

- `percent` is a **decimal string with one decimal** (`"50.0"`), like the Portfolio API's percents. `null` means
  "no percent to show" — draw rows so far and no bar, never an estimate.
- `depth_days: null` = full history ("as far back as OKX gives"). For those datasets `target_from`, `expected`
  and `percent` are all `null` until `status` is `done`; then `expected = rows_written` and `percent = "100.0"`.
- Depths live in `config/defaults.yaml` → `backfill.depth_days` (1m 30, 5m 30, 15m 180, 1H 730, 4H 730,
  1D/funding/open_interest/long_short_ratio/taker_volume null). "2 years, from 29 Oct 2024" =
  `depth_days` 730 + `target_from`.
- An instrument row exists only once trading_core has created backfill jobs for it. Rows for the rubik
  datasets use a bare ccy as `inst_id` (`"BTC"`) and `asset_class: null`.
- Re-sync is limited to **1 per minute per user** (`data_api.resync_per_minute`), pause to 5. The 429 is
  Laravel's default throttle body; disable the button for 60 s after a success.

## GET /api/data/backfill?env=demo

`env` optional (`demo`|`live`, default `demo`); anything else → 422 `{errors: {env: [...]}}`.

```json
{
  "data": {
    "env": "demo",
    "as_of": "2026-10-29T12:00:00.000000Z",
    "instruments": [
      {
        "inst_id": "BTC-USDT-SWAP",
        "asset_class": "crypto",
        "open_issues": 1,
        "datasets": [
          {
            "dataset": "candles:1H",
            "status": "running",
            "depth_days": 730,
            "target_from": "2024-10-29T12:00:00.000000Z",
            "cursor_ts": "2025-10-29T12:00:00.000000Z",
            "rows_written": 8760,
            "expected": 17520,
            "percent": "50.0",
            "last_error": null,
            "updated_at": "2026-10-29T12:00:00.000000Z"
          },
          {
            "dataset": "funding",
            "status": "running",
            "depth_days": null,
            "target_from": null,
            "cursor_ts": null,
            "rows_written": 300,
            "expected": null,
            "percent": null,
            "last_error": null,
            "updated_at": "2026-10-29T12:00:00.000000Z"
          },
          {
            "dataset": "open_interest",
            "status": "done",
            "depth_days": null,
            "target_from": null,
            "cursor_ts": "2024-01-02T00:00:00.000000Z",
            "rows_written": 1200,
            "expected": 1200,
            "percent": "100.0",
            "last_error": null,
            "updated_at": "2026-10-29T12:00:00.000000Z"
          }
        ]
      }
    ],
    "totals": {
      "instruments": 2,
      "jobs": 5,
      "by_status": { "pending": 1, "running": 2, "done": 1, "failed": 1, "paused": 0 },
      "rows_written": 10860,
      "percent": "20.0"
    },
    "quality": {
      "open": 1,
      "open_by_kind": { "gap": 1, "duplicate": 0, "zero_volume": 0, "outlier": 0 }
    },
    "quality_issues": [
      {
        "id": "9d7c…uuid",
        "kind": "outlier",
        "inst_id": "BTC-USDT-SWAP",
        "dataset": "candles:1m",
        "from_ts": "2026-10-02T00:00:00.000000Z",
        "to_ts": "2026-10-02T00:01:00.000000Z",
        "detected_at": "2026-10-25T12:00:00.000000Z",
        "repaired_at": "2026-10-26T12:00:00.000000Z",
        "detail": { "sigma": 9.2 }
      }
    ]
  }
}
```

Field rules:

- `instruments` sorted by `inst_id`; `datasets` in the fixed order `candles:1m, 5m, 15m, 1H, 4H, 1D, funding,
  open_interest, long_short_ratio, taker_volume` (only the ones that have a job).
- `status`: `pending | running | done | failed | paused`. `last_error` is trading_core's text/slug for a failed job.
- `cursor_ts`: oldest bar written so far (the backfill walks backwards from now); null before the first page.
- `expected` (candles with a depth): whole bars between `max(target_from, instrument list_time)` and the job's
  creation time, so a recently listed instrument is not stuck below 100 %. `percent = rows_written / expected`,
  capped at `"100.0"`. Done → always `"100.0"`.
- A non-candle dataset given a depth in config later: `expected` null, `percent` = how far `cursor_ts` has
  walked from creation back to `target_from`.
- `totals.percent` = share of jobs that are `done` (null when there are no jobs). `totals.rows_written` sums every job.
- `quality.open` counts issues with `repaired_at` null; `instruments[].open_issues` is the same per instrument.
- `quality_issues`: every open issue plus every issue repaired in the last **7 days** (`data_api.repaired_issue_days`);
  one repaired 8 days ago is not listed. Newest `detected_at` first, at most 500 (`data_api.quality_issue_limit`).
  `kind`: `gap | duplicate | zero_volume | outlier`. `detail` is free-form jsonb from ATL-47 or null
  (e.g. `{"missing_bars": 5}`, `{"sigma": 9.2}`); show it as secondary text only.

## GET /api/data/storage

```json
{
  "data": {
    "hypertables": [
      { "name": "candles", "bytes": 245760 },
      { "name": "funding_rates", "bytes": 24576 },
      { "name": "open_interest", "bytes": 24576 },
      { "name": "long_short_ratio", "bytes": 24576 },
      { "name": "taker_volume", "bytes": 24576 }
    ],
    "total_bytes": 344064
  }
}
```

`bytes` = TimescaleDB `hypertable_size()` (all chunks, indexes and TOAST); not per env. Format with the
locale's byte units on the client.

## POST /api/data/resync · POST /api/data/pause

Body (both optional): `{"env": "demo", "inst_id": "BTC-USDT-SWAP"}`. No `inst_id` = every instrument.
`inst_id` must match `^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$`, ≤ 64 chars, else 422.

Laravel sends a signed `POST /internal/backfill/start` (or `/internal/backfill/pause`) to trading_core with body
`{"env":"demo","inst_id":"BTC-USDT-SWAP"}` (`inst_id` may be `null`), and writes an `audit_log` row
(`data.resync_requested` / `data.pause_requested`, subject_type `backfill_jobs`, `after` = env, inst_id, ok, error)
for every attempt, successful or not.

202:

```json
{ "ok": true, "env": "demo", "inst_id": "BTC-USDT-SWAP", "trading_core": { "ok": true, "jobs": 3 } }
```

`trading_core` is ATL-46's response body relayed as-is (object; `{}` if it returned none).

502 (trading_core answered non-2xx, or could not be reached):

```json
{ "ok": false, "error": "trading_core_error", "message": "The trading service could not carry out the request. Try again." }
```

`error` is `trading_core_error` or `trading_core_unavailable`; `message` comes from `backend/lang/en/data.php`.
For the Arabic UI, map the `error` slug in the frontend's own locale files rather than showing `message`.

429: a second re-sync within 60 s (pause: a 6th within 60 s).

## For ATL-46 (trading_core)

`/internal/backfill/start` and `/internal/backfill/pause` must accept that JSON body. Create jobs with
`target_from = created_at − backfill.depth_days[dataset]` from `atlas_shared.defaults`; for null depths store
any far-past floor you like — the API hides it.

## Not built here

No per-dataset re-sync, no retention rules, no "storage used" per env, no live push of progress (poll the
backfill endpoint, e.g. every 5 s while any job is `running`).
