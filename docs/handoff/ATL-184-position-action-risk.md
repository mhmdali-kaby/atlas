# ATL-184 — Risk Engine checks Portfolio Manager position actions

For ATL-169 (Portfolio Manager wiring) and ATL-185 (executing an approved action).

## Two-minute version

- `POST /internal/risk/evaluate/{proposal_id}` (signed, empty body, ATL-160) now accepts proposals
  with `kind` in `amend_stop`, `amend_targets`, `partial_close`, `close`. No 409
  `risk_engine_unsupported_kind`/"only entries" any more for these kinds.
- The status gate is unchanged: only `passed` or `risk_checked` rows are evaluated. **The PM sets its
  proposal to `passed` (no vote) before calling evaluate.** Anything else → 409 `risk_engine_error`.
- Like entries, evaluate writes one `risk_checks` row, sets the proposal to **`risk_checked`** and
  publishes one `risk.decision` on `atlas.risk`. It does **not** set `awaiting_owner`: the caller does
  that exactly as the cycle's `_risk` does (rejected → `rejected`; `decision == "advise_only"` →
  `advise_only`; else `awaiting_owner` + `approval_expires_at`, then `proposal.awaiting_owner`).
- Code: `services/trading_core/src/trading_core/risk/position_actions.py` (pure `run_position_checks`,
  loader `load_action_context`), branch in `risk/engine.py::evaluate`. Entries are untouched.

## The seven checks (in `risk_checks.checks`, same entry shape as the entry checks)

Each entry: `{"n", "check", "status", "passed", "code", "detail", "values"}`. `status` is `passed`,
`failed`, `not_applicable` (check does not apply to this kind, `passed: null`) or `not_run` (an
earlier check failed). The first failure rejects.

| n | check | runs for | failure codes |
| --- | --- | --- | --- |
| 1 | `env_state` | all | `connection_unverified`; `trading_paused` / `circuit_breaker_active` (**amend_targets only**) |
| 2 | `position_open` | all | `positions_stale`, `no_open_position`, `position_ambiguous` |
| 3 | `stop_tightens` | amend_stop | `stop_missing`, `stop_unchanged`, `stop_widens` |
| 4 | `stop_vs_mark` | amend_stop | `mark_unknown`, `stop_beyond_mark` |
| 5 | `targets_vs_mark` | amend_targets | `targets_missing`, `mark_unknown`, `target_beyond_mark` |
| 6 | `close_size` | partial_close, close | `close_pct_missing`, `close_pct_out_of_range`, `close_below_min_size` |
| 7 | `autonomy` | all | never fails: level 1 → `advise_only`, level 2 (and 3–4 while not enabled) → `awaiting_owner` |

Rules behind them:

- **Paused / breaker tripped:** amend_stop, partial_close and close pass (`values.note =
  "risk_reducing_allowed_while_halted"`); amend_targets is refused. A widening stop is refused by
  check 3 whatever the state. Configurable: `risk_rules.position_actions.paused_allowed_kinds`.
- **Open position:** newest `positions_snapshots` row set for the env (must be ≤
  `positions_max_age_seconds`, 300) with `pos != 0` on the instrument. `proposals.side` is optional:
  `buy` = the long, `sell` = the short. Only needed when the account is in long/short mode and holds
  both; two positions and no side → `position_ambiguous`.
- **Live stop:** `algo_orders` rows on the instrument with `kind in (sl, oco)` and `state in (live,
  partially_effective)`; price = `trigger_px` (sl) or `sl_trigger_px` (oco). Matched to the position
  by pos_side (`net`/null for a net position) and, when the row has a side, the closing side (sell for
  a long). Several → the tightest one is the bar (highest for a long, lowest for a short). None → the
  check passes with `values.note = "no_live_stop"`, `current_stop: null` (a new stop adds protection;
  check 4 still applies).
- **Mark:** the market-data hash (`markPx`, else ticker `last`) when its tickers time is within the
  owner's `market_data_max_age_seconds`, else the position snapshot's `mark_px` if that snapshot is
  within 300 s. `values.mark_source` = `market_data` | `positions_snapshot`.
- **close_size:** close = 100 %. partial_close needs `0 < close_pct <= 100` (the DB CHECK already
  keeps it in (0, 100)). Contracts = |pos| × pct / 100, rounded down to lotSz (a 100 % close is not
  rounded), must be ≥ minSz.

## Response body (200), position action

Same keys as for entries (`decision_body`):

```json
{"ok": true, "proposal_id": "7f…", "risk_check_id": "c1…", "env": "demo", "settings_version": 9,
 "result": "approved", "decision": "awaiting_owner",
 "reason": "Approved close on XAU-USDT-SWAP: close 10 contracts.",
 "failed_check": null, "binding_cap": null,
 "final_contracts": "10", "final_notional_usd": "400", "leverage": null,
 "checks": [
   {"n": 1, "check": "env_state", "status": "passed", "passed": true, "code": null,
    "detail": "Trading is halted, but a close reduces risk.",
    "values": {"paused": true, "active_breakers": [], "connection_verified": true,
               "note": "risk_reducing_allowed_while_halted"}},
   {"n": 2, "check": "position_open", "status": "passed", "passed": true, "code": null,
    "detail": "Open long position on XAU-USDT-SWAP.",
    "values": {"positions_taken_at": "2031-03-14T14:59:30+00:00", "max_age_seconds": 300,
               "pos_side": "net", "direction": "long", "contracts": "10", "avg_px": "3950"}},
   {"n": 3, "check": "stop_tightens", "status": "not_applicable", "passed": null, "code": null,
    "detail": "Not applicable to close.", "values": {}},
   {"n": 4, "check": "stop_vs_mark", "status": "not_applicable", "…": "…"},
   {"n": 5, "check": "targets_vs_mark", "status": "not_applicable", "…": "…"},
   {"n": 6, "check": "close_size", "status": "passed", "passed": true, "code": null,
    "detail": "Close 10 of 10 contracts (100%).",
    "values": {"close_pct": "100", "position_contracts": "10", "contracts": "10", "notional_usd": "400"}},
   {"n": 7, "check": "autonomy", "status": "passed", "passed": true, "code": null,
    "detail": "The owner approves each trade.", "values": {"autonomy_level": 2, "max_enabled_level": 2}}
 ]}
```

- `result` is `approved` or `rejected` (never `resized`). `leverage` is always null.
- `final_contracts` / `final_notional_usd`: the contracts to close and their USD value for
  partial_close/close; null for amend_stop/amend_targets and for every rejection.
- An approved amend_stop's check 3 `values`: `new_stop`, `current_stop` (null with `note:
  "no_live_stop"`), `live_stops: [{algo_id, algo_cl_ord_id, kind, price}]` — the rows ATL-185 amends.
- Rejected example: `"result": "rejected", "decision": null, "failed_check": "stop_tightens",
  "reason": "The new stop 3850 is wider than the live stop 3900; a stop may only tighten."`, check 3
  `code: "stop_widens"`, checks 4–7 `not_run` (after a failure every later check is `not_run`,
  whether or not it applies to the kind).

## Config (`config/defaults.yaml` → `risk_rules.position_actions`)

`positions_max_age_seconds: 300`, `protecting_states: [live, partially_effective]`,
`stop_kinds: [sl, oco]`, `paused_allowed_kinds: [amend_stop, partial_close, close]`.

## Not built here

- Executing an approved action (ATL-185). The Executor's `execution/plan.py` still refuses non-entry
  kinds; `/internal/proposals/{id}/execute` will 409 for them until ATL-185. ATL-185 should re-run
  evaluate first (as approve does for entries) because the live stop or position may have moved.
- Setting `awaiting_owner` / `approval_expires_at` and publishing `proposal.awaiting_owner` for PM
  proposals — the caller's job (ATL-169), same as the cycle does for entries.
- Tick rounding of the new stop/targets (the PM rules already round; the Executor should too).
