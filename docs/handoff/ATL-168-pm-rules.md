# ATL-168 — Portfolio Manager management rules (brief §7.8)

Module: `services/agents/src/atlas_agents/pm/rules.py` (in **atlas_agents**, not trading_core). It is
pure: no DB, no clock, no network, no order state of its own. The caller loads the inputs from the
tables ATL-142 keeps current and passes them in. Tests: `agents/tests/test_pm_rules.py`.

## Call

```python
from atlas_agents.pm.rules import (
    PositionState, ProposalPlan, StructureReport, EventInfo, FundingInfo, RuleSettings,
    evaluate, evaluate_full,
)

settings = RuleSettings.from_config({"fee_rate": "0.0005"})   # overrides optional, same keys as yaml
actions = evaluate(position, plan, atr_1h, last_swing, events, funding, now, settings,
                   structure=structure)                       # -> list[RuleAction], [] when quiet
ev = evaluate_full(...same args...)   # -> Evaluation(actions, dropped, r_multiple)
```

`structure` is an extra keyword (not in the task's signature) because rule 5 needs it.

## Inputs (all Decimal for prices/rates, aware datetimes)

- `PositionState(inst_id, side "long"|"short", entry_price, mark_price, stop, opened_at,
  tick_sz=None, tp1_filled=False, tp1_resting=False, tp2_filled=False, applied_rules=frozenset())`
  - `stop`: the stop resting on OKX now — from `algo_orders` (sl / oco) for the position.
  - `tp1_filled` / `tp2_filled`: the TP portion has already been taken on OKX.
  - `tp1_resting`: a TP1 algo rests on OKX, so the rules do **not** emit a partial_close at TP1
    (OKX takes it); they still trail the stop.
  - `applied_rules`: one-shot keys already acted on. Today only `event_reduce:<event id>` — put the
    action's `data["applied_key"]` in it once the partial close executed, or the next 5-min check
    will propose it again.
- `ProposalPlan(stop, take_profits=(TakeProfit…), time_stop_minutes=None, proposal_id=None)`;
  `ProposalPlan.from_trade_proposal(TradeProposal, id)`. `stop` is the **original** stop: 1R =
  |entry − original stop|. `None` for an external position: rules 1–4 then do not run.
- `atr_1h`: ATR(14) on 1H (None → trailing rules dropped with `no_atr`).
- `last_swing`: last 1H swing on the protective side (swing low for a long, high for a short).
  Ignored if it is on the wrong side of the mark.
- `events`: `EventInfo(id, name, scheduled_at, importance)` already filtered to events that
  affect the instrument (economic_events / earnings_events). The module filters importance and window.
- `funding`: `FundingInfo(rate, interval_hours=8)`; rate per settlement, positive = longs pay.
- `structure`: `StructureReport(status "intact"|"weakening"|"broken", as_of, source_id)`.
  **Gap:** the TechnicalSetup card (ATL-135) has no structure-status field yet. The brief asks the
  TA to say intact/weakening/broken on each 1H close of a held instrument; whoever wires the PM
  (ATL-169) needs that from the TA card (or must derive it) — I did not invent one.

## Output

`RuleAction(kind, rule, inst_id, reason, stop=None, close_pct=None, data={})`

| rule | kind | fields |
| --- | --- | --- |
| `breakeven` | amend_stop | stop = entry ± entry × fee_rate × 2 |
| `tp1_partial` | partial_close | close_pct = TP1's pct (fallback tp1_close_pct) |
| `tp1_trail` | amend_stop | mark ∓ max(1.5 × ATR, distance to last swing) |
| `two_r_trail` | amend_stop | mark ∓ 1.0 × ATR (R ≥ 2, TP2 not filled) |
| `time_stop` | close | proposal time_stop_minutes, else default; not after TP1 |
| `invalidation` | close | structure "broken", report ≤ 120 min old |
| `event_reduce` | partial_close (close when event_close_pct = 100) | close_pct 50; data.applied_key |
| `funding_cost` | flag + suggest_close | advisory, not proposals |

`action.to_position_action(evidence=())` builds the ATL-153 `PositionAction` for amend_stop /
partial_close / close (raises for flag / suggest_close). The proposal kind is `action.kind`.

Consolidation: at most one amend_stop (the tightest), one partial_close (the largest pct); a close
supersedes both. Actions come back in rule order.

`Evaluation.dropped`: `DroppedAction(rule, reason, stop)` with reasons `widens_stop`,
`crosses_mark`, `superseded_by:<rule>`, `no_atr`, `structure_report_stale`. A stop equal to the
current one is a silent no-op.

## Guarantees

Every amend_stop is rounded toward the market to tick_sz, is strictly tighter than `position.stop`
and stays on the safe side of the mark (hypothesis property, 600 examples). Decimal throughout.

## Config

`config/defaults.yaml` → `agents.portfolio_manager.rules` (documented there). `fee_rate` is a
fallback — pass the instrument's taker fee from trade-fee as an override when you have it.
`close_before_high_impact_events` is meant to be overridden from the settings table; no settings
key/UI for it exists yet.
