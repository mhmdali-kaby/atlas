# ATL-197 — Portfolio Manager, Orchestrator and Historian daily scores

Three more agents get one `agent_scores` row per env and Baghdad day from the 00:15 Baghdad job
(`agents.schedule.scoring`). Code: `services/agents/src/atlas_agents/scorecards/portfolio_manager.py`,
`scorecards/orchestrator.py`, `scorecards/historian.py`, hooked in `atlas_agents/agents/scores.py`.
Config: `agents.scoring.portfolio_manager|orchestrator|historian` in `config/defaults.yaml`.

| agent slug | `n` | `hit_rate` | `brier_score` |
| --- | --- | --- | --- |
| `portfolio_manager` | closed trades with a realised R | share of losers closed at ≤ 1.1R loss | null |
| `orchestrator` | cycles counted (on time + late) | on-time cycle rate | null |
| `historian` | probability statements scored | direction hit rate (p ≠ 0.5) | Brier of `pct_positive` |

When the day is scored and which day: PM and Orchestrator use the shared `agents.scoring.lookback_days`
(3; yesterday is scored at 00:15). The Historian needs its 72h horizon closed, so it runs its own range,
`agents.scoring.historian.lookback_days` (5): a day is first scored at day end + 72h.

## `data` — `portfolio_manager`

Closed trades = PM post-mortem journal entries (ATL-194, `data.position_ref` set) whose
`data.actual.closed_at` is on the day.

| key | type | meaning |
| --- | --- | --- |
| `r_captured` / `r_captured_n` | float\|null / int | Σ realised_r ÷ Σ mfe_r over trades with mfe_r > 0 |
| `avg_realised_r`, `avg_mfe_r` | float\|null | the two averages behind that ratio (same trades) |
| `trades_n` | int | trades with a realised R (= row `n`) |
| `losers_n`, `losers_within_n`, `losers_within_rate` | int, int, float\|null | `outcome = loss` trades; within = realised_r ≥ −`loser_limit_r` (1.1) |
| `compliance_rate` / `compliance_n` / `compliance_ok` | float\|null / int / int | PM proposals created that day that are compliant (unverified excluded) |
| `violations` | list | the non-compliant ones, same shape as `actions` |
| `unverified_n` | int | amend_stops whose position entry could not be found |
| `actions` | list | `{proposal_id, inst_id, kind, rule, stop, previous_stop, side, status ok\|violation\|unverified, reason}` |
| `trades` | list | `{journal_id, inst_id, outcome, realised_r, mfe_r}` |
| `compliance_alert` | object | only when there are violations: `{proposal_ids (sorted), posted_at}` |
| `metrics` | object | `r_captured, losers_within_rate, compliance_rate` |

Compliance: the rule name (its part before `:`, so `event_reduce:<id>` counts as `event_reduce`) must be
one `pm/rules.py` emits for the kind (`amend_stop`: breakeven, tp1_trail, two_r_trail; `partial_close`:
tp1_partial, event_reduce; `close`: time_stop, invalidation, event_reduce; `amend_targets`: none), and an
amend_stop must be strictly tighter than the previous stop: the latest earlier PM amend_stop on the
instrument after the entry that is not rejected/cancelled/expired, else the entry proposal's stop. The
entry is the `evidence` ref of type `proposal`, else the latest sent/filled/closed entry on the instrument.

Below 100 %: one War Room `system_alert` (author `portfolio_manager`, card
`{code: "pm_rule_compliance", day, compliance_rate, violations}`) and one `system.alert` on
`atlas.system`. A re-run with the same violation ids posts nothing; a new set posts again.

## `data` — `orchestrator`

| key | type | meaning |
| --- | --- | --- |
| `on_time_rate`, `cycles_n`, `cycles_on_time`, `cycles_late`, `cycles_pending` | | cycles started that day |
| `cycles` | list | `{cycle_id, kind, started_at, closed_at, deadline, status on_time\|late\|pending}` |
| `cycle_budget_seconds`, `grace_seconds` | int | 1530 (research+strategist+review+vote+risk), 120 |
| `ack_n`, `ack_unanswered` | int | owner messages answered / not answered by the Orchestrator |
| `ack_latency_seconds` | list[float] | owner message → first Orchestrator reply (`reply_to`) |
| `ack_median_seconds`, `ack_p90_seconds`, `ack_max_seconds` | float\|null | linear interpolation |
| `ack_within_target_share`, `ack_target_seconds` | float\|null, float | share within 10 s |
| `loops_stopped` | int | Orchestrator `loop_summary` cards that day |
| `metrics` | object | `on_time_rate, ack_median_seconds, ack_p90_seconds, loops_stopped` |

Deadline of a cycle = max(started_at + budget, latest stored `cycles.deadlines` value) + grace. Note:
during the day, `orchestrator/owner.py record_score` keeps writing *today's* row live with
`hit_rate` = ack share within target and `n` = acks; the 00:15 job then rewrites that day with
`hit_rate` = on-time rate and `n` = cycles, recomputing the ack keys from the War Room and keeping any
other keys. A UI reading today's row should prefer `data.metrics` (absent on the live row).

## `data` — `historian`

| key | type | meaning |
| --- | --- | --- |
| `brier_score` / `brier_n` | float\|null / int | over every (card, horizon) statement |
| `mean_p` / `observed` | float\|null | mean stated p and the fraction that happened, same sample |
| `direction_hits`, `direction_n`, `direction_hit_rate` | | statements with p ≠ 0.5 whose side was right |
| `verdict_mix` | `{supports, weakly_supports, does_not_support, contradicts}` counts | every card of the day |
| `verdicts_n`, `cards_n` | int | |
| `by_horizon` | `{"1h": {brier_score, brier_n, mean_p, observed}, ...}` | per horizon |
| `cards` | list | `{message_id, inst_id, t0, verdict, statements[{horizon, p, p0, p1, happened}], skipped[str]}` |
| `window_closes` | ISO ts | |
| `metrics` | object | `brier_score, mean_p, observed, direction_hit_rate` |

p = `distribution[h].pct_positive / 100`; happened = close of the last 5m candle by t0 + h > the one by
t0 (each ≤ 30 min old), t0 = the card's `created_at`. A zero move did not happen.

So "said 64%, 51% happened" = `data.mean_p` / `data.observed` beside `brier_score`, sample `data.brier_n`.
