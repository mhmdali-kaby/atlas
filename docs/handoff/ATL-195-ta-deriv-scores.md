# ATL-195 — Technical Analyst and Derivatives & Sentiment daily scores

Two more agents now get one `agent_scores` row per env and Baghdad day, written by the same
00:15 Baghdad job as Market Scout and News Intelligence (ATL-110, `docs/handoff/ATL-110-agent-scores.md`).

- Code: `services/agents/src/atlas_agents/scorecards/technical.py` and `scorecards/derivatives.py`
  (each exposes `AGENT` and `score_day(ctx, day)`), registered in `atlas_agents/agents/scores.py`
  with `scoring.register_scorer(...)` plus a `@HOOKS.schedule(<slug>, "scoring")` hook. The next
  scorer (ATL-196/197) adds its own module and its own two blocks in `agents/scores.py`; nothing
  else needs editing.
- Settings: `agents.scoring.technical` and `agents.scoring.derivatives` in `config/defaults.yaml`.
- Source: War Room `research_report` messages by the agent (`author_type = 'agent'`) whose
  `card.type` is `technical_setup` / `positioning_report`, `created_at` inside the Baghdad day.
  Each card is scored from its own `created_at` (t0).
- A day is skipped (no row) until its windows close: TA at day end + 24h, Derivatives at day end +
  48h, i.e. the 00:15 job scores TA for the day before yesterday and Derivatives for three days ago.
  `lookback_days` is 3, so both get scored once their window closes.

## Row columns

| agent slug | `n` | `hit_rate` | `brier_score` |
| --- | --- | --- | --- |
| `technical_analyst` | resolved triggered setups (target + stop) | target-before-stop rate | null |
| `derivatives_sentiment` | direction calls (non-neutral sides of `supports_direction`) | supports-direction accuracy | Brier of every squeeze probability |

A `null` rate with `n = 0` is normal (e.g. a TA day with only "no setup" cards).

## `data` keys — `technical_analyst`

| key | type | meaning |
| --- | --- | --- |
| `target_before_stop_rate` / `target_before_stop_n` | float\|null / int | TP1 hits ÷ (TP1 hits + stops), over triggered setups that resolved within 24h |
| `target_hits`, `stop_hits` | int | the two parts of that sample |
| `triggered_n`, `open_n`, `not_triggered_n` | int | triggered (incl. open), triggered but unresolved, never reached the entry zone |
| `level_hold_rate` / `level_hold_n` | float\|null / int | stated levels (`levels_used` with a price) that held within 0.25 ATR(1H) over 24h |
| `avg_realised_r` / `realised_r_n` | float\|null / int | mean realised R of resolved setups (stop = −1, TP1 = (TP1−fill)/risk) |
| `avg_expected_r` / `expected_r_n` | float\|null / int | mean stated `expected_r` of the same setups |
| `avg_r_gap` | float\|null | `avg_realised_r − avg_expected_r` |
| `no_setup_accuracy` / `no_setup_n` | float\|null / int | "none" cards whose 24h closes stayed inside the nearest level below/above (± tol) |
| `cards_n` | int | cards found that day |
| `setups` | list | per card: `message_id, inst_id, setup_type, direction, t0, ref_price, atr, tolerance, levels[{price, held}]`, then for long/short `outcome (target\|stop\|open\|not_triggered), fill, realised_r, expected_r, triggered_at, resolved_at`, or for none `range [lo, hi]\|null, inside` |
| `skipped` | list | `{message_id, reason}` for cards with no price data |
| `window_closes` | ISO ts | when the day became scorable |
| `metrics` | object | flat copy for the prompt scorecard: `target_before_stop_rate, level_hold_rate, avg_realised_r, avg_expected_r, no_setup_accuracy` |

## `data` keys — `derivatives_sentiment`

| key | type | meaning |
| --- | --- | --- |
| `supports_accuracy` / `supports_n` / `supports_correct` | float\|null / int / int | direction calls right on the 24h close-to-close sign (zero move = miss) |
| `squeeze_calls_n`, `squeeze_call_hits`, `squeeze_call_hit_rate` | int, int, float\|null | squeeze_risk ≥ 0.6 calls followed by a ≥ 2 ATR(1D) move that way within 48h |
| `mean_squeeze_call_p` | float\|null | mean stated probability of those calls |
| `brier_score` / `brier_n` | float\|null / int | Brier over every stated squeeze probability (calls or not) |
| `mean_p` | float\|null | mean stated squeeze probability, same sample as `brier_score` |
| `observed` | float\|null | fraction of that sample that happened |
| `cards_n`, `skipped`, `window_closes` | | as for TA |
| `reports` | list | per card: `message_id, inst_id, t0, p0, p1?, calls[{side, view, predicted (+1/−1), correct}], atr?, squeezes[{kind, p, call, happened}]` |
| `metrics` | object | `supports_accuracy, squeeze_call_hit_rate, squeeze_brier, mean_p, observed` |

So the scorecard line "said 64%, 51% happened" is `data.mean_p` / `data.observed` beside the
row's `brier_score`, with `data.brier_n` as its sample.

## Things worth knowing

- Ambiguous bars are scored conservatively: a 15m bar touching both stop and TP1 is a stop; on the
  bar that fills the entry only the stop is checked.
- The fill is the trigger bar's open clamped into the entry zone, so realised R uses that fill, not
  the zone midpoint the card's `r_multiple`s use.
- TA posts a card every 30-min cycle, so one instrument can appear many times a day; every card is
  scored on its own.
- `scoring.upsert_score` now takes `brier_score=` (default null); `scoring.close_at`, `atr_at` and
  `day_bounds` are public helpers for the next scorers.
