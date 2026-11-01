# ATL-198 — voters' 30-day reliability weights

Code: `services/agents/src/atlas_agents/scorecards/reliability.py`; hook `write_reliability_weights`
in `atlas_agents/agents/scores.py`, job `reliability` at `agents.schedule.reliability` (00:30 Baghdad),
registered under the **orchestrator** slug (it runs the votes; 15 min after the 00:15 scorers, so the
Strategist scorer's paper trades exist). Config: `agents.scoring.reliability` (days 30, min_n 10,
base 0.5, scratch_r 0.1); clamp and default come from `agents.votes.reliability_min/max/default`.

## What is written

On each voter's (`agents.votes.voters`) `agent_scores` row for **yesterday** (the fire day − 1),
merged into `data` (the row is created with `n = 0`, `hit_rate = null` when the agent has none —
the Historian, News Intelligence and any voter whose scorer did not run):

```json
{"reliability_weight": "1.25", "reliability_accuracy": 0.75, "reliability_n": 12, "reliability_correct": 9}
```

`reliability_weight` is a string Decimal (what `votes.reliability_weights` already parses);
`reliability_accuracy` is null when n = 0. Below `min_n` the weight is `"1"` but accuracy and n are
still stored. `scoring.upsert_score` now keeps every `data.reliability*` key when a scorer
re-scores the day, so a lookback re-run does not erase the weight.

## Sample and rules

- Chains = Strategist entry proposals whose latest version was created in the 30 Baghdad days
  ending yesterday. Vote per voter = `votes.collect_votes` (newest version voted; `change` on an
  earlier version = fix applied).
- Outcome R: taken trade (PM post-mortem `data.actual.realised_r`, else `positions_closed`), else
  the head's filled `paper_trades.realised_r`. Not filled, still open, no paper row, or
  |R| < scratch_r → not counted.
- Correct: take / change-with-fix before a win; skip / change-without-fix before a loss.
- weight = clamp(0.5 + correct/n, 0.5, 1.5).

## Two-minute version

A row with `n = 0` and only reliability keys may now exist for a day; a scorecard should not read
that as "scored zero". Nothing is broadcast; votes read the newest weight in the last 30 days.
