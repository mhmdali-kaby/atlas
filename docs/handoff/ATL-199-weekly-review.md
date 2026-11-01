# ATL-199 — the Historian's weekly review: the `weekly_review` card and research_report

Code: `services/agents/src/atlas_agents/historian_weekly.py`, hook `post_weekly_review` in
`atlas_agents/agents/historian.py`. Config: `agents.schedule.historian_weekly_review: "Sun 18:00"` and
`agents.history.weekly_review.*` in `config/defaults.yaml`.

## Two-minute version

- Runs once per week, Sunday from 18:00 Baghdad (the first supervisor tick at or after it, until
  midnight). The schedule parser now takes a weekday form: `"Sun 18:00"` (any day name's first three
  letters). `"HH:MM"` still means daily. `ScheduleHook.weekday()` returns 0 = Monday … 6 = Sunday.
- **One review per week**: a `research_reports` row (agent `historian`, kind `weekly_review`) with
  `data.week_start` = that week's Baghdad Monday stops every later tick. A failed model run also
  stores code's card, so a week never gets two.
- Every figure is code's. The model writes only its comment, a one-line `verdict` and **exactly one**
  suggested change. A reply with 0 or 2+ changes gets one follow-up; a second bad reply ends the
  run `degraded` (agent state degraded, brief §6.3) and code posts the figures with
  `suggestion: null`, `status: "degraded"`.
- A week with no closed trades: code posts "No closed trades this week." with no model run and no
  `agent_runs` row (`status: "no_trades"`).

## The War Room message

`warroom.message`, author `historian`, Main thread (`thread: {"kind":"main","key":"main"}`),
`reply_to: null`, `kind: "research_report"`. The card is told apart by `card.kind` (also
`card.type`) = `"weekly_review"`.

`content_md`: the model's ≤150-word comment, then `**Verdict:** …` and `**Suggested change:** … (reason)`.
Code-posted cards: a 2–3 line summary from code.

## The card (also `research_reports.data`, identical)

Every money/R figure is a decimal string with 2 places; counts are ints.

```json
{
  "type": "weekly_review",
  "kind": "weekly_review",
  "env": "demo",
  "as_of": "2097-06-16T15:00:00Z",
  "week_start": "2097-06-10",
  "day": "2097-06-16",
  "window": {"from": "2097-06-09T21:00:00Z", "to": "2097-06-16T15:00:00Z"},
  "trade_count": 4,
  "no_trades": false,
  "totals": {"count": 4, "wins": 2, "losses": 1, "scratches": 1, "total_r": "2.75", "r_n": 3, "total_usd": "54.95"},
  "by_setup": [
    {"key": "breakout", "count": 2, "wins": 1, "losses": 1, "scratches": 0, "total_r": "0.50", "r_n": 2, "total_usd": "9.45"},
    {"key": "pullback_to_support", "count": 2, "wins": 1, "losses": 0, "scratches": 1, "total_r": "2.25", "r_n": 1, "total_usd": "45.50"}
  ],
  "by_asset_class": [
    {"key": "commodity", "count": 1, "wins": 0, "losses": 1, "scratches": 0, "total_r": "-1.00", "r_n": 1, "total_usd": "-20.55"},
    {"key": "crypto", "count": 2, "wins": 2, "losses": 0, "scratches": 0, "total_r": "3.75", "r_n": 2, "total_usd": "75.10"},
    {"key": "stock", "count": 1, "wins": 0, "losses": 0, "scratches": 1, "total_r": null, "r_n": 0, "total_usd": "0.40"}
  ],
  "by_agent": [
    {"key": "portfolio_manager", "count": 1, "wins": 0, "losses": 0, "scratches": 1, "total_r": null, "r_n": 0, "total_usd": "0.40"},
    {"key": "strategist", "count": 3, "wins": 2, "losses": 1, "scratches": 0, "total_r": "2.75", "r_n": 3, "total_usd": "54.55"}
  ],
  "journal_ids": ["<journal_entries.id>", "…"],
  "agent_scores": [
    {"agent": "strategist", "day": "2097-06-15", "n": 4, "hit_rate": "0.75", "brier_score": null,
     "pnl_attributed_usd": null, "metrics": {"avg_realised_r": 0.5}}
  ],
  "paper_trades": {
    "count": 3, "filled": 2, "total_r": "-0.40", "avg_r": "-0.20",
    "by_status": [{"status": "rejected", "count": 3, "filled": 2, "total_r": "-0.40"}]
  },
  "verdict": "History weakly supports the process: breakout setups made 0.50R over 2 trades.",
  "suggestion": {"change": "Raise min R for breakout setups to 2.0", "reason": "n is 2"},
  "status": "ok",
  "meta": {"unmatched_numbers": [], "checked": 3}
}
```

Field notes:

- `window` = 00:00 Baghdad six days before the review day → the firing time. A trade belongs to it
  by the post-mortem's `data.actual.closed_at` (ATL-194).
- Groups are sorted by `key`. `key` is `unknown` when a post-mortem lacks that tag. Setup =
  `data.tags.setup`; asset class = `data.asset_class`; agent = the opening proposal's author
  (`data.expected.author`).
- `total_r` sums the trades that have an R (`r_n` of them), and is `null` when `r_n` is 0.
  `total_usd` sums `realised_pnl_usd` over all `count` trades.
- `paper_trades` is **absent** (not null) when the `paper_trades` table (ATL-196) does not exist.
  It counts ideas decided in the window. `filled` ones have an R; `total_r`/`avg_r` are over those.
- `agent_scores`: each agent's newest `agent_scores` row on or before the review day. `metrics` is
  `data.metrics` when present, else the row's scalar `data` keys.
- `status`: `ok` (model verdict + suggestion), `no_trades`, or the failed run's status
  (`degraded`, `error`, `rate_limited`, `over_budget`, `unavailable`). Only `ok` has `verdict`
  and `suggestion`. Everything else has both `null`.
- `meta` (number guard on the model's text) exists only when `status` is `ok`.

## Not built here

No screen: the War Room card and the Journal's weekly rows are other tasks. ATL-200's
`GET /api/journal/weekly-reviews` reads these rows by `data.week_start`.
