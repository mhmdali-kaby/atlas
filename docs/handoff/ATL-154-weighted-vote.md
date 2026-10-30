# ATL-154 — the weighted vote (brief §7.9)

For ATL-160 (Orchestrator cycle state machine), ATL-159 (Devil's Advocate review card) and ATL-165
(vote_summary card UI).

## Two-minute version

- `atlas_agents/votes.py`. `tally(...)` is the pure Decimal arithmetic; `close_vote(ctx, proposal_id)`
  does the reads, stores `passed|rejected` on the proposal, posts one `vote_summary` War Room message
  and publishes `proposal.vote_closed` on `atlas.proposals`. The DB is committed before publishing.
- `close_vote` is plain code, not an agent tool. The Orchestrator's state machine (ATL-160) calls it
  at the vote deadline with a `ToolContext` whose `agent` is the announcer (use `orchestrator`) and
  whose correlation id is the cycle (`cycle-<uuid>`). It does **not** call the Risk Engine and does
  not touch the cycle row; it does not move under_review → voting either (it accepts both).
- It refuses `unknown_proposal` and `proposal_not_open` (status not in
  `agents.votes.closable_statuses` = under_review, voting) with `ToolError`, publishing nothing.
- **ATL-159, the contract you must post**: a War Room message by `devils_advocate` whose `card` has
  `"type": "da_review"`, `"proposal_id": "<uuid of the version reviewed>"` and
  `"risk_rating": <JSON integer 1-5>`. Any message kind works (the query is by author + card type).
  The newest such card on **any version** of the proposal counts. A missing card, or a rating that
  is not an integer 1-5 (`"4"` as a string does not count), makes the vote fail with
  `da_rating_missing`. Rating > `da_max_rating` (4) fails with `da_rating_too_high`.

## Rules as built

- Voters: `agents.votes.voters` = technical_analyst, derivatives_sentiment, news_intelligence,
  historian, devils_advocate. Votes from anyone else are ignored.
- Votes are read from the whole version chain (the proposal and its parents). For each voter the
  vote on its newest voted version counts.
- Points: take 1, skip 0, change 0.5 **when the vote was cast on an earlier version than the one
  being closed** (the Strategist revised after it, so "fix applied"), else 0. Code cannot judge
  whether the revision addresses that exact fix; a revision after the change vote is the signal.
- Weight = confidence (clamped 0..1) × reliability. Reliability = the newest
  `agent_scores.data.reliability_weight` for the voter in the env within the last 30 Baghdad days,
  clamped 0.5–1.5, default 1.0. **Nothing writes `reliability_weight` yet** (the Phase 3 scorecards
  should), so every weight is currently 1.0 × confidence.
- Missing voter: skip at confidence 0.5, in the score (0) and in the total weight.
- ratio = Σ(points × weight) / Σweight; 0 when the total weight is 0. Pass = ratio ≥ threshold AND
  DA rating present and ≤ 4.
- Threshold: flat settings key `agents.vote_threshold` (0..1) in `settings.data` for the env, else
  `agents.votes.threshold` = 0.6. The Laravel settings API does not expose that key yet (Settings →
  Agents "vote threshold" belongs to whoever builds that field).

## The vote_summary message (stream `atlas.warroom`, type `warroom.message`)

Main thread, `author` = ctx.agent, `kind` = `vote_summary`, `content_md` = a one-paragraph English
summary. `card` (decimals are strings, at most 4 places):

```json
{"type": "vote_summary", "proposal_id": "8b0c…", "version": 2, "cycle_id": "…|null",
 "kind": "entry", "inst_id": "XAU-USDT-SWAP", "side": "buy", "result": "rejected",
 "ratio": "0.5556", "threshold": "0.6", "weighted_score": "2.5", "total_weight": "4.5",
 "da_rating": 3, "da_max_rating": 4, "reasons": ["ratio_below_threshold"],
 "voters": [
   {"agent": "technical_analyst", "vote": "take", "missing": false, "confidence": "1",
    "reliability": "1", "weight": "1", "points": "1", "score": "1", "voted_version": 2,
    "fix": null, "fix_applied": false},
   {"agent": "historian", "vote": "change", "missing": false, "confidence": "1",
    "reliability": "1", "weight": "1", "points": "0.5", "score": "0.5", "voted_version": 1,
    "fix": "widen the stop to 96", "fix_applied": true},
   {"agent": "devils_advocate", "vote": "skip", "missing": true, "confidence": "0.5",
    "reliability": "1", "weight": "0.5", "points": "0", "score": "0", "voted_version": null,
    "fix": null, "fix_applied": false}
 ]}
```

`reasons` is empty when passed; otherwise any of `ratio_below_threshold`, `da_rating_missing`,
`da_rating_too_high`. `voters` is always the five voters in config order.

## proposal.vote_closed (stream `atlas.proposals`)

```json
{"id": "…", "type": "proposal.vote_closed", "ts": "…", "env": "demo", "source": "agent:orchestrator",
 "correlation_id": "cycle-<uuid>",
 "payload": {"proposal_id": "…", "version": 2, "cycle_id": "…", "inst_id": "XAU-USDT-SWAP",
             "result": "passed", "ratio": "0.8", "threshold": "0.6", "da_rating": 3,
             "reasons": [], "message_id": "<vote_summary envelope id>"}}
```

## Config (`config/defaults.yaml` → `agents.votes`)

voters, scores {take, change_applied, change, skip}, missing_confidence 0.5, threshold 0.6,
threshold_setting `agents.vote_threshold`, reliability_days 30, reliability_min 0.5,
reliability_max 1.5, reliability_default 1, da_agent devils_advocate, da_card_type da_review,
da_max_rating 4, closable_statuses [under_review, voting].
