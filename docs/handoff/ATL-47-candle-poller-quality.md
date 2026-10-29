# ATL-47 — Closed bars by REST polling, candle quality checks and gap repair

Code: `services/trading_core/src/trading_core/marketdata/` (`grid.py` bar grid + OKX→row mapping, `poller.py`,
`quality.py`, `api.py`). Tests: `services/trading_core/tests/test_marketdata.py`.
Config: `config/defaults.yaml` → `candle_poller`, `candle_grid`, `quality` (regenerate with `php artisan atlas:defaults`).

## The two-minute version

- Both are scheduler jobs inside trading_core, registered in `app.default_scheduler`: `candle_poller` (ticks every
  `candle_poller.tick_seconds` = 1 s) and `quality` (every `quality.interval_seconds` = 900 s, and once at start).
  The backfill, the poller and the gap repair now share **one `RateLimiter`** (public OKX limits are per IP).
- Only series whose `backfill_jobs` row for `candles:<bar>` is **`done`** are polled or checked. Funding/OI are not
  polled or checked here.
- **Poller**: for every bar, the latest close at `now − close_delay_seconds` (3 s). A close not yet handled is due;
  for each done series it calls `market/candles` with `before = newest stored ts` (bars newer than that only; older
  bars are never refetched), keeps confirmed bars, upserts with ATL-46's `upsert_rows`. Smallest bar first,
  `concurrency` 4. So a bar is written ≈ close + 3 s + ≤ 1 s tick + rate-limiter wait. First tick after start catches
  every bar up. If more than `page_limit` (100) bars are missing (process was down), only the newest page is fetched;
  the hole is a `gap` the checker repairs.
- **Checker**: per done series, the newest `quality.window_bars` (500) bars → `data_quality_issues` rows with
  `dataset = "candles:<bar>"`, then re-fetches every open `gap` from `market/history-candles`.

## Issue kinds (what lands in `data_quality_issues`)

| kind | one issue per | from_ts / to_ts | detail | repaired_at |
| --- | --- | --- | --- | --- |
| `gap` | run of missing grid slots between two stored bars | first / last missing bar open time | `{"missing_bars": 3, "repair_attempts": 1, "still_missing": 0}` | set when every slot has a bar after the REST re-fetch |
| `duplicate` | grid slot holding ≥ 2 rows (an off-grid ts beside the on-grid bar; the PK forbids identical ts) | slot open / latest ts in it | `{"rows": 2, "ts": ["…", "…"]}` | never; nothing deleted |
| `zero_volume` | run of consecutive bars with `vol = 0` | first / last bar | `{"bars": 6}` | never (stock perps have quiet hours) |
| `outlier` | bar whose close-to-close log return is > `outlier_sigma` (8) σ of the previous `outlier_window` (100) returns, needs `outlier_min_samples` (30) | the bar / the bar | `{"sigma": 10.6, "return": 0.0105, "window": 44, "prev_c": "…", "c": "…"}` | never; the bar is kept |

- Rerun safety: insert `ON CONFLICT (env, inst_id, dataset, kind, from_ts) DO UPDATE … WHERE repaired_at IS NULL`
  — an open issue gets its `to_ts`/`detail` refreshed (detail is merged with `||`, so `repair_attempts` survives),
  a repaired one is left alone. `detected_at` is the first detection.
- Outliers already flagged are left out of the rolling window; a spike that reverts on the next bar is **one**
  outlier (the reverting bar is not flagged and its return is not added to the window).
- A gap OKX itself does not have stays open with `still_missing > 0` and is not re-fetched after
  `quality.max_repair_attempts` (3) tries.
- No trailing gap: missing bars after the newest stored bar are the poller's job, not a gap.
- Grid: 1m/5m/15m/1H/4H on UTC; 1D opens at 16:00 UTC (OKX aligns 1D to UTC+8) — `candle_grid.offset_minutes`.

## POST /internal/quality/check (signed, like every `/internal` route)

Body `{"env": "demo", "inst_id": "BTC-USDT-SWAP"}`, both optional; same 422 / 409 bodies as `/internal/backfill/*`.

- With `inst_id` → **200**, run now:
  `{"ok": true, "env": "demo", "inst_id": "BTC-USDT-SWAP", "series": 6, "found": {"gap": 1, "duplicate": 0, "zero_volume": 2, "outlier": 0}, "new": 1, "repaired": 1, "repair_failed": 0}`
  (`found` counts this run's findings, `new` how many were not already recorded).
- Without → **202** `{"ok": true, "env": "demo", "inst_id": null, "started": true}` (starts the scheduled job;
  `started` false if a run is in flight). Laravel does not call this yet; ATL-48's re-sync could.

## Also in this task

- `backfill.depth_days."candles:5m"` is now 180 (was 30), regenerated into both defaults files; a new 5m job's
  `target_from` = `created_at − 180 days`. Existing 5m jobs keep their old `target_from`.
- `pause_jobs(engine, env, inst_id=…)` (and so `POST /internal/backfill/pause` with an `inst_id`) also pauses that
  instrument's currency OI job (`BTC` for `BTC-USDT-SWAP`) — the same set `start` creates for it. Note that this
  pauses OI for every BTC contract, since the series is per currency.

## Not built

- No polling or checks for funding, OI, long/short or taker volume.
- Duplicates are not cleaned up (an off-grid row is left in place); only gaps are repaired.
- Fixtures are hand-written respx mocks; the `before` semantics of `market/candles` were not re-checked against
  live OKX (no network in the build sandbox, same caveat as ATL-45).
