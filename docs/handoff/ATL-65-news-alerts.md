# ATL-65: enrichment wiring and `news.alert`

For ATL-70 (the `news_alert` card), patch E (News Intelligence, the Portfolio Manager), and anyone
who reads `news_items` or `news_clusters`.

## The two-minute version

- Every new item is enriched in the ingest process before it is stored, in this order: link (ATL-62),
  then event_type and sentiment (ATL-63), then embed and cluster (ATL-64), then importance with
  novelty. `atlas_ingest.enrichment.Enricher` is the pipeline's hook. It inserts each item before it
  clusters the next one and returns the count, so `Pipeline` skips its own insert.
- An item alerts when **importance is 5**, or when **importance is 4 or more and it links an
  instrument the env holds**. "Held" means the newest positions tick, the same one the Portfolio API
  trusts. Matching is by base symbol (`TSLA-USDT` ↔ `TSLA-USDT-SWAP`/`TSLA-USDC-SWAP`). The
  thresholds live in `config/defaults.yaml` under `news.alerts`.
- **One alert per story.** The cluster is claimed with `news_clusters.alerted_at`, a new nullable
  column, using `UPDATE … WHERE alerted_at IS NULL`. A later item of the same cluster never alerts,
  even if it scores higher. If Redis is down when the alert is published, the claim is released, so
  the story's next item can alert.
- Alerts are published after the commit as a `news.alert` envelope on `atlas.warroom`. The bridge
  turns it into a `news_alert` War Room message in the main thread, with author_type `system` and
  author `system:news`. **ATL-70 reads the card, not content_md.** content_md is the headline only,
  as inert plain text.
- Every alert field is untrusted source text (headline, url, source name). The bridge validates it
  and accepts only http/https URLs. Render the card fields as text. Never use
  `dangerouslySetInnerHTML`, and never put the url into `href` without the http(s) check the bridge
  already applied.

## `news.alert` envelope (brief §3.2), exactly as ingest publishes it

This is the shared fixture `services/ingest/tests/fixtures/alerts/news_alert_envelope.json`. A pytest
proves that ingest produces exactly this shape, and Pest feeds it to the bridge.

```json
{
  "id": "67e68c59-7651-53a2-abdf-6fb3422ad865",
  "type": "news.alert",
  "ts": "2026-10-29T12:00:41.250000Z",
  "env": "demo",
  "source": "system:news",
  "correlation_id": "news:8a7b6c5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d",
  "payload": {
    "news_item_id": "3f9c2a1e-7b4d-4c8e-9a10-2b3c4d5e6f70",
    "cluster_id": "8a7b6c5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d",
    "headline": "Tesla reports third-quarter earnings",
    "source": "rss.cnbc",
    "source_name": "CNBC Top News",
    "category": "markets_news",
    "url": "https://www.cnbc.com/2026/10/29/tesla-q3-earnings.html",
    "published_at": "2026-10-29T12:00:00Z",
    "importance": 4,
    "event_type": "earnings",
    "inst_ids": ["TSLA-USDT"],
    "asset_classes": ["stock"],
    "sentiment": 0.0,
    "novelty": 1.0,
    "reason": "held_instrument",
    "held_inst_ids": ["TSLA-USDT-SWAP"]
  }
}
```

| field | type | meaning |
| --- | --- | --- |
| `id` | uuid | `uuid5(ALERT_NAMESPACE, cluster_id)`: one id per story, so a re-publish is deduplicated by `warroom_messages.event_id` |
| `ts` | ISO-8601 Z | publish time; it becomes the message's `created_at` |
| `correlation_id` | string | `news:<cluster_id>` |
| `news_item_id` | uuid | the `news_items` row that triggered the alert (the story's first qualifying item) |
| `cluster_id` | uuid | `news_clusters.id`; `/api/news/clusters/{id}` (ATL-67) lists the other sources |
| `headline` | string | the item's title, untrusted |
| `source` | string | `news_sources.key` |
| `source_name` | string | `news_sources.name` (display) |
| `category` | string \| null | `news_sources.category` (`exchange`, `crypto_news`, `markets_news`, …) |
| `url` | string | the article link as the feed gave it (http/https only; the bridge rejects anything else) |
| `published_at` | ISO-8601 Z | the item's published_at |
| `importance` | int 1–5 | final importance, with novelty |
| `event_type` | string | one of the 17 brief §7.2 types |
| `inst_ids` | string[] | linked **instrument families** (`TSLA-USDT`, `BTC-USDT`), not instIds |
| `asset_classes` | string[] | from the linker |
| `sentiment` | float −1..1 | the item's headline sentiment (ATL-63); `0.0` when the lexicon scores nothing |
| `novelty` | float 0..1 \| null | ATL-64 novelty |
| `reason` | `importance` \| `held_instrument` | which rule fired (`importance` wins when both do) |
| `held_inst_ids` | string[] | the held instIds the item matched (`[]` for an importance-5 alert on an unheld instrument) |

## What the bridge stores (`warroom_messages`)

| column | value |
| --- | --- |
| `thread_id` | the env's main thread |
| `author_type` | `system` |
| `author` | `system:news` |
| `kind` | `news_alert` |
| `content_md` | `BridgeEventHandler::plainContent(headline)`: one line with control and private-use characters removed, backticks turned into `'`, runs of `*`, `{` or `}` cut to one, and a leading `- `/`1. ` prefixed with U+2060. So the War Room renderer (`tokens.ts`) shows no bold, code, tag, list or placeholder from source text |
| `card` | every payload field above, verbatim (`sentiment` and `novelty` as numbers). jsonb returns keys in its own order |
| `event_id` | envelope `id` |
| `created_at` | envelope `ts` |

`GET /api/warroom/messages?thread_id=<main>` returns it in the usual `WarroomMessageResource` shape,
and the bridge broadcasts `.warroom.message` on `private-warroom` once, when it is stored. A
`news.alert` whose `source` is not `system:news`, or whose fields fail validation (bad url,
importance outside 1–5, sentiment outside −1..1, missing headline…), is logged as malformed, acked
and never stored.

## Enrichment columns written on `news_items`

`instruments`, `asset_classes`, `entities` (the linker's Entity list as JSON: text, alias,
target_kind, target, field, start, end; NULL when there are none), `event_type`, `sentiment`
(headline score of "title. summary", stored even when nothing is linked), `importance`, `novelty`,
`cluster_id`, `embedding`. After each insert, `news_clusters.max_importance` is recomputed from the
cluster's stored items, because the cluster row is written with the provisional importance (no
novelty yet).

## Backfill

```
cd services
uv run --package atlas-ingest python -m atlas_ingest.enrich --since 72h [--env demo] [--embedder real|fake|auto] [--batch-size 100]
# news_items enriched: 42
```

It picks rows in the window with `event_type IS NULL OR embedding IS NULL`, oldest first, and
enriches them exactly as the live hook does. Each batch is committed separately. A second run prints
`0`. It never publishes alerts. `--since` accepts `30m`, `72h` and `7d`.

## Other changes

- **The headline RSS feeds' default `poll_seconds` went from 180–300 to 60** (CoinDesk,
  Cointelegraph, The Block, Decrypt, CNBC, MarketWatch). With 180 the brief's < 90 s
  ingest-to-alert target cannot be met. The per-symbol Yahoo template stays at 300, since it makes
  one request per symbol. 60 is the Data sources API minimum. `NewsSourcesSeeder` uses
  `firstOrCreate`, so **an existing DB keeps its old intervals**. The owner can lower them in
  Settings → Data sources.
- `EnrichHook` now returns `int | None`: an int means "I stored N rows", and None means "pipeline,
  insert them".
- The enricher re-syncs `instrument_aliases` through `AliasWatcher` at most every
  `news.alerts.alias_resync_seconds` (60). It reinstalls the process linker when the sync writes rows,
  so a resync is picked up without restarting ingest.
- `ingest/tests/conftest.py` installs an offline default enricher for every ingest test: FakeEmbedder,
  an empty linker and a publisher that drops what it is given.

## Latency (AC3)

`test_fetch_to_publish_latency_is_under_90s_including_one_poll_interval` serves the recorded CoinDesk
feed and measures the time from the poll starting to the alert being published, with FakeEmbedder. It
adds the slowest configured headline RSS poll interval. It logs, for example,
`fetch-to-publish 0.029 s + one RSS poll interval 60 s = 60.029 s (target < 90 s)`. The real model
adds about 5 s once per process to load, and some tens of ms per poll to embed.

## Not built

- Alerts on watchlist instruments: the watchlist arrives in patch E. Add those to `held_instruments`, or
  as a third rule.
- Notifying the Portfolio Manager: the alert is on the bus, so patch E's PM subscribes to it.
- Per-instrument sentiment and translation, as ATL-63 noted.
