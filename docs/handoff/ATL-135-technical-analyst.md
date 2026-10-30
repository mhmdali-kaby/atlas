# ATL-135 — Technical Analyst agent (brief §7.4)

For ATL-137 (TA evals), ATL-139 (War Room TechnicalSetup card), ATL-153 (submit_proposal checklist
item 2: "TechnicalSetup exists, is not none, < 60 min old").

## Where it is

| File | What |
| --- | --- |
| `services/shared/src/atlas_shared/technical.py` | `TechnicalSetup`, `SetupTarget`, `LevelRef` (pydantic, Decimal prices) |
| `services/agents/src/atlas_agents/tools/ta_table.py` | `okx_market.get_ta_table(inst_id)` + `build_ta_table(ctx, inst_id)` |
| `services/agents/src/atlas_agents/technical_analyst.py` | `analyse()`, `SetupReviewer`, `enforce_setup()` (the code rules) |
| `services/agents/src/atlas_agents/agents/technical_analyst.py` | the three trigger hooks + `settled()` |
| `config/defaults.yaml` → `agents.technical_analyst` | history bars, stale rule, min stop ATR mult, slippage limit, caps |

The agent is **disabled by default** (`agents.roster.technical_analyst: {}`); the owner enables it
in Settings → Agents. Its prompt is `services/agents/prompts/technical_analyst.md` (unchanged, the
§7.4 role prompt; `{{MIN_R}}` = settings `min_expected_r`, so the env needs a settings row).

## The card (the `research_report` message's `card`)

```json
{
  "type": "technical_setup",
  "inst_id": "BTC-USDT-SWAP",
  "timeframe_bias": {"1D": "up", "4H": "up", "1H": "range", "15m": "up"},
  "setup_type": "breakout",
  "direction": "long",
  "entry_zone": ["100.0", "100.2"],
  "trigger": "1H close above 100.2 with relative volume > 1.3",
  "stop": "96.7",
  "targets": [{"price": "110.5", "r_multiple": "3.00", "reason": "1H:z4", "close_pct": null}],
  "expected_r": "3.00",
  "invalidation": "1H close back below 99.0",
  "quality_score": 0.7,
  "est_slippage_bps": "3.12",
  "levels_used": [{"id": "1H:z2", "price": "99.1"}],
  "data_as_of": "2026-10-30T10:00:00Z",
  "no_setup_reason": null,
  "notes": [],
  "meta": {"unmatched_numbers": [], "checked": 14, "corrections": [{"rule": "tick_rounding", "tick_sz": "0.1"}]}
}
```

Decimals serialise as strings. `setup_type == "none"` ⇔ `direction == "none"`; then `entry_zone`,
`stop`, `expected_r` are null, `targets` is `[]` and `no_setup_reason` says why (the model's
"one condition that would create a setup", or a code rule's reason starting `Code rule:`).
`notes` and `meta` are written by code only. `meta.corrections[*].rule` is one of `tick_rounding`,
`stop_wrong_side`, `atr_unavailable`, `stop_too_tight`, `target_wrong_side`, `below_min_r`; the
removing rules carry `rejected_plan` (the model's original setup fields).

To find "the latest TechnicalSetup for X" (ATL-153): `warroom_messages` with `author =
'technical_analyst'`, `kind = 'research_report'`, `card->>'type' = 'technical_setup'`,
`card->>'inst_id' = X`, newest `created_at`. `data_as_of` is the table's (the newest bar close),
not the post time.

## Code rules after the model (`enforce_setup`)

1. Prices rounded to `tick_sz`: entry and targets to nearest; the stop **away from the entry**
   (long down, short up) so rounding never tightens it.
2. Stop beyond the entry zone; distance from the entry-zone midpoint ≥ `min_stop_atr_mult` (1.0) ×
   ATR(1H) from the table, else "none".
3. Targets beyond the entry; R recomputed per target; `expected_r` = close_pct-weighted R when every
   target has a close_pct, else the last target's R. Below the owner's `min_expected_r` → "none".
4. `est_slippage_bps` = the table's book walk for the setup's side at the Risk Engine's max size;
   > `max_slippage_bps` (10), or books5 cannot fill that size → a thin-liquidity note (setup kept).

Invalid card → one retry with the validation errors; a second invalid card ends the run
`degraded` (agent state `degraded`), and nothing is posted.

## Stale data

Before any model call: if the newest closed bar of a `stale_check_bars` timeframe (1H) is more than
`stale_bars` (2) bars late — `bars_late = floor((now − last bar close) / bar)` — or missing, no run
starts and a message is posted instead:

```json
{"kind": "system_alert", "author": "technical_analyst", "run_id": null,
 "content_md": "**Data alert: no technical analysis of X.** Candles are stale (1H: newest closed bar 3 bars late; limit 2 bars). …",
 "card": {"type": "data_alert", "inst_id": "X", "stale": {"1H": 3}, "limit_bars": 2, "data_as_of": "…"}}
```

## Triggers (all run in the background; `await agents.technical_analyst.settled()` in tests)

- **Watchlist**: the supervisor now offers every `warroom.message` as `warroom.message.<kind>` to
  `HOOKS.event` handlers. A `watchlist` card authored by `market_scout` (author_type agent) starts
  one run per pick, best rank first, max `watchlist_max_runs` (10). Trigger `alert`.
- **@Technical**: instIds in the text (regex, upper-cased, max `mention_max_instruments` = 3), one
  run each, replying to the mention. No instId → a short `text` reply asking for one, no run.
- **candle.closed**: the supervisor has a new `consume_market` (atlas.market XREAD, started by
  `serve`) that hands only registered types to `HOOKS.event`; ticks are dropped. A closed
  `position_bar` (1H) of an instrument with a non-zero position at the last account sync → one run.

## get_ta_table output (abridged)

`{inst_id, data_as_of, spec{tick_sz,lot_sz,min_sz,ct_val,asset_class}, timeframes{<bar>: ATL-132's
entry + zones[nearest zones_reported], patterns{5 flags}, volume{rel_volume,obv,session_vwap,
anchored_vwap}, bars_late}, levels{source_bar:"1H", prior{prior_day,prior_week},
volume_profile(no rows), round_numbers}, book{available, reason, max_notional_usd,
max_notional_basis, summary, slippage{buy,sell}: BookFill, depth_note}, stale{}, stale_limit_bars}`.
Max notional = min(equity, `bot_capital_cap_usdt`) × `max_position_pct` (decision #250). The tool
is in `okx_market`, so every "all read" agent gets it (Scout, Historian, TA, Derivatives, DA).

## Not built here

Evals (ATL-137), the War Room card UI (ATL-139), a per-instrument thread (cards go to Main or the
mention's thread), journal reading in the run prompt (the agent has `memory.read_journal`).
