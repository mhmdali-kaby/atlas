# ATL-128 — Manual risk check: `POST /api/risk/check` → `POST /internal/risk/check`

## Two-minute version

- The owner (or a test) posts a hand-written TradeProposal; trading_core stores it as a `proposals` row
  (`author = owner:manual`, `kind = entry`, `status = draft`, `cycle_id = null`, env = the env trading_core
  runs) and calls ATL-127's `evaluate(..., allowed_statuses=("draft",))`. `evaluate` marks it `risk_checked` (then it is closed, below), one
  `risk_checks` row is written and one `risk.decision` is published on `atlas.risk`, exactly as for an
  agent proposal. Nothing goes to OKX (a respx test blocks every host and counts zero calls).
- `evaluate` gained one keyword, `allowed_statuses`, overriding `risk_rules.evaluable_statuses` for that
  call only. Nothing else uses it.
- Right after `evaluate`, the manual proposal is closed as `expired` (`risk.api.FINAL_STATUS`). A manual
  check is a what-if: it is not in the Strategist's `pending_statuses`, so it never trips no_duplicate,
  and not in `EXECUTABLE_PROPOSAL_STATUSES`, so the Executor refuses it. The `risk_checks` row and the
  `risk.decision` event (which still says `decision = awaiting_owner` etc.) stay as the record.

## Laravel `POST /api/risk/check` (Sanctum, throttle `risk-check` = `risk_api.checks_per_minute`, 20)

Request (prices/percentages as decimal strings or JSON numbers):

```json
{"inst_id": "XAU-USDT-SWAP", "side": "buy", "order_type": "limit", "entry": "4000", "stop": "3960",
 "take_profits": [{"price": "4100", "pct": "100"}], "size_suggestion_pct_equity": "5",
 "leverage_suggestion": 3, "expected_r_multiple": "2.5", "probability_estimate": "0.5",
 "rationale_md": "Hand-written check.", "invalidation": "1H close below 3960.",
 "entry_zone": null, "time_stop_minutes": null, "evidence": [{"type": "news", "id": "…"}]}
```

`entry_zone`, `time_stop_minutes`, `evidence` are optional; nulls are dropped before relaying.
Form Request (`RiskCheckRequest`) 422s a missing/zero stop, bad side/order_type, 0 or >3 take-profits,
probability outside 0..1, bad inst_id. trading_core re-checks the cross-field rules (stop on the right
side, a limit/post_only needs entry or entry_zone, take-profit pct ≤ 100 in total).

200 — trading_core's body unchanged:

```json
{"ok": true, "proposal_id": "…", "risk_check_id": "…", "env": "demo", "settings_version": 7,
 "result": "resized", "decision": "awaiting_owner",
 "reason": "Resized by max_usdt_per_trade: 250 contracts, 1000.000 USD.", "failed_check": null,
 "binding_cap": "max_usdt_per_trade", "final_contracts": "250", "final_notional_usd": "1000",
 "leverage": 3, "checks": [ …9 entries, shape in docs/handoff/ATL-127-risk-engine.md… ]}
```

`final_contracts`/`final_notional_usd` are plain decimal strings (trailing zeros dropped); they,
`leverage` and `binding_cap` are null when `result = rejected`. A rejected check is still a 200.

Errors: `{"ok": false, "error", "message"}` plus
- 422 `invalid_proposal` + `errors` (trading_core's pydantic list `[{loc, msg, type}]`);
- 409 `risk_engine_unavailable` + `detail` (e.g. no settings row; the draft row is deleted again);
- 502 `trading_core_error` / `trading_core_unavailable`.
Form Request 422s use Laravel's normal `{message, errors: {field: [...]}}`.

Audit: `action = risk.manual_check`, `subject_type = proposals`, `subject_id` = proposal id (null on
error), `after = {inst_id, side, ok, error, risk_check_id, result, decision}`. A Form Request 422 is not
audited (nothing was relayed).

## trading_core `POST /internal/risk/check` (`trading_core/risk/api.py`)

Same request body as JSON (`TradeProposal.model_validate_json`, Decimal-exact; unknown fields refused).
401 unsigned/replayed (middleware). 422 `{"ok": false, "error": "invalid", "errors": [...]}`, 409
`{"ok": false, "error": "risk_engine_error", "detail"}`, 200 as above. Tests inject
`app.state.risk_redis` and `app.state.clock`.
