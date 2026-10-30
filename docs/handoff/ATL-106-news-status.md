# ATL-106 — news cluster status and price move since first seen

For ATL-107 (News Intelligence agent) and ATL-116 (War Room brief/alert cards). Decision #247 (4), brief §7.2.

## The two-minute version

The model never decides whether a story is CONFIRMED, REPORTED or RUMOUR, and never states how far
price has moved since the story broke. Code computes both from the database. The news tools return
them, and when News Intelligence posts a `news_brief` card the War Room tool **overwrites** whatever
the model wrote with those values. Your prompt should say "copy `status` and
`price_move_since_first_seen_atr` from the tool result, and use the `cluster_id` the tool gave you".
A `news_brief` naming a `cluster_id` Atlas does not have is refused (`invalid_card`). That is the
one failure mode to expect during evals.

## Source flags (`news_sources`)

New columns: `reputable boolean not null default false` and `official boolean not null default false`.
They come from `config/defaults.yaml` `news.sources.<key>.reputable|official`. The seeder sets them
on insert, and the migration `2026_10_30_100000_add_reputation_flags_to_news_sources` backfills
existing rows. A row the defaults don't know stays false/false. The owner can't change them: they
are read-only in `GET /api/data-sources`, and PATCH refuses them with 422 `prohibited`.

| key | reputable | official |
| --- | --- | --- |
| okx.announcements | true | **true** |
| rss.coindesk, rss.theblock, rss.cnbc, rss.marketwatch, finnhub.news | true | false |
| cryptopanic, rss.cointelegraph, rss.decrypt, rss.yahoo, calendar/sentiment sources | false | false |

`GET /api/data-sources` rows now carry `"reputable": true, "official": false` beside the existing
fields. `api_key` is still never in the response, only `has_key` and `key_last4`.

## Status rule (`atlas_agents.tools.news_status.decide_status`)

Over the **distinct** `news_items.source` keys of the cluster's items known by `ctx.now()`
(replay-bounded on `fetched_at` and `published_at`):

- any official source → `CONFIRMED` (`status_rule: "official_source"`)
- 2+ reputable sources → `CONFIRMED` (`"two_reputable_sources"`)
- exactly 1 reputable source → `REPORTED` (`"one_reputable_source"`)
- otherwise → `RUMOUR` (`"no_reputable_source"`)

`status_sources` lists the keys that decided it: the official ones, the reputable ones, or for a
RUMOUR every key that carried it. Distinct means by source key, so two Finnhub items from different
publishers are one source (`finnhub.news`). In replay, a story whose second reputable outlet had not
been fetched yet is still `REPORTED`.

## Price move (`atlas_quant.move_since_atr`, wrapped by `news_status.price_move_since`)

```
value = (price_now - close of the last 1H bar closed by first_seen) / Wilder ATR(14) of the 1H bars closed by first_seen
```

- `price_now` is the close of the newest closed 1m candle by `ctx.now()`, read through `tools.market.latest_closed_candles` — the same helper `okx_market.get_mark_price` uses, so the two proxies cannot drift.
- Batched: `news_status.price_moves_since(session, ctx, {inst_id: [first_seen, …]})` reads price-now for all instruments in one DISTINCT ON query and loads 1H bars once per instrument, sliced per first_seen. A `search_news` over many clusters costs 1 + (number of distinct instruments) candle queries.
- `first_seen` is the earliest `published_at` among the known items.
- Config: `agents.news_price_move.bar` (1H) and `history_bars` (200), plus `quant.atr_window` (14).
- `value` is signed: positive means price is above the pre-news close.
- When it can't be computed, `value` is null and `reason` says why: `no closed 1m candle for X to price it now`, `no 1H candle closed at first_seen (the … bar is missing)`, `fewer than 14 1H candles before first_seen for ATR(14)`, or `ATR(14) at first_seen is zero`.

## Tool output changes

`news.get_story_cluster` adds these fields to its existing output:

```json
{
  "status": "REPORTED",
  "status_rule": "one_reputable_source",
  "status_sources": ["rss.coindesk"],
  "price_move_since_first_seen_atr": [
    {"inst_id": "BTC-USDT-SWAP", "value": 1.4, "reason": null,
     "ref_ts": "2026-10-20T14:00:00Z", "ref_close": 100.0, "atr": 2.142857,
     "price_now": 103.0, "price_ts": "2026-10-20T17:05:00Z"},
    {"inst_id": "XAU-USDT-SWAP", "value": null, "reason": "no closed 1m candle for XAU-USDT-SWAP to price it now",
     "ref_ts": null, "ref_close": null, "atr": null, "price_now": null, "price_ts": null}
  ]
}
```

`news.search_news` keeps `items` and `injection_flags`, and adds `clusters`. It has one entry per
distinct `cluster_id` among the returned items, in the order those items appear:

```json
{"clusters": [{"cluster_id": "…uuid…", "status": "RUMOUR", "status_rule": "no_reputable_source",
  "status_sources": ["cryptopanic"], "sources": ["cryptopanic"],
  "first_seen": "2026-10-20T15:10:00Z", "price_move_since_first_seen_atr": [ … as above … ]}]}
```

Items with no `cluster_id` get no status entry.

## `NewsBrief` card (`atlas_agents.schemas.NewsBrief`)

Post it with `warroom.post_message(kind="research_report", card={...})` and `"type": "news_brief"`.
It is registered in `CARD_TYPES` and is allowed only for `research_report`.

```json
{
  "type": "news_brief",
  "inst_ids": ["BTC-USDT-SWAP"],
  "clusters": [{
    "cluster_id": "uuid from the tool",
    "headline": "…",
    "status": "RUMOUR",
    "status_sources": [],
    "sources": [{"name": "CoinDesk", "url": "https://…", "published_at": "2026-10-20T15:10:00Z"}],
    "event_type": "listing",
    "importance": 3,
    "sentiment": 0.4,
    "first_seen": "2026-10-20T15:10:00Z",
    "price_move_since_first_seen_atr": [{"inst_id": "BTC-USDT-SWAP", "value": 1.4, "reason": null}]
  }],
  "upcoming_events": [{"time": "2026-10-21T12:30:00Z", "name": "US CPI", "forecast": "0.3%",
                       "previous": "0.2%", "sensitive_instruments": ["XAU-USDT-SWAP"]}],
  "net_sentiment_24h": 0.2,
  "net_sentiment_72h": 0.1,
  "priced_in": "partly",
  "what_to_watch": "…",
  "data_as_of": "2026-10-20T17:04:00Z"
}
```

- `status`, `status_sources`, `first_seen` and `price_move_since_first_seen_atr` are optional to
  write and **always replaced** from the database by `warroom.apply_news_status`. In the stored
  card, each price move is reduced to `{inst_id, value, reason}`.
- `status` defaults to RUMOUR when it is omitted. `meta` is runtime-only, like the other cards.
- `importance` is 1–5. `sentiment` and `net_sentiment_*` are −1..1.
- `priced_in` is `yes`, `partly` or `no`.
- Extra keys are refused (`extra="forbid"`).

## Left for you

- No prompt work: ATL-107 writes the News Intelligence prompt and tells the model the rule above.
- The `news_brief` card does not record that a status was overwritten. If ATL-116 wants to show
  "model said CONFIRMED, code says RUMOUR", add a `CardMeta` field in `apply_news_status`.
- The `news.alert` envelope from ingest has no status yet. If alerts should carry one, call
  `cluster_statuses` when building the alert card.
- `services/shared/src/atlas_shared/defaults.py` is generated and is not `ruff format`-clean on
  develop. This change didn't cause that; `ruff check` passes.
