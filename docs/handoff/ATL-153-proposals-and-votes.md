# ATL-153 — submit_proposal (Strategist checklist), revise/withdraw, cast_vote

For the Orchestrator/votes/approvals work (cycle state machine, vote weighting, Risk Engine call),
the Devil's Advocate, the War Room proposal card UI and the Portfolio Manager.

## Two-minute version

- `warroom.submit_proposal` and `warroom.cast_vote` are real. Logic lives in
  `services/agents/src/atlas_agents/proposals.py`; the tools in `atlas_agents/tools/warroom.py`
  write Postgres themselves (the bridge stores neither proposals nor votes) and then publish.
- Schemas are shared: `atlas_shared.proposals.TradeProposal` (entry) and `PositionAction`
  (PM kinds), Decimal prices. `stop` is required on an entry.
- revise/withdraw are **actions of `submit_proposal`**, not new tools: the §7.10 matrix is unchanged
  (`submit_proposal` = Strategist + PM, `cast_vote` = the five voters).
- A proposal is saved only when all six §7.6 items pass; otherwise the agent gets one
  `checklist_failed` error listing **every** failing item and nothing is written or published.
- New columns (migration `2026_10_30_600000_add_close_pct_and_vote_fix`): `proposals.close_pct`
  (partial_close only, 0 < x < 100, CHECKed required for partial_close) and `votes.fix` (text,
  a "change" vote's fix). The vote weighting needs `fix` to decide "+0.5 if the fix was applied".

## Tool inputs

`submit_proposal`:

```json
{"action": "submit|revise|withdraw", "kind": "entry|amend_stop|amend_targets|partial_close|close",
 "proposal": { ... }, "proposal_id": "uuid (revise/withdraw)", "summary_md": "optional card text"}
```

Entry `proposal` (TradeProposal, brief §6.3):

```json
{"inst_id": "XAU-USDT-SWAP", "side": "buy", "order_type": "limit",
 "entry": null, "entry_zone": [4010.0, 4015.0], "stop": 3988.1,
 "take_profits": [{"price": 4060, "pct": 50}, {"price": 4090, "pct": 30}],
 "time_stop_minutes": 1440, "size_suggestion_pct_equity": 2.5, "leverage_suggestion": 2,
 "expected_r_multiple": 2.4, "probability_estimate": 0.52,
 "rationale_md": "...", "invalidation": "1H close below 3995",
 "evidence": [{"type": "research_report", "id": "<message id>"}]}
```

Schema rules: limit/post_only need entry or entry_zone; stop below the entry (or zone midpoint) for
a buy, above for a sell; 1–3 take-profits, pct sum ≤ 100; size 0 < x ≤ 100; leverage ≥ 1; p 0..1.

PM `proposal` (PositionAction): `{inst_id, stop?, take_profits?, close_pct?, rule, rationale_md,
evidence}` — amend_stop needs stop, amend_targets needs take_profits, partial_close needs
close_pct. `rule` is stored in `proposals.invalidation` (that column has no other use for PM rows).
PM rows have `side`/`order_type` null. "Stops only tighten" is **not** enforced here (patch J).

`cast_vote`: `{proposal_id, vote: take|skip|change, confidence 0..1, reason, fix?}`; `change`
without a fix is refused (`invalid_vote`).

## Error codes

`invalid_proposal` (schema / wrong action args / revision changes kind or inst), `checklist_failed`,
`kind_not_allowed` (entry not from strategist; PM kind not from portfolio_manager),
`no_open_position` (PM kind with no non-zero position on the inst at the last account sync),
`unknown_proposal`, `not_author` (revise/withdraw someone else's), `proposal_not_open`
(revise/withdraw/vote outside `open_statuses` = under_review, voting), `revision_limit`,
`invalid_vote`, `content_too_long` (summary_md).

`checklist_failed` message: `"2 of 6 Strategist checklist items failed, nothing was saved: (1)
on_watchlist: ...; (5) expectancy: ..."`.

## The six checks (config `agents.strategist.checklist`)

1. `on_watchlist` — current version of today's (Baghdad) watchlist has the inst, not removed by the
   owner (pins are watchlist items with `pinned_by_owner`).
2. `technical_setup` — newest `technical_analyst` research_report card `type=technical_setup` for the
   inst; fails if none, `setup_type == "none"`, or posted ≥ `setup_max_age_minutes` (60) ago
   (`warroom_messages.created_at`).
3. `news_and_events` — any story (cluster, or lone item) linked to the inst, published in the last
   `news_lookback_hours` (24), whose code status (`news_status.decide_status`) is CONFIRMED and whose
   mean item sentiment opposes the side by ≥ `contradict_sentiment` (0.2); and any economic event with
   importance ≥ `blackout_importance` (5) scheduled in `[now, now + event_blackout_minutes]` (60; a
   settings-table `event_blackout_minutes` integer overrides it — the Laravel settings API does not
   expose that key yet). Earnings are not checked.
4. `historian_verdict` — newest `historian` card `type=history_verdict` for the inst is not
   `contradicts`. No verdict passes.
5. `expectancy` — expected R ≥ settings `min_expected_r` (falls back to the row's risk profile
   default; **no settings row fails**), and p×R − (1−p) > `min_expectancy` (0.2).
6. `no_duplicate` — no non-zero position in the same direction (net mode: sign of pos) at the last
   account sync, and no entry proposal on the same inst + side in `pending_statuses` (draft …
   partially_filled; `filled` is excluded so a closed trade never blocks). A revision excludes its
   parent.

## Writes and lifecycle

- submit: one `proposals` row, `status = under_review`, `version = 1`, `author = agent`,
  `cycle_id` = the run's correlation id when it is `<uuid>`, `cycle-<uuid>` or `cycle:<uuid>` and that
  cycle exists in the env, else null. **Orchestrator: use one of those correlation-id forms.**
- revise: parent must be open and authored by the caller; child row `version = parent + 1`,
  `parent_id`, same cycle, same kind and inst; parent → `cancelled`. A proposal that already has a
  child, or is itself a revision, is refused (`max_revisions` = 1). The full checklist reruns.
- withdraw: open + own → `cancelled`.
- vote: upsert on `(proposal_id, agent)`; a second vote replaces vote/confidence/reason/fix. Votes
  belong to a version: a revision starts with no votes. Status must be under_review or voting.
- Moving under_review → voting → passed/rejected is the Orchestrator's job; nothing here does it.

## Events

War Room message (stream `atlas.warroom`, type `warroom.message`), kind `proposal`, Main thread,
author = the agent, `content_md` = summary_md or a generated one-liner, `card`:

```json
{"type": "proposal", "proposal_id": "8b0c…", "version": 1, "parent_id": null, "cycle_id": null,
 "kind": "entry", "author": "strategist", "status": "under_review", "inst_id": "XAU-USDT-SWAP",
 "side": "buy", "order_type": "limit", "entry": null, "entry_zone": ["4010.0", "4015.0"],
 "stop": "3988.1", "take_profits": [{"price": "4060", "pct": "50"}, {"price": "4090", "pct": "30"}],
 "time_stop_minutes": 1440, "size_suggestion_pct_equity": "2.5", "close_pct": null,
 "leverage_suggestion": 2, "expected_r_multiple": "2.4", "probability_estimate": "0.52",
 "expectancy_r": "0.768", "rationale_md": "…", "invalidation": "1H close below 3995",
 "evidence": [{"type": "research_report", "id": "…"}],
 "checklist": [{"item": 1, "name": "on_watchlist", "passed": true, "detail": "on the 2026-10-30 watchlist (rank 2)"}, …6],
 "summary_md": "**BUY XAU-USDT-SWAP** v1 …", "created_at": "2026-10-30T07:12:00+00:00"}
```

Decimals are strings. PM rows: `checklist` is `[]`, `close_pct` set for partial_close.

Then on `atlas.proposals`:

```json
{"id": "…", "type": "proposal.opened", "ts": "…", "env": "demo", "source": "agent:strategist",
 "correlation_id": "cycle-<uuid>",
 "payload": {"proposal_id": "…", "version": 1, "parent_id": null, "cycle_id": "…", "kind": "entry",
             "author": "strategist", "inst_id": "XAU-USDT-SWAP", "side": "buy",
             "status": "under_review", "message_id": "<warroom envelope id>", "card": { …as above… }}}
```

A revision publishes the same `proposal.opened` (version 2, parent_id set); there is no separate
"parent cancelled" event — consumers treat `parent_id` as superseding it. Also on `atlas.proposals`:
`proposal.withdrawn` `{proposal_id, version, inst_id, status: "cancelled"}` and
`proposal.vote_cast` `{vote_id, proposal_id, version, agent, vote, confidence, fix}`. Nothing
consumes these yet.

## Not built here

Moving to voting, vote weighting, Risk Engine call, the card UI, PM stop-tightening rule, earnings in
the blackout, and an owner-editable `event_blackout_minutes` in the Laravel settings API. The DB is
committed before publishing, so a Redis failure after commit leaves a saved proposal with no card.
