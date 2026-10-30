# ATL-159 — the Devil's Advocate (brief §7.7)

For ATL-160 (Orchestrator cycle), ATL-154's vote (it reads this card), the War Room card UI and the
Strategist (who replies to the review).

## Two-minute version

- Code: `atlas_agents/devils_advocate.py` (precompute, code rules, reviewer, `review()` / `rerate()`),
  hooks in `atlas_agents/agents/devils_advocate.py`, card schema `atlas_shared.devils_review`.
  Config `agents.devils_advocate` in `config/defaults.yaml`. The agent is **off by default**
  (`agents.roster`), like the Technical Analyst; turn it on in Settings → Agents.
- **Trigger 1:** `proposal.opened` on `atlas.proposals` (new or revised version). The supervisor now
  consumes `atlas.proposals` (`Supervisor.consume_proposals`, started by `serve`); only agents that
  registered the event type see an envelope. An envelope older than `review_deadline_seconds` (60)
  when picked up is skipped and logged (a restart replaying the stream does not review stale ones).
- **Trigger 2:** a `warroom.message` of kind `text` by `strategist` whose `reply_to` is the row id of a
  round-1 `da_review` message → exactly one re-rate (round 2) per proposal version. A second reply, a
  reply to the re-rate, or a stored round-2 card (after a restart) starts nothing. The Strategist's
  `warroom.reply` tool already produces exactly this (kind text, reply_to = row id).
- **The model does not call `cast_vote` or post the card.** It answers with a fenced JSON
  `DevilsReview`; code validates (all 10 items; one retry; a second invalid card → run `degraded`,
  agent degraded), applies the rules below, **casts the vote through `warroom.cast_vote`** (so
  `proposal.vote_cast` is published as usual, author `devils_advocate`) and posts the card.
- The round-1 review is a `research_report` reply under the proposal's War Room card
  (`reply_to` = that card's row id, same thread; the `message_id` of `proposal.opened` is resolved to
  the row, waiting up to `agents.mentions.parent_wait_seconds` for the bridge). The re-rate replies
  under the round-1 review.

## Code rules after the model (`enforce_review`), each recorded in `card.corrections`

| rule | what |
| --- | --- |
| `rating_guide` | `risk_rating` := §7.7 guide from item severities: 5 if any `loss_gt_1r`; 4 if ≥ 2 `serious`; 3 if 1; 2 if only `minor`; 1 if none. `{from, to, loss_gt_1r: [item nos], serious: [...]}` |
| `fix_applied_impossible` | `fix_applied` forced false on round 1 of version 1 |
| `take_needs_low_rating` | `take` with rating > `take_max_rating` (2) and no applied fix → `change` (with a fix) or `skip`. `{from, to, risk_rating, take_max_rating}` |
| `change_without_fix` | `change` with no fix → `skip` |

The vote cast is the corrected one: `cast_vote(proposal_id, vote, confidence, reason="Risk N/5. …",
fix=<fix when change>)`. If the vote is refused (proposal revised/closed meanwhile) the card is still
posted with `meta.vote_error`.

## The card (`card.type = "da_review"`) in full

```json
{"type": "da_review", "proposal_id": "8b0c…", "version": 1, "inst_id": "XAU-USDT-SWAP",
 "round": 1, "rerate_of": null,
 "items": [
   {"item": 1, "name": "event_risk", "status": "problem", "severity": "serious",
    "detail": "CPI at 15:30 Baghdad, 3 h into the 24 h window"},
   {"item": 2, "name": "crowding", "status": "ok", "severity": "none", "detail": "OK"},
   … items 3-10: history, stop_quality, chasing, correlation, liquidity, priced_in, data_quality,
   overconfidence (always all 10, sorted by item) …],
 "strongest_objection": "CPI lands inside the window with a stop 0.9 x the 4h noise.",
 "risk_rating": 4, "fix": "Wait until after CPI", "fix_applied": false,
 "vote": "change", "confidence": 0.7, "reason": "…",
 "precomputed": {"as_of": "…",
   "stop_noise": {"bar": "4H", "noise_mult": "0.8", "available": true, "n_bars": 30,
                  "reference_price": "100.2", "stop_distance": "4", "median_range": "2",
                  "threshold": "1.6", "ratio": "2.5", "verdict": "ok", "data_as_of": "…"},
   "chasing": {"atr_bar": "1H", "available": true, "atr": "2.1", "last_close": "100.3",
               "entry": "100.2", "entry_vs_last_close_atr": "0.05", "nearest_level": "99.8",
               "entry_to_level_atr": "0.19", "level_source": "technical_setup.levels_used",
               "move_bars": 24, "move_before_entry_atr": "1.2", "entry_limit_atr": "1.5",
               "move_limit_atr": "3", "flags": [], "data_as_of": "…"},
   "correlation": {"available": true, "lookback_days": 30, "threshold": "0.8",
                   "asset_class": "commodity", "positions": [{"inst_id": "XAG-USDT-SWAP",
                   "direction": "long", "asset_class": "commodity", "corr_30d": "0.86",
                   "over_threshold": true}], "correlated_count": 1,
                   "same_class_same_direction": 1, "positions_as_of": "…"},
   "events": {"available": true, "window_start": "…", "window_end": "…", "window_minutes": 1440,
              "min_importance": 4, "economic": [{"name": "CPI", "country": "US", "importance": 5,
              "scheduled_at": "…", "minutes_from_now": 180}], "earnings": []}},
 "corrections": [{"rule": "take_needs_low_rating", "from": "take", "to": "change",
                  "risk_rating": 4, "take_max_rating": 2}],
 "meta": {"numguard…": "…", "elapsed_seconds": 12.4, "late": false}}
```

Decimals in `precomputed` are strings. A part without data is `{"available": false, "reason":
"no_stop_or_entry"|"no_candles"|"atr_unavailable"|"error", …}`. `risk_rating` is always a JSON
integer, which is what ATL-154's `close_vote` needs; it reads the newest `da_review` on any version.
Round 2 has `round: 2` and `rerate_of` = the round-1 review's row id.

## Item validation (`ReviewItem`)

`item` 1-10 must match `name` (the order above); `ok` ⇒ severity `none`; `problem` ⇒ severity
`minor|serious|loss_gt_1r` and a `detail` containing a digit. Missing items fail with
"checklist items missing: 7 (liquidity); …", which is what the retry prompt shows the model.

## Not built here

- PM-requested open-position reviews ("strongest reason to close / hold"); PM proposals
  (amend/close) get the same review as entries, with chasing/stop parts using the last close.
- DA scoring (false-alarm / miss rates) — Phase 3 scorecards.
- The prompt file `prompts/devils_advocate.md` stays the brief's text verbatim (a test pins it);
  the card format and the precomputed table are in the run prompt.
- Note for whoever touches the supervisor: `Supervisor.tool_context()` does not carry the host's
  publisher, so a hook that publishes through `ctx.publish` goes to the real bus; this module uses
  `dataclasses.replace(ctx, publish=host.publish)` (`voting_context`).
