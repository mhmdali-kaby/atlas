# ATL-107 — News Intelligence: @News briefs, the 07:30 morning brief, news.alert analyses

For ATL-116 (War Room brief/alert cards) and anyone adding an agent that reacts to a non-mention
event. Brief §7.2, decision #247 (5). Built on ATL-101 (registry) and ATL-106 (news status).

## The two-minute version

- News Intelligence is now **enabled** (`agents.roster.news_intelligence.enabled: true`). Its hooks
  live in `services/agents/src/atlas_agents/agents/news_intelligence.py`. The logic (prompts, the
  morning context, the reviewer, the rate limiter) is in `atlas_agents/news_intelligence.py`.
- Every run posts **one** `warroom.message` of kind `research_report` with a `news_brief` card,
  then a `warroom.thinking` `run_end`. The three triggers differ only in where the message goes and
  in the card's `form`: `instrument_brief`, `morning_brief` or `alert_analysis`.
- **Code owns these card fields**, whatever the model wrote: `form`, `degraded_asset_classes`,
  `top_instruments` (morning only), `upcoming_events[].importance`, and each cluster's `status`,
  `status_sources`, `first_seen` and `price_move_since_first_seen_atr`. The model can't relabel or
  restate them. Render them as facts.
- **A new supervisor trigger kind:** `@HOOKS.event(slug, "<envelope type>")`. The supervisor hands
  any non-`warroom.message` envelope on `atlas.warroom` only to the agents that registered its type.
  News Intelligence is the only one registered for `news.alert`, so no other agent runs on an alert
  (tested with the Historian enabled beside it).
- **New `agent_runs.trigger` value `alert`** (migration
  `2026_10_30_200000_add_alert_trigger_to_agent_runs`). The Agents page's "last run" can now show
  `alert`, as well as mention/schedule/eval/manual.

## Triggers

| trigger | when | message goes to | agent_runs.trigger | correlation_id |
| --- | --- | --- | --- | --- |
| @News mention | a `warroom.message` with `"news_intelligence"` in `mentions`, from anyone (depth rule from ATL-101) | `reply_to` = the mention's row id, `thread_id` = its thread | `mention` | the mention's |
| morning brief | `agents.schedule.news_morning_brief` = `"07:30"` Asia/Baghdad, once per Baghdad day (a restart the same day posts nothing) | Main: `thread: {"kind":"main","key":"main"}`, `reply_to: null` | `schedule` | `news_morning_brief:YYYY-MM-DD` (Baghdad date) |
| news.alert | each ingest `news.alert` envelope in this env, at most `agents.news_intelligence.alerts_per_hour` (6) per rolling hour; the rest are skipped with a WARNING log and start no run | `reply_to` = the bridge's row for the alert (found by `event_id` = envelope id), `thread_id` = its thread (Main). If the bridge has not stored it after `alert_parent_tries` × `alert_parent_wait_ms` (10 × 500 ms), it goes to Main with `reply_to: null` | `alert` | `news:<cluster_id>` (the alert's) |

The alert limiter is in the supervisor's memory, so a restart starts a fresh hour. An invalid card
gets one retry with the errors (`agents.output_retries`). That includes a `cluster_id` Atlas does
not have. After a second failure the run ends `degraded`, no message is posted, and `run_end`
still arrives.

## The `warroom.message` envelope (exactly what the agent publishes)

```json
{
  "id": "6f1c…",
  "type": "warroom.message",
  "ts": "2026-10-30T04:30:02.120000Z",
  "env": "demo",
  "source": "agent:news_intelligence",
  "correlation_id": "news_morning_brief:2026-10-30",
  "payload": {
    "thread": {"kind": "main", "key": "main"},
    "author_type": "agent",
    "author": "news_intelligence",
    "kind": "research_report",
    "content_md": "Morning brief, 04:30 UTC (07:30 Baghdad). …",
    "card": { "…": "the news_brief card below" },
    "reply_to": null,
    "mentions": [],
    "reasoning_md": "…the run's thinking…",
    "tool_calls": [{"id": "toolu_1", "name": "mcp__news__search_news", "input": {"…": "…"},
                    "input_truncated": false, "as_of": "2026-10-30T04:30:01Z", "output": "…",
                    "output_truncated": false, "is_error": false}],
    "tokens_in": 5200,
    "tokens_out": 900,
    "cost_usd": null,
    "run_id": "0c6d…"
  }
}
```

For a mention or an alert reply, `thread` is replaced by `"thread_id": "<uuid>"` and `reply_to` is
the parent row id. `run_id` equals `payload.run_id` of the run's `warroom.thinking` `run_end` event.

## The `news_brief` card (`atlas_agents.schemas.NewsBrief`)

```json
{
  "type": "news_brief",
  "form": "morning_brief",
  "inst_ids": ["BTC-USDT-SWAP"],
  "clusters": [{
    "cluster_id": "8a7b6c5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d",
    "headline": "Exchange lists token",
    "status": "REPORTED",
    "status_sources": ["rss.coindesk"],
    "sources": [{"name": "CoinDesk", "url": "https://…", "published_at": "2026-10-30T02:30:00Z"}],
    "event_type": "listing",
    "importance": 4,
    "sentiment": 0.4,
    "first_seen": "2026-10-30T02:30:00Z",
    "price_move_since_first_seen_atr": [{"inst_id": "BTC-USDT", "value": 1.4, "reason": null}]
  }],
  "upcoming_events": [{
    "time": "2026-10-30T12:30:00Z", "name": "US CPI", "forecast": "0.3%", "previous": "0.2%",
    "sensitive_instruments": ["XAU-USDT-SWAP"], "importance": 3
  }],
  "net_sentiment_24h": 0.4,
  "net_sentiment_72h": null,
  "priced_in": "partly",
  "what_to_watch": "Follow-through after the listing.",
  "data_as_of": "2026-10-30T04:30:00Z",
  "top_instruments": [{"inst_id": "BTC-USDT", "stories": 3, "net_sentiment": 0.21}],
  "degraded_asset_classes": [],
  "meta": {"unmatched_numbers": [], "checked": 12, "verdict_corrections": []}
}
```

| field | owner | meaning |
| --- | --- | --- |
| `form` | code | `instrument_brief` (@News), `morning_brief` (07:30), `alert_analysis` (news.alert) |
| `clusters[].status`, `status_sources`, `first_seen`, `price_move_since_first_seen_atr` | code (ATL-106) | see `docs/handoff/ATL-106-news-status.md` |
| `upcoming_events[].importance` | code | the highest `economic_events.importance` scheduled at exactly that `time`, as known at the brief's time; `null` when Atlas has no event then (e.g. an earnings date) |
| `top_instruments` | code, morning only (`[]` otherwise) | top `morning_brief.top_instruments` (5) by distinct stories (clusters) over the last `lookback_hours` (24), then item count, then id. `inst_id` is what `news_items.instruments` holds: instrument **families** such as `BTC-USDT`, not instIds. `net_sentiment` is the mean item sentiment rounded to 3 places, or `null` when no item has one |
| `degraded_asset_classes` | code | asset classes, in config order, for which **every** news source in `agents.news_intelligence.asset_class_categories` (crypto→`crypto_news`; stock/etf/index/commodity→`markets_news`) is disabled, `needs_key` (keyed with no key) or degraded (`disabled_until` in the future). Computed when the card is posted. The model is also told in its prompt and must say so in the text |
| `meta` | code | number guard findings. It checks text and card against the run's tool outputs and the code-written fields only, never against the model's own card |
| everything else | model | validated by NewsBrief (`extra="forbid"`) |

Times in `content_md` are written as `14:30 UTC (17:30 Baghdad)` (`news_intelligence.both_zones`).
Card times are ISO-8601 UTC.

## The morning context (what code hands the model at 07:30)

`build_morning_context(ctx)` returns `{as_of, window_from, events_to, top_stories[10],
upcoming_events[≤30, importance ≥ 2, next 48h, each with time_text in both zones],
top_instruments[5], degraded_asset_classes}`, as a tool-result envelope with source
`atlas.news_morning_context`. Headlines and event names are wrapped in `<untrusted_source>`. It is
replay-bounded like the tools. All knobs are under `agents.news_intelligence.morning_brief`.

## Adding another event-driven agent

```python
@HOOKS.event("portfolio_manager", "news.alert")
async def on_alert(host: AgentHost, envelope: EventEnvelope) -> RunResult | None: ...
```

`warroom.message` cannot be registered this way (mentions have their own hook). Envelopes from
another env are ignored. A handler returns None when it started nothing.

## Not built here

- No eval cases for News Intelligence (ATL-110's harness work).
- The prompt file `prompts/news_intelligence.md` is unchanged (no tool was renamed). The per-trigger
  instructions, including "copy status from the tool, never write it", are in the run prompts.
- The alert card from ingest (`news_alert`) still has no status. The analysis that replies to it
  carries the code-computed status in its `news_brief` card.
