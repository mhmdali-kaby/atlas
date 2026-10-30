# ATL-158 — Strategist (brief §7.6)

For ATL-160 (the Orchestrator cycle calls it), ATL-166 (its evals), ATL-159 (the Devil's Advocate reads
its proposal card) and ATL-169 (the Portfolio Manager's formalise request).

## Two-minute version

- Slug `strategist`, handle `@Strategist`, **disabled by default** (`agents.roster.strategist: {model:
  strong}`); the owner enables it in Settings → Agents.
- The model never computes a size, a leverage or a final probability. Code computes them before the
  run (they are in the inputs the model reads) and **sets them on the proposal at submission**, in
  `warroom.submit_proposal`'s own path, so the rule holds even if the model calls the tool itself.
- In a planning run the model does not call `submit_proposal`: it ends with a decision block and
  code submits it **once** (one retry if the checklist refuses it). Revise/withdraw stay tool calls.
- No usable TechnicalSetup → **no model run**: code posts "No trade on X" naming the missing input.

## Files

| File | What |
| --- | --- |
| `services/agents/src/atlas_agents/strategist/inputs.py` | `gather(session, ctx, inst_id) -> StrategistInputs`, `watchlist_items(session, ctx)`, `atr_percentile(...)` |
| `services/agents/src/atlas_agents/strategist/sizing.py` | pure: `size_suggestion`, `lowest_leverage`, `analog_hit_rate`, `clamp_probability`, `default_time_stop_minutes`, `split_take_profits` |
| `services/agents/src/atlas_agents/strategist/rules.py` | `apply(session, ctx, raw)` / `apply_with(raw, inputs)` → (corrected proposal dict, card meta) |
| `services/agents/src/atlas_agents/strategist/run.py` | `plan(host, inst_id, ...)`, `PlanReviewer`, `setup_problem`, `cycle_correlation_id` |
| `services/agents/src/atlas_agents/agents/strategist.py` | `run_cycle(host, cycle_id)`, the @Strategist mention handler, `settled()` |
| `services/agents/src/atlas_agents/tools/warroom.py` | `_submit`/`_revise` call the rules for Strategist entries; `submit_entry(ctx, args)`; `ProposalOut.meta` |
| `config/defaults.yaml` → `agents.strategist.{sizing, probability, take_profit_split, time_stop, ...}` | every threshold |

## Entry points

**Orchestrator (ATL-160):** `await atlas_agents.agents.strategist.run_cycle(host, cycle_id: str, *,
limit=None) -> list[RunResult]`. Awaited (not background): it returns when every plan is done. One
`plan` per current watchlist item (today's Baghdad watchlist, owner removals left out, best rank first,
at most `cycle_max_instruments` = 10), each with `trigger="schedule"` and correlation id
`cycle-<cycle_id>` — that is what gives the saved proposal its `cycle_id` (the cycle row must exist in
`cycles` for the env). A code "no trade" returns no RunResult, but its message is posted with the same
correlation id. Nothing listens on a bus event for this; call the function.

**@Strategist:** instIds in the text (≤ `mention_max_instruments` = 3), background runs, replies to the
mention; no instId → "Name the instrument to plan…". An owner mention says "The owner asks"; a
`portfolio_manager` mention is the formalise request: the Strategist submits **entries only**
(ATL-153 keeps amend/close kinds PM-only), so the prompt tells it to answer `no_trade` and say so if
the PM asks for an amend/close.

## What the model receives (`StrategistInputs.to_json()`)

```json
{"inst_id": "BTC-USDT-SWAP", "as_of": "2097-05-06T09:00:00+00:00", "asset_class": "crypto",
 "watchlist_pick": {"rank": 1, "asset_class": "crypto", "bias": "long", "conviction": "0.6", "thesis": "…",
                    "catalysts": [], "key_levels": {}, "invalidation": "…", "pinned_by_owner": false},
 "inputs": {
   "technical_setup": {"message_id": "<warroom_messages.id>", "posted_at": "…", "age_minutes": 10, "card": {…}},
   "positioning_report": null, "news_brief": null,
   "history_verdict": {"message_id": "…", "posted_at": "…", "age_minutes": 5, "card": {…}}},
 "missing": ["positioning_report", "news_brief"],
 "portfolio": {"equity_usd": "…", "sizing_equity_usd": "…", "bot_capital_cap_usdt": null, "available_usd": "…",
               "settle_ccy": "USDT", "account_synced_at": "…", "open_positions": [{"inst_id", "direction", "pos", "avg_px", "upl"}],
               "proposals_today_on_instrument": [{"proposal_id", "side", "status", "version", "kind"}]},
 "settings": {"version": 3, "values": {"max_position_pct": "5", …}},
 "sizing": {"base_pct": "5", "conviction": "0.6", "conviction_multiplier": "0.75", "atr_percentile": "70",
            "volatility_multiplier": "0.75", "size_suggestion_pct_equity": "2.8125"},
 "leverage_suggestion": 1,
 "analog_hit_rate": {"buy": {"hit_rate": "0.5", "horizon": "24h"}, "sell": {"hit_rate": "0.5", "horizon": "24h"}},
 "default_time_stop_minutes": 1440, "default_take_profit_split_pct": ["50", "30", "20"], "notes": []}
```

Where each input comes from (newest `research_report` card, bounded by `ctx.bound` in replay):
technical_setup = author `technical_analyst`, `card.type = technical_setup`, `card.inst_id`;
positioning_report = author `agents.strategist.positioning_author` (`derivatives_sentiment`), type
`positioning_report` (ATL-136's card; read as raw JSON, no schema import, so it works before ATL-136
merges); news_brief = author `news_intelligence`, type `news_brief`, `card.inst_ids ? inst`;
history_verdict = author `historian`, type `history_verdict`.

## The numbers

- **Size** = base_pct × conviction mult × volatility mult. base_pct = settings `max_position_pct`,
  else the row's risk profile default; no settings row → no size (the checklist then fails on R anyway).
  Conviction = the watchlist pick's (<0.55 → 0.5, 0.55–0.7 → 0.75, >0.7 → 1.0; bounds inclusive in the
  middle band; unknown → 0.5). ATR% percentile = percentile of the newest ATR(14)% on 1H among the last
  180 values (<60 → 1.0, 60–85 → 0.75, >85 → 0.5; too few candles → 0.5). Golden: 5 × 0.75 × 0.75 = 2.8125.
- **Leverage** = ceil(notional / (available × 0.9)), ≥ 1, ≤ min(settings `max_leverage` (or profile),
  instrument `maxLever`). notional = sizing equity × size% (sizing equity = min(equity,
  `bot_capital_cap_usdt`), decision #250). available = `avail_bal` of the settle ccy at the last sync,
  converted with eq_usd/eq. Unknown equity/balance → 1.
- **Probability** clamp: hit rate = newest history_verdict's `distribution[h].pct_positive / 100` at the
  first of `analog_horizons` [24h, 4h, 72h, 1h] it has (100 − it for a sell). p is clamped to hit rate
  ± 15 points (within 0..1). No verdict or no distribution → no clamp, `meta.analog_hit_rate: null`.
  This is a proxy: the Historian reports % positive at a horizon, not "TP1 before stop".
- **Defaults** when the model leaves them out: `time_stop_minutes` = 1440 for crypto/commodity, and for
  `stock|etf|index` the minutes to the close of the 2nd US regular session (09:30–16:00
  America/New_York, Mon–Fri, DST from zoneinfo, a session in progress counts as the first; exchange
  holidays are not known). Take-profit pct when **every** target has none: 50/30/20 (2 targets 50/50,
  1 target 100).

## The proposal card (ATL-153's, plus `meta` for Strategist entries)

```json
"meta": {
  "corrections": [
    {"rule": "size_from_guidance", "model": "9", "code": "2.8125"},
    {"rule": "leverage_lowest_that_fits", "model": 7, "code": 1},
    {"rule": "take_profit_split_default", "model": null, "code": ["50", "30", "20"]},
    {"rule": "time_stop_default", "model": null, "code": 1440},
    {"rule": "probability_clamped", "model": "0.8", "code": "0.65", "hit_rate": "0.5", "horizon": "24h"}],
  "sizing": {…as in the inputs…}, "leverage_suggestion": 1,
  "analog_hit_rate": {"hit_rate": "0.5", "horizon": "24h", "max_adjust_points": "15"},
  "inputs_missing": ["positioning_report", "news_brief"]}
```

The same object is `ProposalOut.meta` in the tool result. PM rows have no `meta` key. A revision gets
the rules and its own `meta` too.

## Messages a run posts

- Code no-trade (no/none/stale setup): `warroom.message`, kind `text`, author `strategist`, `card: null`,
  `run_id: null`, `content_md` = `**No trade on X.**\n- no TechnicalSetup from the Technical Analyst for X
  (missing input: technical_setup)`. Correlation id = the cycle's / mention's.
- Model `no_trade`: the run's final message, kind `text`, `**No trade on X.**` + the reasons + the
  synthesis.
- Model `trade`: first the `proposal` card + `proposal.opened` (ATL-153 shapes), then the run's final
  `text` message = synthesis + "Proposal **v1** submitted for X (<id>)." — the card arrives **before**
  the synthesis text.
- Checklist refusal: one follow-up with the error; refused again → the final text is "No trade on X" +
  "the proposal was refused: checklist_failed: …". Not degraded. An unreadable block twice → `degraded`.

## Not built here

Evals (ATL-166); waiting for missing research (the cycle's timeout is ATL-160's — the Strategist plans
with whatever exists, except the TechnicalSetup); answering the Devil's Advocate (it can revise via the
tool); correlated-exposure trimming in code (the model sees positions; the Risk Engine enforces the cap).
