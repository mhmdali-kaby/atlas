# ATL-194 — Portfolio Manager post-mortems: the journal entry and the `post_mortem` card

Code: `services/agents/src/atlas_agents/pm/postmortem.py`, hook `pm_postmortem` in
`atlas_agents/agents/portfolio_manager.py`. Config: `agents.portfolio_manager.postmortem.*` in
`config/defaults.yaml` (the task said `agents.pm.postmortem`; every other PM key lives under
`agents.portfolio_manager`, so it went there).

## When it runs

Every 60 s (`every_seconds`) while the Portfolio Manager is enabled. It finds `positions_closed` rows of
the env that are **full closes of Atlas positions** (`proposal_id` set, `close_type` 2 close, 3
liquidation or 5 ADL; partial closes 1/4 are skipped) closed in the last `lookback_hours` (48), with no
post-mortem yet. Nothing runs in replay.

One close = one entry. The key is `(env, proposal_id, data->>'position_ref')`, backed by the partial
unique index `journal_entries_postmortem_unique` (migration `2026_11_02_100000_…`).
`position_ref` = `"<OKX posId>:<closed_at ISO UTC>"`, e.g. `"2001234:2099-07-01T11:30:00Z"`.

## journal_entries row

| column | value |
| --- | --- |
| `agent` | `portfolio_manager` |
| `inst_id`, `proposal_id` | the position's instrument and **opening** (entry) proposal |
| `title` | `"BTC-USDT-SWAP long: +1.69R +$3.71 (win)"` |
| `expected_md` | code text: entry/stop, take-profits, expected R + probability, invalidation |
| `actual_md` | code text: avg entry → exit, realised (gross, fees, funding) = R, MFE/MAE, held, closed by |
| `lessons_md` | the one rule-style lesson (one line), **null while `data.lesson_pending`** |
| `tags` (text[]) | exactly five: `setup:<..>`, `asset:<..>`, `regime:<..>`, `catalyst:<..>`, `outcome:win\|loss\|scratch` |

`data` (jsonb) — every number is a string (Decimal), computed by code, never by the model:

```json
{
  "env": "demo",
  "as_of": "2099-07-01T11:33:00Z",
  "inst_id": "BTC-USDT-SWAP",
  "side": "long",
  "asset_class": "crypto",
  "setup_tag": null,
  "proposal_id": "9b0c…",
  "position_ref": "2001234:2099-07-01T11:30:00Z",
  "outcome": "win",
  "expected": {
    "entry": "100", "entry_zone": null, "stop": "95",
    "take_profits": [{"price": "110", "pct": "100"}],
    "expected_r": "2", "probability": "0.55",
    "invalidation": "1H close below 96",
    "evidence": [{"type": "research_report", "id": "r1"}],
    "time_stop_minutes": null, "author": "strategist"
  },
  "actual": {
    "avg_entry": "100.5", "avg_exit": "110", "size": "4", "ct_val": "0.1",
    "gross_pnl_usd": "3.80", "fees_usd": "-0.04", "funding_usd": "-0.05",
    "realised_pnl_usd": "3.71", "okx_realised_pnl_usd": "3.71",
    "initial_risk_usd": "2.20", "realised_r": "1.69",
    "mfe_r": "1.91", "mae_r": "-0.45", "excursion_bar": "1m",
    "opened_at": "2099-07-01T07:00:00Z", "closed_at": "2099-07-01T11:30:00Z",
    "holding_minutes": 270, "close_reason": "take_profit", "close_type": "2", "fill_count": 3
  },
  "votes": [{"agent": "devils_advocate", "vote": "skip", "confidence": "0.7", "reason": "…"}],
  "deadline_at": "2099-07-01T11:45:00Z",
  "notes": [],
  "review": {"right_md": "…names agents…", "wrong_md": "…names agents…"},
  "lesson": "When the 1H trend and a strong level agree, let the take-profit work, do not trail early.",
  "lesson_pending": false,
  "lesson_attempts": 1,
  "tags": {"setup": "pullback_to_support", "asset": "crypto", "regime": "trend", "catalyst": "none", "outcome": "win"},
  "tag_list": ["asset:crypto", "catalyst:none", "outcome:win", "regime:trend", "setup:pullback_to_support"],
  "on_time": true,
  "numguard": {"unmatched_numbers": [], "checked": 4},
  "card_event_id": "…only when code posted the card (lesson pending)…"
}
```

Definitions, for the scorecard jobs:

- `realised_pnl_usd` = gross + fees + funding. Gross = Σ OKX `fillPnl` of the closing fills (or price move
  × size × ctVal when OKX gave none); fees = Σ fill fees with OKX's sign (paid is negative); funding = the
  positions-history funding fee. `okx_realised_pnl_usd` is OKX's own figure, for cross-checking.
- `initial_risk_usd` = |avg entry − proposal stop| × size × ctVal; `realised_r` = realised / initial risk
  (null without a stop).
- `mfe_r` ≥ 0 and `mae_r` ≤ 0, in R of the per-unit risk, over candles overlapping the hold; `excursion_bar`
  is the first of `candle_bars` (`1m`, then `1H`) that had candles; all three null when none.
- `close_reason`: `liquidation`, `adl`, `kill`, the PM rule that closed it (e.g. `time_stop`,
  `invalidation`, or `pm_close`), `stop_loss`, `take_profit`, `manual` (owner Close in Atlas), `external`.
- `outcome`: win if R ≥ `scratch_r` (0.1), loss if R ≤ −0.1, else scratch (PnL sign when R is null).
- Tags: outcome and asset are code's. `setup` is `positions_closed.setup_tag` when set, else the model's
  pick. `regime` and `catalyst` are the model's, from the vocabularies in config; `unknown` while pending.

## The War Room card

A `warroom.message` with `kind: "post_mortem"`, author `portfolio_manager`. It replies to the opening
proposal's `proposal` card (`reply_to` = that message id, in its thread) when that card is in
`warroom_messages`; otherwise it goes to the main thread with `reply_to: null`.

`card` = the `data` above **minus** `lesson_attempts`, `on_time`, `numguard`, `card_event_id`, **plus**:

```json
{ "type": "post_mortem", "journal_id": "<journal_entries.id>", "lesson_update": false,
  "meta": {"unmatched_numbers": [], "checked": 4} }
```

`meta` is the number guard on the model's prose (absent on a code-posted card). `content_md` is the
model's ≤120-word summary followed by `**Lesson:** …`; when code posts it, a one-line code summary
ending "Lesson pending.".

## When the model fails

Error, rate limit, disabled/over budget or an invalid answer after one follow-up: the row is still
written from the facts with `lesson_pending: true`, `lessons_md: null`, regime/catalyst `unknown`, and
code posts the card (`lesson_pending: true`). Each later tick retries the prose for that same row (up to
`lesson_attempts` = 3 model runs; rate-limited or over-budget runs do not count). On success the **same
row** is updated (lessons_md, tags, review, lesson, `lesson_pending: false`) and a second
`post_mortem` card is posted with `lesson_update: true`, replying to the first card when it is stored.
The UI should show the newest card for a `journal_id`, or fold the update into the first.

## Not built here

No API or screen (the Journal page and card are other tasks). The model does not call
`memory.write_journal` for post-mortems: code writes the row so every number is code's; the prompt
tells the model not to.
