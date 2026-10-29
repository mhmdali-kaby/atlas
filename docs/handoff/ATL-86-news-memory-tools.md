# ATL-86 — news and memory MCP tools

Modules: `services/agents/src/atlas_agents/tools/news.py`, `memory.py`, `guard.py` (imported by
`atlas_agents/tools/__init__.py`, so they replace the catalogue stubs). Tests:
`agents/tests/test_news_tools.py`, `test_memory_tools.py` (real test DB; the injection test uses
real Redis DB 15 and skips without it).

## The two-minute version

- **`ToolContext` has a new field `publish: Callable[[str, EventEnvelope], object]`**, default
  `atlas_shared.bus.publish`. Tools that put events on the bus call `ctx.publish(stream, envelope)`;
  tests pass a `Recorder` or a function bound to the test Redis client. ATL-88's warroom tools
  should use it too.
- **`guard.quote_fields(ctx, tool=, source=, item_id=, fields={name: text|None})`** quotes each field
  with `untrusted()` and returns `(quoted_fields, InjectionFlag | None)`. On a flag it publishes one
  `warroom.message` on `atlas.warroom`. Every news result model has `injection_flags: list[InjectionFlag]`
  (`{item_id, source, fields, matches}`; no matched text).
- **The alert envelope**: `source "system:guard"`, id = `uuid5(ns, "<env>:<source>:<item_id>")` so the
  bridge stores one alert per item however often it is read. Payload: main thread,
  `author_type "system"`, `author "system:guard"`, `kind "system_alert"`, `content_md` names source,
  item id, fields, agent and tool, `card = {alert: "untrusted_instructions", source, item_id, fields,
  agent, tool}`. **No text from the item is ever in the alert.** A publish failure is logged, not raised.
- `fetch_article`: stored body first (matches `url` or `canonical_url`); otherwise
  `news._fetcher` (= `guarded_fetch`, atlas_ingest's `Extractor.article_text`, the round 6–8 guard).
  Errors: `invalid_url` (non-http(s)), `fetch_refused` (guard said no / no text),
  `not_available_in_replay` (never fetches the web in replay). Fetched text has source `web:<host>`.
- `atlas-agents` now depends on `atlas-ingest` (workspace) for that guard.

## Replay rules

- news_items: `fetched_at <= as_of AND published_at <= as_of`.
- economic_events / earnings_events: `created_at <= as_of`; outcomes hidden for events not yet due
  (`actual`, `surprise` when `scheduled_at > as_of`; `eps_act`, `rev_act` when `date >= as_of.date()`).
- story cluster: items bounded as above; `item_count`, `first_seen`, `last_seen`, `max_importance`,
  `instruments` are recomputed from the bounded items, not read from the (mutable) cluster row.
- memory reads: `created_at <= as_of`; scores also `day < replay day`. **Memory writes
  (`write_journal`, `append_notebook`) return `read_only_in_replay`.**
- Every result's `as_of` is `ctx.now()` (fetch_article from a stored body: the item's `fetched_at`).

## Tool notes

- `search_news`: FTS with the same `to_tsvector('english', title || ' ' || summary)` /
  `websearch_to_tsquery` as Laravel's `NewsSearch` (uses `news_items_fts_gin`). `inst_ids` = overlap.
  Ordered rank (with query) → `published_at` desc → importance desc. Summaries cut to 600 chars.
- `get_okx_announcements`: `source = 'okx.announcements'`, default since = 7 days.
- Calendar names (`economic_events.name`) and cluster headlines are quoted too.
- `get_earnings_calendar`: symbols upper-cased before matching.
- `write_journal`: checks `access.allowed_tools(ctx.agent)` again (only the PM) → `forbidden`.
  `entry` = `{title, inst_id?, proposal_id? (uuid), expected_md?, actual_md?, lessons_md?, tags[], data{}}`,
  extra keys refused (`invalid_input`). Tags are de-duplicated and sorted.
- `get_my_scores`: always `agent = ctx.agent` and `env = ctx.env`; there is no agent argument.
- `news.web_search` stays a `not_implemented` stub (the built-in WebSearch is the real one).
  `test_tools.IMPLEMENTED` lists the catalogue tools with real bodies; add yours there.
