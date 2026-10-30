# ATL-108 — Market Scout's 08:00 watchlist: pipeline, card, envelopes

For ATL-109 (intraday re-run), ATL-110 (scores), ATL-112 (Scout evals) and ATL-115 (War Room card).
Code: `services/agents/src/atlas_agents/agents/market_scout.py` (pipeline + hooks) and
`services/agents/src/atlas_agents/scout/publish.py` (pure rules). Tests: `tests/test_market_scout.py`.

## The two-minute version

- `market_scout` is now **enabled** in `agents.roster`. Its hooks are the daily `scout_watchlist`
  (`agents.schedule.scout_watchlist: "08:00"` Baghdad) and the @Scout mention.
- One entry point: `await run_watchlist(host, trigger=..., correlation_id=..., day=None, depth=0)`.
  ATL-109 should call it with its own trigger and a fresh correlation id. Each call saves a **new
  version** for the day. Only `trigger="schedule"` skips a day already done: a watchlist with
  `data.correlation_id` equal to the fire's id. That also covers the empty case, where the model
  never runs, so the supervisor's `agent_runs` check would not see it.
- **An @Scout mention runs in the background** (`asyncio.create_task`) and the handler returns None.
  It has to: the teammates' replies arrive through the War Room consumer that called the handler,
  so awaiting the run there would deadlock. The 08:00 hook awaits inside `tick` (the consumer is a
  separate task), which delays the other schedule hooks by up to `wait_seconds` + the run.
- **Agents can no longer post `kind="watchlist"`.** `warroom.post_message` answers
  `code_posted_kind`. The card is always posted by code after the rules below.
- **Mentions → replies.** Code publishes two `warroom.message` mentions (envelopes below), then polls
  `warroom_messages` every `poll_seconds` (2) for a reply with `author` = the target and
  `reply_to` = the row whose `event_id` is the mention's envelope id. It stops after
  `wait_seconds` (300). News Intelligence is not enabled until ATL-107, so for now @News always
  times out and every watchlist is `partial_evidence`. That is the brief's rule working, not a bug.
- `mentions.resolve_parent` now retries for up to `agents.mentions.parent_wait_seconds` (3) when
  an **agent's** mention is not stored yet. The supervisor and the bridge read the same stream, so
  the teammate otherwise replied to Main with `reply_to: null` and the Scout never matched it.
- The feature table is reused when the newest `feature_table` research report is still valid
  (`valid_until > now`). Otherwise it is built and saved. Staleness is checked **after** the wait:
  `now - table.as_of > stale_minutes` (15) → partial.

## Order of the rules (code, after the model)

1. Schema: the last fenced JSON block must validate as `ScoutAnswer` and pass `answer_problems`:
   exactly 3 backdrop lines, ≤ `max_picks`, unique inst_id/rank, exactly min(3, unpicked shortlist)
   rejected candidates from the shortlist, and `no_pick_reason` when there are no picks. A failure
   gets one follow-up. A second failure ends the run `degraded` and **saves and posts nothing**.
2. Correlation: picks (minus blocked) plus open positions are grouped by 30-day correlation >
   `prescore.correlation.threshold` (0.8). Pairs come from `shortlist.correlated_pairs`, plus
   `compute_correlations(picks + held)` when positions exist. A group with more than
   `max_correlated_picks` (2) members that holds a pick is refused **once** with a follow-up. If
   the model repeats it, the lowest-ranked picks of the group are dropped (an owner pin is never
   dropped).
3. `enforce`: drop blocklisted (settings `blocklist`, else `agents.scout.universe.blocklist`),
   owner-removed today, and not-in-shortlist picks. Then trim correlated picks. Then carry today's
   owner pins (from the current version, `pinned_by_owner` and not removed) that the model left
   out. Then cap convictions at `partial_conviction_cap` (0.5): every pick when evidence is
   partial, else each pick whose asset class (from the feature table, else the pick) is in
   `degraded_asset_classes`. Then renumber ranks 1..n. A pick above the cap gets
   `conviction_before_cap` and `cap_reason` on the card (`partial_evidence` wins when both apply).
3a. `degraded_asset_classes` is computed in `prepare()` from `news_sources`: a class in
   `agents.news_intelligence.asset_class_categories` is degraded when no source in its categories is
   up (up = enabled, not requires_key-without-key, not inside `disabled_until`; the same rule as
   Laravel's `SourceStatus::DOWN`). A class with no source row is degraded. The news-down cap alone
   does **not** make the status `partial_evidence`.
4. Status: `empty` when no picks remain, else `partial_evidence` when evidence is partial, else
   `published`. Saved by `save_watchlist` with `created_at = now`, `edit_window_ends_at = created_at
   + watchlist_edit_window_minutes` (10). The carried pins get `pinned_by_owner = true` again.
5. The card is the run's final `warroom.message`. Afterwards `watchlists.message_id` is filled from
   the bridge-stored row (`card->>'watchlist_id'`), waiting up to `link_seconds` (5). If the bridge
   is late it stays null.

Code layout: `agents/market_scout.py` (hooks, pipeline, reviewer), `scout/teammates.py` (the
@News/@Historian requests and the reply wait), `scout/publish.py` (the pure rules).

## Envelopes

The mentions, source `agent:market_scout`, Main thread, `correlation_id` = `scout_watchlist:YYYY-MM-DD`
(the schedule) or the mention's correlation id:

```json
{"type": "warroom.message", "env": "demo", "source": "agent:market_scout",
 "correlation_id": "scout_watchlist:2026-10-31",
 "payload": {"thread": {"kind": "main", "key": "main"}, "author_type": "agent", "author": "market_scout",
   "kind": "text", "content_md": "@News instrument briefs for today's shortlist instruments with news or catalysts: XAU-USDT-SWAP. For each: story clusters with CONFIRMED/REPORTED/RUMOUR, upcoming catalysts in 48h, net sentiment and whether it is priced in.",
   "card": null, "reply_to": null, "mentions": ["news_intelligence"], "mention_depth": 1,
   "reasoning_md": null, "tool_calls": null, "tokens_in": null, "tokens_out": null, "cost_usd": null}}
```

The Historian mention says `@Historian regime and analog check for today's top Market Scout
candidates: <top 8>.` If no shortlist instrument has news or its own catalyst, @News is asked to
brief the 48h calendar and the top 5 instead. A mention-triggered run at depth d mentions at
depth d+1. When that reaches `max_depth`, the teammate is `not_asked` and the evidence is partial.

## The watchlist card (`payload.kind = "watchlist"`, `payload.card`)

Published (run-posted: the payload also carries `run_id`, `reasoning_md`, `tool_calls` and usage):

```json
{
  "type": "watchlist",
  "watchlist_id": "6f1c2b3a-…", "version": 1, "date": "2026-10-31", "status": "published",
  "created_at": "2026-10-31T05:06:12+00:00", "edit_window_ends_at": "2026-10-31T05:16:12+00:00",
  "reason": null,
  "picks": [
    {"inst_id": "SOL-USDT-SWAP", "rank": 1, "asset_class": "crypto", "bias": "long", "conviction": 0.72,
     "thesis": "…", "catalysts": [{"name": "US CPI", "time": "2026-10-31T12:30:00Z", "source": "forexfactory", "importance": 5}],
     "key_levels": {"support": 100.0, "resistance": 120.0}, "invalidation": "…",
     "data_as_of": "2026-10-31T04:59:00Z", "pinned_by_owner": false,
     "conviction_before_cap": 0.8, "cap_reason": "news_class_down"},
    {"inst_id": "XAU-USDT-SWAP", "rank": 2, "asset_class": "commodity", "bias": "long", "conviction": 0.7,
     "…": "…", "pinned_by_owner": false}
  ],
  "degraded_asset_classes": ["crypto"],
  "backdrop": ["BTC trending up.", "Gold flat ahead of CPI.", "No big US event in 48h."],
  "rejected": [{"inst_id": "ETH-USDT-SWAP", "reason": "weaker evidence"}, {"…": "3 in total"}],
  "corrections": [{"rule": "blocked", "inst_id": "DOGE-USDT-SWAP", "detail": "on the blocklist"}],
  "correlation_refused": [["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]],
  "trigger": "schedule", "correlation_id": "scout_watchlist:2026-10-31",
  "feature_table_report_id": "…", "feature_table_as_of": "2026-10-31T05:00:00+00:00",
  "shortlist": ["SOL-USDT-SWAP", "…"], "previous_version": null,
  "evidence": {
    "feature_table_as_of": "2026-10-31T05:00:00Z", "feature_table_age_minutes": 6.2, "feature_table_stale": false,
    "teammates": [
      {"agent": "news_intelligence", "status": "replied", "mention_id": "…", "message_id": "…", "replied_at": "…", "note": null},
      {"agent": "historian", "status": "replied", "mention_id": "…", "message_id": "…", "replied_at": "…", "note": null}
    ]
  },
  "meta": {"unmatched_numbers": [], "checked": 14}
}
```

- `corrections[].rule` ∈ `blocked | removed_by_owner | not_in_shortlist | correlation |
  conviction_cap | pinned`.
- `teammates[].status` ∈ `replied | timed_out | not_asked`.
- `picks[].conviction_before_cap` / `picks[].cap_reason` are present **only** on a pick whose
  conviction was lowered; `cap_reason` ∈ `partial_evidence | news_class_down`. `conviction` is
  already the capped value. `degraded_asset_classes` is always present (`[]` when every class has
  news), also on both empty variants.
- `content_md` starts with `**News down** for crypto: convictions in that class capped at 0.5.`
  when a class is degraded.
- `meta` is the number guard (same shape as other cards; ATL-96 highlights from it).
- `content_md`: the 3 backdrop lines plus `Today's watchlist: SOL-USDT-SWAP long (0.72), …`.

**Partial evidence**: `status: "partial_evidence"`, every `picks[].conviction` ≤ 0.5,
`reason: "Partial evidence: historian did not reply in time"` (also `feature table is 17 min old
(limit 15 min)`), `conviction_cap` corrections, and `cap_reason: "partial_evidence"` with
`conviction_before_cap` on each pick that was above 0.5. `content_md` starts with `**Partial evidence**: …`.

**Empty, from the shortlist** (code-posted, no model call; the payload has no `run_id`):
`status: "empty"`, `picks: []`, `backdrop: []`, `rejected: []`, `evidence` absent,
`reason: "No instrument passed the universe filter: 30 rejected (by check: volume 22, state 5, age 3)."`,
`content_md: "No watchlist today: <reason>"`.

**Empty, from the model** (zero picks, or every pick removed by the rules): `status: "empty"`,
`reason` = the model's `no_pick_reason` (or "Every pick was removed by Atlas's rules."), and
`content_md` starts with `No watchlist today:`.

`watchlists.data` holds every card key except `type, watchlist_id, version, date, status,
created_at, edit_window_ends_at, reason, picks, meta` (those are columns, items or runtime).

## Not built here

- The intraday triggers (ATL-109). Call `run_watchlist` with `trigger="manual"` (or a new trigger)
  and your own correlation id. Pins and removals carry over from the current version.
- Pick outcomes. The prompt's `your_recent_watchlists[].outcome` is `"not scored yet"` until
  ATL-110 writes agent_scores.
- In replay (`host.replaying`) nothing is published, so the teammate wait times out. ATL-112's
  evals should stub `wait_for_replies` (or seed reply rows) and `prepare`, the way the tests do.
