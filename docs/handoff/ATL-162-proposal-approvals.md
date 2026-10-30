# ATL-162 — /api/proposals: approve, reject, edit, ask the team; the approval sweeper

For ATL-164 (proposal card), ATL-180 (approval pill + toast), ATL-163 (`/why`), ATL-169 (PM).

## Two-minute version

- Laravel: `routes/api/proposals.php`, `ProposalController`, `App\Support\Proposals\{ProposalView,
  ProposalRules,Dec}`, `ProposalPolicy`, models `Proposal`, `Vote`, `RiskCheck`, `Order`
  (read-only except the owner's writes). All routes `auth:sanctum` (401 otherwise); writes are
  throttled `proposals-write` (`proposals_api.writes_per_minute`, 30).
- **Approve never sends anything from Laravel.** It checks, then makes one signed call to
  trading_core `POST /internal/proposals/{id}/execute`, which re-runs the Risk Engine and only
  calls the Executor when the fresh check approves. A rejected re-check closes the proposal
  `rejected / risk_rejected` and the Executor is not called.
- Use `proposal.actions` from the API to enable buttons; it applies exactly the rules the endpoints
  enforce. `actions.approve_blocked_reason` says why Approve is off.
- **Edit makes a new version.** Version N is `cancelled / revised`; version N+1 (author `owner`)
  is risk-checked and, if it passes, is `awaiting_owner` with the **same `approval_expires_at`**
  (an edit does not extend the window). Approve/reject the new id from then on.
- trading_core's sweeper (job `approvals`, every `approvals.sweep_seconds` = 15) posts one
  reminder at half the window and rejects at `approval_expires_at` with
  `status_reason = approval_timeout`.

## New proposals columns (migration `2026_10_31_200000_add_proposal_approval_columns`)

| column | values |
| --- | --- |
| `status_reason` | `owner_rejected`, `approval_timeout`, `risk_rejected` (re-check on approve or edit), `revised` (an edit replaced it), `advise_only`, `execution_<executor reason>` e.g. `execution_trading_paused` |
| `owner_note` | the owner's reject reason text |
| `decided_by` / `decided_at` | `owner` or `system`, when |
| `approval_reminded_at` | set once by the sweeper |

## Endpoints (all JSON; decimals are strings without trailing zeros)

### `GET /api/proposals?env=demo&status=awaiting_owner,advise_only&inst=BTC-USDT-SWAP&date=2026-10-31&limit=50&cursor=`

Cursor-paginated, newest first. `date` is a Baghdad day. `{"data": [<summary>], "links", "meta"}`.

Summary object:

```json
{"id": "9f…", "env": "demo", "cycle_id": "c1…", "version": 1, "parent_id": null, "kind": "entry",
 "author": "strategist", "inst_id": "BTC-USDT-SWAP", "side": "buy", "order_type": "limit",
 "entry": "4000", "entry_zone": null, "stop": "3960",
 "take_profits": [{"price": "4100", "pct": "100"}], "time_stop_minutes": 1440,
 "size_suggestion_pct": "5", "close_pct": null, "leverage_suggestion": 3, "expected_r": "2.5",
 "probability": "0.5", "rationale_md": "…", "invalidation": "…", "evidence": [],
 "status": "awaiting_owner", "status_reason": null, "owner_note": null, "decided_by": null,
 "decided_at": null, "approval_expires_at": "2026-11-01T05:40:00.000000Z",
 "approval_reminded_at": null, "created_at": "…Z", "updated_at": "…Z",
 "risk_check": {"id": "rc…", "proposal_id": "9f…", "settings_version": 7, "result": "resized",
   "decision": "awaiting_owner", "reason": "…", "final_contracts": "3", "final_notional_usd": "300",
   "leverage": 2, "checks": [ …9 entries… ], "created_at": "…Z"},
 "actions": {"approve": true, "approve_blocked_reason": null, "edit": true, "reject": true, "ask_team": true}}
```

`approve_blocked_reason`: `not_awaiting_owner` · `approval_expired` · `risk_check_missing` ·
`settings_changed` (settings saved after the check; Edit re-runs it).

### `GET /api/proposals/{id}`

`{"data": <summary> + {"versions": [{id, version, author, status, status_reason, stop,
size_suggestion_pct, take_profits, created_at}], "votes": [{id, proposal_id, agent, vote,
confidence, reason, fix, created_at}], "da_review": {message_id, proposal_id, risk_rating,
content_md, card, created_at} | null, "risk_checks": [...all, oldest first], "orders": [{id,
proposal_id, risk_check_id, cl_ord_id, ord_id, inst_id, side, ord_type, purpose, px, sz,
filled_sz, avg_px, lever, state, reduce_only, last_error_code, last_error_msg, created_at,
updated_at}]}}`. Votes, checks and orders cover **every version** in the chain; `da_review` is the
newest `da_review` card on any version.

### `POST /api/proposals/{id}/approve` (no body)

200 sent / 409 refused / 502 failed:

```json
{"ok": true, "error": null, "message": null,
 "execution": {"ok": true, "status": "sent", "reason": "sent", "detail": "Order sent; stop confirmed on OKX.",
   "risk_check_id": "rc2…", "order_id": "o…", "cl_ord_id": "e…", "ord_id": "900001", "state": "live",
   "stop_verified": true, "okx_code": null, "proposal_id": "9f…",
   "risk_check": {<the re-check's decision body, as /internal/risk/check>}},
 "proposal": <summary>}
```

Laravel-side 409 (no call made): `{"ok": false, "error": "<approve_blocked_reason>", "message", "proposal": <summary>}`.
trading_core refusal → 409 `error: execution_refused` with `execution.reason` one of
`not_awaiting_owner`, `approval_expired`, `risk_engine_error`, `risk_rejected`, `advise_only`, or
any Executor refusal reason (ATL-143 handoff). 502 `execution_failed` (OKX refused / stop
unverified), `trading_core_error`, `trading_core_unavailable`.

### `POST /api/proposals/{id}/reject` `{"reason": "CPI in an hour."}` (required, ≤ 1000)

Allowed from `awaiting_owner` and `advise_only`; else 409 `not_rejectable`. 200 `{"ok": true, "proposal": <summary>}`.

### `PATCH /api/proposals/{id}` `{"size": "2.5", "stop": "3975", "targets": [{"price": "4100", "pct": "50"}]}`

Any one or more fields. `size` = size suggestion in % of equity (0 < x ≤ 100; the Risk Engine still
decides contracts). 422 (field `stop`): `stop_widened` (a buy's stop lower / a sell's higher than
now), `stop_wrong_side`. 422 (`targets`): `target_wrong_side`, `targets_over_100`. 409:
`not_editable` (not an entry), `not_awaiting_owner`, `approval_expired`. If trading_core can't run
the re-check: 409 `risk_engine_unavailable` / 502 `trading_core_*`, and the original is restored
untouched. 200 `{"ok": true, "proposal": <detail of the new version>, "risk_check": <decision body>}`;
the new version's status is `awaiting_owner`, `rejected` (`risk_rejected`) or `advise_only`.

### `POST /api/proposals/{id}/ask-team` `{"question": "Why not wait for CPI?"}`

Posts an owner (`author_type: human`) message in the Main thread, `@Orch <question>\n\n(About the
buy BTC-USDT-SWAP proposal, version 1.)`, mentions include `orchestrator`, card
`{"type": "proposal_question", "proposal_id", "version"}`. 201 `{"ok": true, "message_id", "thread_id"}`.

## trading_core `POST /internal/proposals/{id}/execute`

Signed like every `/internal` call. Body `{"approved_by": "owner"}` (422 otherwise). Gate (404 /
409 `not_awaiting_owner` / `approval_expired`), then `evaluate(id, allowed_statuses=("awaiting_owner",))`
— this flips the row to `risk_checked` under its row lock, so a double approve gets 409. Then
`execute(new_rc, "owner")`. Tests inject `app.state.approval_execute`.

## Events (all on atlas.proposals unless noted; brief §3.2 envelope, correlation `cycle-<id>` or the proposal id)

Common payload: `{proposal_id, version, cycle_id, inst_id, side, status, status_reason, approval_expires_at}` plus:

| type | source | extra |
| --- | --- | --- |
| `proposal.approved` | `human:owner` | `ok, error, status, reason, order_id, cl_ord_id, state, risk_check_id, decided_by` |
| `proposal.rejected` | `human:owner` | `decided_by: owner, reason` |
| `proposal.rejected` | `service:trading_core.approvals` | `decided_by: system, on_approval_timeout: reject` (timeout) or `decided_by: owner, risk_check_id, result` (re-check reject) |
| `proposal.advise_only` | `service:trading_core.approvals` | re-check found autonomy 1 |
| `proposal.revised` | `human:owner` | `parent_id, author: owner, changes: [size|stop|targets], risk_check_id, result, decision` (payload ids are the **new** version) |
| `proposal.asked_team` | `human:owner` | `message_id` |
| `proposal.approval_reminder` | `service:trading_core.approvals` | `minutes_left` |

War Room (atlas.warroom, author `orchestrator`, `author_type: system`, source `system:orchestrator`,
kind `text`, Main): reminder card `{"type": "approval_reminder", "proposal_id", "approval_expires_at"}`,
timeout card `{"type": "approval_timeout", "proposal_id", "status": "rejected"}`.

## Not built here

- No UI (ATL-164/180). No Telegram buttons (Phase 3).
- `on_approval_timeout` other than `reject` is logged and treated as reject; Settings has no field for it yet.
- An edited version keeps the original expiry; the reminder fires on the original schedule (if it
  already fired on version 1 it fires again for version 2 — it is a new row).
