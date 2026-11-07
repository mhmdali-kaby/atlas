# ATL-61 — economic calendar, earnings and Fear & Greed collectors

Four collector files in `services/ingest/src/atlas_ingest/collectors/`, registered by exact key.
They write structured tables, **not** `news_items`, and return `[]` to the pipeline.

| source key | file | writes | upsert key |
|---|---|---|---|
| `forexfactory.calendar` | `forexfactory.py` | `economic_events` | (source, external_id) |
| `finnhub.calendar` | `finnhub_calendar.py` | `economic_events` | (source, external_id) |
| `finnhub.earnings` | `finnhub_earnings.py` | `earnings_events` | (symbol, date) |
| `alternative_me.fng` | `alternative_me.py` | `market_sentiment` (index_key `crypto_fng`) | (index_key, ts) |

The shared row types and upserts are in `atlas_ingest/calendar.py`.

## The two-minute version

- **external_id** = sha256 hex of `country|name|scheduled_at (UTC isoformat)`. A release that is
  rescheduled gets a new row; the old one stays.
- **Importance** comes from `news.calendar_importance` (High 5, Medium 3, Low 1, Holiday 0),
  case-insensitive, so Finnhub's `high/medium/low` use the same map. **0 means not stored**: the
  column is CHECKed 1..5, and holidays are not events. An unknown label gets
  `news.calendar.unmapped_importance` (1).
- **actual / forecast / previous** are kept as published text (`"245K"`, `"0.3%"`; Finnhub values
  get their `unit` appended). `surprise` = actual - forecast when both parse as one number
  (`K/M/B/T` suffixes multiply, `%` is dropped). A later poll with no actual keeps the stored one
  (COALESCE), so a feed that drops a field never erases it.
- **Updates only when something changed** (`ON CONFLICT DO UPDATE ... WHERE col IS DISTINCT FROM
  new`), so `RETURNING` counts real changes. That count goes into the new
  `CollectContext.stats.written`, and the runner sets `last_item_at` when it is > 0.
- Every batch is de-duplicated on its key (last occurrence wins) and sorted before the upsert, in
  chunks of 1000.
- **Finnhub replaces ForexFactory**: when `finnhub.calendar` is enabled, has an `api_key`, and is
  not degraded, `forexfactory.calendar` makes no request (it records an empty success). When
  Finnhub is degraded, ForexFactory covers.
- **Cadence**: both calendar collectors implement the new optional hook
  `next_poll_seconds(source, ctx)`. It returns `fast_poll_seconds` (60) while any stored event with
  importance >= `fast_min_importance` (1) is within ±`fast_window_minutes` (10) of now; otherwise
  `poll_seconds` (900), shortened so the next poll lands when the next event's window opens
  (never below 60).
- **Earnings** makes one `from..to` call (`days_back` 3, `days_ahead` 14) and keeps only symbols in
  `ctx.stock_symbols()` (stock/ETF perp underlyings). With no stock perps synced, it makes no request.
- Finnhub keys go in the `X-Finnhub-Token` header only.

## Runtime changes (small, in shared files)

- `CollectContext` gained `engine: Engine | None` and `stats: PollStats` (`written: int`).
- `IngestRunner._run` passes `engine`, calls `next_poll_seconds` after a successful poll when the
  collector has it, and records `inserted + stats.written`.

## Defaults added (`config/defaults.yaml` → `news`)

`calendar_importance`, `calendar.{unmapped_importance, fast_poll_seconds, fast_window_minutes,
fast_min_importance, finnhub_days_back, finnhub_days_ahead}`, `earnings.{days_back, days_ahead}`,
`sentiment.{fng_index_key, fng_limit}`.

## Not done / not verified

- Fixtures in `tests/fixtures/calendar/` are written from the providers' documented shapes, not
  recorded (no network in the build). Re-record on a networked run.
- A Finnhub 429 is an ordinary `HTTP 429` failure here. ATL-60 adds `RateLimited` /
  `retry_after_seconds`; once both are on develop the two Finnhub collectors here should raise it.
