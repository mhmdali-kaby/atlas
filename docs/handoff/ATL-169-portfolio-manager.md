# ATL-169: the Portfolio Manager's position checks (brief §7.8)

This handoff is for ATL-170 (the daily report), ATL-171 (PM evals), ATL-178 (position_update / limit
cards) and ATL-185 (executing approved actions).

## Two-minute version

- The agent is `portfolio_manager`. It is **off by default** and runs on the **strong** model when it
  runs at all. Hooks are in `atlas_agents/agents/portfolio_manager.py`, the work is in `atlas_agents/pm/`:
  `inputs.py` (DB → rule inputs), `manage.py` (tick, triggers, reviewer), `guard.py` (what may be
  submitted), `route.py` (vote skipped → risk → autonomy) and `limits.py` (70% warnings).
- **The cheap path:** `manage.tick(host)` runs every `agents.portfolio_manager.check.every_seconds`
  (300). For a position where no rule fires there is no model call and no message.
- **A rule fires** (amend_stop / partial_close / close) → `manage.manage()`: one run with the computed
  actions in the prompt. The model ends with
  `{"actions": [{"kind","rule","stop","close_pct","rationale_md"}], "status_md"}`. `ActionReviewer`
  checks each action with `guard.match` and submits it through `warroom.submit_entry` (the same path
  as the tool). A refused block gets one follow-up. Any computed action still not submitted after the
  run is then submitted by code, with "(submitted by code: the rule is mandatory)".
- **Guard** (`pm/guard.py`): `guard.expecting(env, correlation_id, Expected(...))` registers the computed
  actions for that run. Every PM `submit_proposal` (model tool call or reviewer) is refused with
  `widens_stop` when the stop is not strictly tighter than the live OKX stop or not on the safe side of
  mark, and with `not_computed_action` for anything else, including any PM submission outside a run
  that has expected actions. A second identical submission returns `already_submitted`.
- **Routing** (`pm/route.py`): `under_review → passed` (no vote), then one
  `POST /internal/risk/evaluate/{id}`. After that it follows the cycle's `_risk` rules: `rejected` /
  `advise_only` (autonomy 1) / `awaiting_owner` + `approval_expires_at`. On `TradingCoreError` the
  proposal becomes `rejected` with `status_reason = "risk_<code>"`, for example
  `risk_risk_engine_error` until ATL-184 merges. A War Room line names the code.

## What a proposal looks like

`proposals` row: `author portfolio_manager`, `kind` amend_stop|partial_close|close, `stop` /
`close_pct`. `invalidation` holds the **rule label**: the rule name (`breakeven`, `tp1_partial`,
`tp1_trail`, `two_r_trail`, `time_stop`, `invalidation`) or, for the event rule,
`event_reduce:<event id>`. `evidence` = `[{"type": "proposal", "id": "<opening entry proposal id>"}]`.
The War Room proposal card and the `proposal.opened` event are the ATL-153 ones. `cycle_id` is null.

`proposal.awaiting_owner` / `proposal.advise_only` on atlas.proposals use the cycle's payload, plus
`kind`, with `cycle_id: null` and `side: null`:

```json
{"proposal_id": "…", "version": 1, "cycle_id": null, "kind": "amend_stop", "inst_id": "BTC-USDT-SWAP",
 "side": null, "risk_check_id": "…", "result": "approved", "decision": "awaiting_owner",
 "final_contracts": "3", "final_notional_usd": "…", "leverage": null,
 "approval_expires_at": "2098-03-04T09:15:00Z"}
```

## How state is derived (read this, ATL-185)

- **Current stop**: the tightest live (`live|partially_effective`) `algo_orders` row of kind `sl` (its
  `trigger_px`) or `oco` (its `sl_trigger_px`) on the instrument.
- **tp1_resting**: a live `oco` with a `tp_trigger_px`, or a live `tp`.
- **tp1_filled**: a PM `partial_close` whose `invalidation = "tp1_partial"` is in
  `sent|partially_filled|filled|closed`. **ATL-185: move the proposal to one of those statuses when
  the close executes. That is all it takes.**
- **applied_rules** (the event rule's one-shot key): `invalidation` of every PM proposal since the
  position opened that is not `rejected|cancelled|expired|advise_only`.
- **Pending**: a PM proposal of the same kind in
  `draft|under_review|voting|passed|risk_checked|awaiting_owner|sent|partially_filled` means that kind
  is not proposed again. A trailing stop waits until the earlier amend is decided.
- **Plan**: the proposal of the newest filled Atlas `entry` order on the instrument in the position's
  direction. There is none for an external position, and then rules 1-4 do not run.
- **opened_at**: `positions_snapshots.c_time`, else the entry order's `created_at`.
- **ATR / swing**: Wilder ATR(14) on 1H, and the last confirmed ZigZag swing at 1.5 × ATR.
- **Events**: every economic event in the next 24 h (they are not mapped to instruments), plus
  earnings of a stock or ETF underlying at importance 4.
- **structure is always None**: no card carries intact/weakening/broken yet (ATL-168's gap), so the
  invalidation rule cannot fire.

## Triggers

| Trigger | Source | Runs the model? |
| --- | --- | --- |
| `pm_check` every 300 s | schedule hook | only when a proposal rule fires; advisory-only (funding) at most once per 60 min per position; once per (position, event) 30 min before importance ≥ 4 |
| `news.alert` naming a held instrument (`inst_ids` or `held_inst_ids`) | atlas.warroom | yes, for each held one |
| `warroom.message.fill` on a held instrument | Executor fill card | yes. A stop or TP that triggers on OKX arrives here as a fill |
| `warroom.message.system_alert` with `author circuit_breaker`, `card.type circuit_breaker` | breakers | yes, for every held position |

Immediate runs use `trigger = "alert"` in agent_runs. Correlation ids: `pm:<inst>:rule:<ts>`,
`pm:<inst>:event:<event id>`, `pm:<inst>:advisory:<ts>`, `pm:<inst>:news|fill|breaker:<ts>`.

## Messages

- The run's final message is `kind: "position_update"` with no card. Its content is `status_md`, the
  model's text and one line per submitted or refused action ("Submitted **amend_stop** (breakeven)
  on X: proposal <id>.", "Refused by code: widens_stop: …").
- A limit warning is `kind: "text"`, author `portfolio_manager`, with this card:

```json
{"type": "limit_warning", "limit": "max_daily_loss_pct", "used": "2.88", "cap": "4", "usage_pct": "72.0"}
```

  `limit` is one of `max_daily_loss_pct`, `max_weekly_loss_pct` or `max_open_positions`. `used` is a
  loss % of the period's start equity, or a count of open positions. Content: "**Limit warning:** the
  daily loss limit is at 72% (loss 2.88% of equity against a 4% limit)." It is sent at most once per
  60 min per limit. The dedupe is in-process, so a restart can warn once more.

## Config (`agents.portfolio_manager.check`)

`every_seconds` 300, `atr_window` 14, `atr_lookback_bars` 120, `swing_atr_mult` 1.5,
`event_lookahead_minutes` 1440, `earnings_importance` 4, `earnings_hours_utc`, `pre_event_minutes` 30,
`pre_event_min_importance` 4, `status_post_interval_minutes` 60, `limit_warning_pct` 70,
`limit_warning_interval_minutes` 60, `output_retries` 1. The roster has `portfolio_manager: { model: strong }`.

## Not built

- The daily report and post-mortems (ATL-170). Executing an approved action, and with it tp1_filled
  (ATL-185). The position-action risk checks (ATL-184).
- Correlated-exposure and total-open-risk warnings. Only daily loss, weekly loss and open positions are
  covered.
- `amend_targets` is never computed: no ATL-168 rule produces it.
- Tests: `agents/tests/test_portfolio_manager.py`. FakeRisk shows what trading_core does to the row.
