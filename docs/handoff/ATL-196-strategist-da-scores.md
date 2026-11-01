# ATL-196 — Strategist and Devil's Advocate daily scores, and paper trades

Code: `services/agents/src/atlas_agents/scorecards/` — `paper.py` (simulation + `paper_trades`
upsert), `outcomes.py` (what happened to each proposal, shared), `strategist.py`,
`devils_advocate.py`. Hooks `score_strategist` / `score_devils_advocate` in
`atlas_agents/agents/scores.py`, schedule name `scoring` (`agents.schedule.scoring`, 00:15 Baghdad),
registered under the `strategist` and `devils_advocate` slugs (a disabled agent is not scored).
Config: `agents.scoring.paper`, `agents.scoring.strategist`, `agents.scoring.devils_advocate` in
`config/defaults.yaml`. Migration: `backend/database/migrations/2026_11_02_300000_create_paper_trades_table.php`;
SQLAlchemy model `atlas_shared.db.PaperTrade`.

## Two-minute version

- The **sample** for both agents is the Strategist's entry proposals created on the Baghdad day,
  latest version of each chain (no revision after it). A proposal belongs to the day of its latest
  version's `created_at`.
- Each run re-scores the last `lookback_days` (7) days, so a trade that closes within a week of being
  proposed is counted. Days that have not ended are skipped. Re-runs upsert: one `agent_scores` row
  per agent/env/day and one `paper_trades` row per proposal.
- **Paper trades** are written by the Strategist's (and DA's) scorer before scoring. A not-filled
  limit entry is stored with `status = not_filled` and **no R** — it is not a loss and is left out of
  regret, Brier and the DA's rates.
- `upsert_score` in `atlas_agents/scoring.py` gained an optional `brier_score` argument (it now also
  overwrites `brier_score` on re-score).

## `paper_trades` columns

| column | type | meaning |
| --- | --- | --- |
| `id` | uuid | |
| `env` | demo\|live | |
| `proposal_id` | uuid, **unique**, FK proposals (cascade) | the simulated (latest) version |
| `version` | int | that version's number |
| `inst_id`, `side` (buy\|sell), `order_type` (market\|limit\|post_only) | | from the proposal |
| `proposal_status` | string | rejected \| expired \| advise_only (config `paper.statuses`) |
| `proposal_status_reason` | string, null | e.g. risk_rejected, owner_rejected, approval_timeout, advise_only; null for a vote rejection |
| `status` | filled \| not_filled | |
| `decided_at` | timestamptz | the latest version's created_at; the simulation starts here |
| `entry_px`, `entry_ts` | decimal, timestamptz, null when not filled | fill price; the fill bar's open time |
| `stop` | decimal | |
| `exit_px` | decimal, null | size-weighted average exit price |
| `exit_ts` | timestamptz, null | close time of the bar of the last exit |
| `exit_reason` | stop \| take_profit \| time_stop \| horizon \| not_filled | what closed the last part |
| `realised_r` | decimal(20,8), null | `r_gross - fees_r`; null exactly when not filled (CHECKed) |
| `r_gross` | decimal(20,8), null | price result in R of `|entry - stop|` (a clean stop = exactly -1) |
| `fees_r` | decimal(20,8), null | taker fee on entry + exits, in R |
| `fee_rate` | decimal | per side, `backtest.taker_fee_rates[instType]` (no trade-fee rate is stored yet) |
| `tp1_hit` | bool, null | TP1 reached before the stop |
| `bar` | 1m \| 15m | candles walked (15m only when there were no 1m bars) |
| `legs` | jsonb | `[{"reason": "take_profit", "price": "110", "ts": "…", "pct": "50", "r": "1.00000000"}, …]` |
| `simulated_at`, `created_at`, `updated_at` | timestamptz | |

Simulation rules (all in `paper.simulate`): market = next 1m open at/after `decided_at`; limit/post_only
= the entry, else the zone edge the Executor uses (high for a buy), filled only in a bar that trades
**through** it before `executor.entry_timeout_minutes` (settings row, else default 30). Stop first in
any bar that touches both. TPs at their price for their pct; the rest closes at the last bar's close
by the time stop, else after `paper.max_hold_hours` (72, `horizon`). The stop does not trail.

## `agent_scores.data` keys

Every row also has `data.metrics` (the headline numbers) and `data.proposals` (per-proposal detail).

**strategist** — `n` = submitted proposals, `hit_rate` = `observed`, `brier_score` column set.

```json
{"avg_realised_r": 0.5, "taken_n": 2,
 "rejection_rate": 0.2, "risk_rejected_n": 1, "submitted_n": 5,
 "brier_score": 0.235, "mean_p": 0.65, "observed": 0.5, "brier_n": 4,
 "regret_r": 0.47975, "regret_n": 2,
 "pending": ["<proposal id still open / taken and not closed>"],
 "proposals": [{"proposal_id": "…", "version": 1, "inst_id": "…", "side": "buy", "status": "closed",
                "risk_result": "approved", "probability": 0.6, "outcome": "taken",
                "realised_r": 2.0, "tp1_hit": true, "source": "journal"}],
 "metrics": {"avg_realised_r": 0.5, "rejection_rate": 0.2, "brier_score": 0.235, "regret_r": 0.47975}}
```

- `avg_realised_r`: taken proposals that have closed. R = sum of the PM post-mortems'
  `data.actual.realised_r` for the chain (ATL-194); without one, the full closes in `positions_closed`
  (sum realised_pnl / sum |entry_px − stop| × size × ctVal).
- `rejection_rate`: newest risk check on the latest version is `rejected` / submitted.
- `brier_score` / `mean_p` / `observed` / `brier_n`: probability_estimate vs TP1-before-stop, taken
  (walked on candles from the first entry fill at the order's avg price) and filled paper trades.
- `regret_r` / `regret_n`: mean `realised_r` of the day's filled paper trades.
- `outcome` per proposal: `taken`, `paper`, `not_filled`, `pending`, `none` (cancelled/withdrawn or
  never reached a decision).

**devils_advocate** — `n` = rated proposals with an outcome, `hit_rate` = `accuracy`, no Brier.

```json
{"high_n": 2, "high_lost": 1, "high_won": 1, "caught_rate": 0.5, "false_alarm_rate": 0.5,
 "low_n": 3, "low_won": 1, "low_lost": 2, "low_win_rate": 0.333, "miss_rate": 0.667,
 "accuracy": 0.4, "rated_n": 5, "high_rating": 4, "low_rating": 2, "scratch_r": "0.1",
 "pending": ["<rated, no outcome yet or not filled>"],
 "proposals": [{"proposal_id": "…", "inst_id": "…", "risk_rating": 4, "outcome": "paper",
                "realised_r": -1.0195, "result": "loss"}],
 "metrics": {"false_alarm_rate": 0.5, "miss_rate": 0.667, "caught_rate": 0.5, "low_win_rate": 0.333}}
```

- Rating = `risk_rating` of the newest `da_review` card (any version of the chain; a re-rate wins).
- win R ≥ `scratch_r`, loss R ≤ −`scratch_r`, scratch otherwise (scratches count in `high_n`/`low_n`
  but in neither won nor lost). Ratings of 3 count only in `rated_n`/`n`.
- **false alarm** = rated ≥ 4 and won; **miss** = rated ≤ 2 and lost — separate keys, as the brief asks.

## Not built here

No API or screen (ATL-200's `/api/agents/scores` returns `data` as stored). Reliability weights
(the vote's 30-day weight) are another task; they can read `paper_trades` and these keys directly.
PM stop trailing is not simulated in paper trades, and the paper fill uses taker fees on both sides.
