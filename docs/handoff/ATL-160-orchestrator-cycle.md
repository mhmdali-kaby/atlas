# ATL-160 — the Orchestrator's cycle state machine (brief §7.9, §8)

For ATL-161 (Orchestrator agent), ATL-162 (approvals API + expiry sweeper), ATL-163 (`/cycle now`),
ATL-164/ATL-180 (approval card, pill and toast), ATL-169 (Portfolio Manager).

## Two-minute version

- All code, no model: `services/agents/src/atlas_agents/orchestrator/cycle.py` (`CycleRunner`,
  `start`, `due_slot`), hooks in `atlas_agents/agents/orchestrator.py`, signed client in
  `atlas_agents/orchestrator/trading_core.py`. Config: `agents.orchestrator`, `agents.schedule.analysis_*`,
  `autonomy.approval_timeout_minutes` in `config/defaults.yaml`.
- The hooks belong to the `orchestrator` agent, which is **off by default**: no cycle runs until the
  owner enables the Orchestrator in Settings → Agents.
- A risk-approved proposal at autonomy 2 ends **`awaiting_owner` with `proposals.approval_expires_at`**
  (now + 15 min by default). Autonomy 1 → new proposal status **`advise_only`**. A risk rejection →
  `rejected`. **Nothing here calls the Executor**; ATL-162's approve is the only way to send.
- trading_core has a new signed endpoint, `POST /internal/risk/evaluate/{proposal_id}` (below). The
  ATL-128 `/internal/risk/check` creates a throw-away manual proposal, so it is not what the cycle uses.

## States (`cycles.state`) and what is stored

`cycle_start → research_requests_sent → research_collected → proposals_open → reviews_done → voting →
vote_closed → risk_check → awaiting_owner → cycle_closed`. Early closes go straight to `cycle_closed`.
`executing` is never reached yet (send_now is treated as awaiting_owner while autonomy 3–4 are off).

New columns (migration `2026_10_31_100000_add_orchestrator_cycle_columns`):

| column | example |
| --- | --- |
| `cycles.slot_at` | `2026-11-01T05:10:00Z` (08:10 Baghdad); null for manual cycles; unique with env |
| `cycles.transitions` | `{"cycle_start": "…Z", "research_requests_sent": "…Z", …}` (jsonb: key order is not kept) |
| `cycles.deadlines` | `{"research": "…Z", "review": "…Z", "vote": "…Z"}` |
| `cycles.timeouts` | `[{"step": "research", "agent": "derivatives_sentiment", "inst_ids": ["BTC-USDT-SWAP"], "deadline": "…Z", "at": "…Z"}]` |
| `cycles.outcome` / `outcome_reason` | `awaiting_owner` / `awaiting approval on BTC-USDT-SWAP` |
| `proposals.approval_expires_at` | timestamptz, set only with status `awaiting_owner` |
| `proposals.status` | CHECK now also allows `advise_only` |

Timeout `step` values: `research` (agent + inst_ids), `proposals` (strategist), `review`
(devils_advocate + proposal_id), `vote` (each missing voter + proposal_id), `risk_check` (agent
`trading_core` + `error`). Outcomes: `no_watchlist`, `no_new_proposals`, `no_proposals`,
`nothing_passed`, `awaiting_owner`, `advise_only`, `risk_rejected`, `error`.

## Events

`cycle.state_changed` on **atlas.system** (bridged to `private-system`), every transition:

```json
{"type": "cycle.state_changed", "source": "system:orchestrator", "correlation_id": "cycle-<uuid>",
 "payload": {"cycle_id": "<uuid>", "state": "voting", "previous": "reviews_done", "at": "…Z",
             "deadlines": {"vote": "…Z"}, "proposal_ids": ["<uuid>"]}}
```

Extra payload keys by state: `cycle_start` kind; `research_requests_sent` agents, skipped, inst_ids,
deadlines; `research_collected` timed_out; `proposals_open` proposal_ids (+ deadlines.review);
`reviews_done` unreviewed; `vote_closed` passed; `awaiting_owner` inst_ids; `cycle_closed` outcome, reason.

Each transition also posts one War Room line (atlas.warroom `warroom.message`): `author_type: system`,
`author: orchestrator`, source `system:orchestrator`, `kind: text`, Main thread,
`card: {"type": "cycle_state", "cycle_id": "<uuid>", "state": "<state>"}`, e.g. "Vote open on
BTC-USDT-SWAP until 2026-11-01T05:23:00Z."

The risk result is posted as `kind: risk_check`, author `orchestrator` (agent), card = trading_core's
decision body with `"type": "risk_check"` (no `ok`): `proposal_id, risk_check_id, env,
settings_version, result, decision, reason, failed_check, binding_cap, final_contracts,
final_notional_usd, leverage, checks[9]`.

On **atlas.proposals**, after the status is written:

```json
{"type": "proposal.awaiting_owner", "source": "system:orchestrator", "correlation_id": "cycle-<uuid>",
 "payload": {"proposal_id": "<uuid>", "version": 1, "cycle_id": "<uuid>", "inst_id": "BTC-USDT-SWAP",
             "side": "buy", "risk_check_id": "<uuid>", "result": "resized", "decision": "awaiting_owner",
             "final_contracts": "3", "final_notional_usd": "300", "leverage": 2,
             "approval_expires_at": "2026-11-01T05:40:00Z"}}
```

`proposal.advise_only` has the same payload with `approval_expires_at: null`.

## trading_core `POST /internal/risk/evaluate/{proposal_id}`

Signed like every /internal call (empty body). Runs `evaluate(proposal_id)` with the default
evaluable statuses (`passed`, `risk_checked`), so it re-checks an Edit too. 200 = the same body as
`/internal/risk/check`; 409 `{"ok": false, "error": "risk_engine_error", "detail"}` (unknown id,
wrong status/kind, no settings); 404 `unknown_proposal` for a malformed id. The signer moved to
`atlas_shared.internal_api` (`sign`, `signed_headers`); `trading_core.security` re-exports it.
The agents process reads `TRADING_CORE_URL` (default `agents.orchestrator.trading_core_url`) and
`INTERNAL_SHARED_SECRET` from its environment (`./atlas up` exports backend/.env).

## Starting a cycle

- Scheduled: hook `analysis_cycle` every `check_seconds` (60) → `due_slot(now)`.
- `/cycle now` (ATL-163): publish an envelope of type **`orchestrator.cycle_now`** on **atlas.warroom**
  (any payload). It starts a `manual` cycle, or posts "A cycle is already running…" when one is.
- In code: `cycle.start(host, "manual")` → cycle id or None; `cycle.settled()` waits for it.

## Not built here

- Approval expiry and the halfway reminder (ATL-162), the Executor call on approve (ATL-162).
- Asking voters other than the Devil's Advocate to vote: the cycle waits up to 3 min and
  `close_vote` counts a missing voter as skip 0.5. Nothing prompts the analysts to cast_vote yet.
- PM amend/close proposals in the cycle (only `kind = entry` with this cycle's id are followed).
- The Orchestrator agent's model work (routing, "Where we are", loop stopping) — ATL-161.
