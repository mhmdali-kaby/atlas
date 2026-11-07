# ATL-110 — daily agent_scores for Market Scout and News Intelligence

For ATL-112 (Scout evals), the Agents → Scorecards UI, and anyone adding another agent's score.
Code: `services/agents/src/atlas_agents/scoring.py` (maths + DB), `atlas_agents/agents/scores.py`
(hooks). Tests: `services/agents/tests/test_scoring.py`.

## The two-minute version

- A daily hook `scoring` at `agents.schedule.scoring` = `"00:15"` Baghdad, registered under **each**
  agent's own slug (`market_scout`, `news_intelligence`), so a disabled agent is not scored.
- Each run (re)scores every Baghdad day in `[today - agents.scoring.lookback_days (3), today - 1]`
  and **upserts** one `agent_scores` row per (agent, env, day). Re-running only overwrites.
- **Why a lookback and not just yesterday:** the 08:00 watchlist's 24h window closes at 08:00 the
  next day, so at 00:15 yesterday's window is still open (skipped). The day before yesterday is
  scored instead. Downtime is caught up the same way.
- The hook is code, not a model run, so no `agent_runs` row marks it done. The hook remembers the
  scored day in memory; a restart scores again (harmless, idempotent).
- Skips write nothing and log at INFO with the reason.

## Market Scout row

Scored watchlist: the day's **current** version (highest). t0 = its `created_at`, t1 = t0 + 24h
(`scoring.scout.outcome_hours`). Skipped when there is no watchlist, status `empty`, `now < t1`, or no
pick has price data.

- move (ATR units, signed) = (close at t1 − close at t0) / ATR. "Close at X" = close of the last
  confirmed `1H` candle closed by X, refused if it closed more than `max_price_lag_minutes` (120)
  earlier. ATR = Wilder ATR(`quant.atr_window` = 14) of the `1D` candles closed by t0.
- picks = items not removed by the owner. Comparison = `watchlists.data.shortlist` minus every item
  of the version (so owner-removed picks count nowhere).
- `n` = picks with a move; `hit_rate` = directional accuracy.

```json
{"agent": "market_scout", "day": "2026-10-20", "n": 3, "hit_rate": "0.5000",
 "data": {
   "selection_edge_atr": 1.5, "median_pick_move_atr": 2.0, "median_shortlist_move_atr": 0.5,
   "directional_accuracy": 0.5, "directional_n": 2, "directional_correct": 1, "comparison_n": 3,
   "watchlist_id": "…", "version": 1, "status": "published",
   "window_start": "2026-10-20T05:00:00+00:00", "window_end": "2026-10-21T05:00:00+00:00",
   "picks": [{"inst_id": "SOL-USDT-SWAP", "bias": "long", "p0": 100.0, "p1": 106.0, "atr": 2.0, "move_atr": 3.0}],
   "skipped": [{"inst_id": "XYZ-USDT-SWAP", "reason": "no 1H candle closed by …"}],
   "metrics": {"selection_edge_atr": 1.5, "directional_accuracy": 0.5}}}
```

Neutral picks are excluded from accuracy; a move of exactly 0 is a miss. `selection_edge_atr` is
null when no comparison instrument had a move.

## News Intelligence row

For each `news_alert` War Room message stored on the Baghdad day: the **first** reply authored by
`news_intelligence` with `reply_to` = that alert, and the item `card.news_item_id`. latency = reply
`created_at` − `news_items.fetched_at` (seconds). Alerts with no reply (or replied to Main with
`reply_to: null`, or an unknown item) are listed in `unanswered_alert_ids`. Skipped when the day has
not ended or no alert was answered. `n` = answered alerts; `hit_rate` = share ≤
`scoring.news.latency_target_seconds` (90). p90 is numpy's linear interpolation.

```json
{"agent": "news_intelligence", "n": 10, "hit_rate": "0.9000",
 "data": {"latency_median_s": 55.0, "latency_p90_s": 91.0, "within_target_share": 0.9,
   "latency_target_s": 90.0,
   "alerts": [{"alert_id": "…", "news_item_id": "…", "latency_s": 40.0}],
   "unanswered_alert_ids": ["…"],
   "metrics": {"latency_median_s": 55.0, "latency_p90_s": 91.0}}}
```

## Where the scores show

- `data.metrics` (numbers only) is the per-agent block. `prompts.scorecard_block` adds a `metrics`
  column (`key=value, …`) when any row has metrics; rows without them render as before.
- Market Scout's prompt `your_recent_watchlists[].outcome` is now `{selection_edge_atr,
  directional_accuracy, picks[{inst_id, bias, move_24h_atr}]}` once scored, else `"not scored yet"`.
- `memory.get_my_scores` already returns `data` in full.

## Not built here

Brier/calibration (no probability statements yet), Historian scores, any UI.
