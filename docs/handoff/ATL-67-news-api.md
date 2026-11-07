# ATL-67 — read-only news, calendar and sentiment API

Routes: `backend/routes/api/news.php`. Controller: `App\Http\Controllers\News\NewsController`.
All `GET`, `auth:sanctum` (401 JSON when not signed in), no `env` (news is shared by demo and live).
The API reads Postgres only. Titles are returned as plain JSON strings and never rendered (brief §1.7).
Every timestamp is UTC ISO 8601 with a `Z`.

## The two-minute version

- Dates (`since`, `until`, `from`, `to`) must be strict ISO 8601: `2026-10-29`, `2026-10-29T08:00`,
  `2026-10-29T08:00:00Z`, `…T08:00:00.123+03:00`. No offset means UTC. `tomorrow`, `@1700000000` and
  `2026-02-30` get a 422 (rule `App\Rules\IsoDateTime`).
- `limit` is 1..100 (`news.api.page_limit_max`), and anything outside that gets a 422.
- `inst_ids[]` matches the literal strings stored in `news_items.instruments` with `&&` (any-of),
  served by `news_items_instruments_gin`. Ask for `BTC-USDT-SWAP` when you mean the swap, because
  there is no family expansion.
- `q` is `websearch_to_tsquery('english', q)` over `title || ' ' || summary`, using the new
  expression index `news_items_fts_gin` (migration `2026_10_29_310000`). The expression lives in
  `App\Support\News\NewsSearch::TS_DOCUMENT`. Change it and the index together.
- **Earnings ingest (ATL-61):** the calendar places `earnings_events.time` = `bmo` at 09:30 and `amc`
  at 16:00 America/New_York (DST-aware, config `news.api.earnings_*`). Any other value, or null,
  goes at 00:00 UTC of `date` with `time_known: false`. Store Finnhub's hour hint as-is (`bmo`/`amc`/`dmh`).
- Sentiment reads `market_sentiment` with `index_key = crypto_fng` by default (`news.api.default_sentiment_index`).

## GET /api/news

Query: `q`, `inst_ids[]` (≤ 50, `[A-Za-z0-9._-]`), `asset_class` (crypto|stock|etf|index|commodity|other,
`asset_classes @>`), `event_type` (the 17 brief types, exact case), `min_importance` 1..5, `since`,
`until` (on `published_at`, inclusive; until < since → 422), `cluster` (bool), `limit` (default 50), `cursor`.
Order: `published_at desc, id desc`. Cursor pagination (`meta.next_cursor`, `links.next`).

```json
{"data":[{"id":"9b0c…","title":"Bitcoin ETF sees record inflow","title_original":null,"language":"en",
  "source":"rss.coindesk","url":"https://…","published_at":"2026-10-29T03:00:00Z",
  "instruments":["BTC-USDT","BTC-USDT-SWAP"],"asset_classes":["crypto"],"event_type":"ETF_flow",
  "sentiment":0.42,"importance":4,"novelty":0.73,"cluster_id":"5e1d…"}],
 "links":{…},"meta":{"path":"…","per_page":50,"next_cursor":"eyJ…","prev_cursor":null}}
```

`cluster=1` collapses to one row per story: the newest matching item of each cluster. An item with
no cluster counts as its own story. Two extra fields are added, both computed **over the items that
match the filters** (not `news_clusters.item_count`): `"item_count":3,"first_seen":"2026-10-29T00:15:00Z"`.

## GET /api/news/clusters/{uuid}

404 if the cluster is unknown or the id is not a UUID. Items are newest first, all of them, each in
the same shape as in the list.

```json
{"data":{"id":"5e1d…","headline":"Fed decision","first_seen":"…Z","last_seen":"…Z","item_count":2,
  "max_importance":4,"instruments":["XAU-USDT-SWAP"],"items":[{…},{…}]}}
```

## GET /api/calendar

Query: `from` (default now), `to` (default from + 48 h; to < from → 422), `min_importance`
(economic only, since earnings have no importance), `kind` = economic|earnings|all (default all), `limit`
(default 100). Both bounds are inclusive. Results are merged and sorted by `at`, then by kind and id.

```json
{"data":[
  {"kind":"economic","id":"…","at":"2026-10-29T12:30:00Z","name":"GDP","country":"US","importance":3,
   "actual":null,"forecast":"3.1%","previous":"3.2%","surprise":null,"source":"forexfactory.calendar"},
  {"kind":"earnings","id":"…","at":"2026-10-29T20:00:00Z","symbol":"AAPL","date":"2026-10-29",
   "time_hint":"amc","time_known":true,"eps_est":"1.23","eps_act":null,"rev_est":null,"rev_act":null}],
 "meta":{"from":"2026-10-29T08:00:00Z","to":"2026-10-31T08:00:00Z","kind":"all","limit":100}}
```

Decimals are exact strings with trailing zeros stripped.

## GET /api/sentiment/latest

`?index=` (`[a-z0-9_.]`, default `crypto_fng`). Returns `{"data":null}` when there are no rows.

```json
{"data":{"index_key":"crypto_fng","value":"72","classification":"Greed","ts":"2026-10-29T00:00:00Z"}}
```

## Not built

No per-user or per-env filtering, no translation (`title_original` passes through), no news write
endpoints, and no family expansion of `inst_ids` (`BTC-USDT` does not imply `BTC-USDT-SWAP`).
