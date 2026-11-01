# ATL-178: War Room daily_report and position_update cards

## Two-minute version

- `research_report` messages with `card.type == "daily_report"` (ATL-170, unchanged) render as
  `cards/DailyReportCard.tsx`. `position_update` messages render as `cards/PositionUpdateCard.tsx`
  **only when they carry a card**. A position_update with `card: null` (anything posted before this
  task) still falls back to the generic card.
- ATL-169 posted position_update with `card: None`, so this task adds the card in Python:
  `atlas_agents/pm/review.py::status_card(plan, now, actions)`. Every figure in it comes from code.
  `ActionReviewer` attaches it to the run's final message. Actions that code submits after the run
  ("submitted by code: the rule is mandatory") are still separate text lines and are not in the card.
- `HeldPosition.funding_paid_usd` (in `pm/inputs.py::funding_paid`) is Σ funding-fee bills (type 8,
  USD-like ccys) on the instrument since `opened_at`, with OKX's sign. It is None when there is no bill.
- Readers: `cards/pm.ts` (`readDailyReport`, `readPositionUpdate`, `ruleKey`/`ruleText`, `sgnClass`).
  Strings: `pm.*` in `warroom.json` (en/ar). CSS: `cards/pm.css` (ported from design v14 `analysts.css`).

## position_update card JSON

```json
{"type": "position_update", "inst_id": "XAU-USDT-SWAP", "side": "short", "size": "4000",
 "entry": "4118.4", "mark": "4076.9", "stop": "4161", "initial_stop": "4161",
 "targets": ["4075.8", "4033.2"], "tp1": "4075.8", "tp1_filled": false,
 "pnl_usd": "1660.00", "r": "0.97", "atr_1h": "11.8",
 "stop_distance_atr": "7.15", "tp1_distance_atr": "0.09",
 "opened_at": "2026-10-27T15:52:00Z", "minutes_in_trade": 1133, "funding_paid_usd": "-12.40",
 "opening_proposal_id": "…",
 "actions": [{"kind": "amend_stop", "rule": "breakeven", "stop": "4117.6", "close_pct": null,
              "proposal_id": "…", "status": "submitted"}],
 "advisories": [{"rule": "funding_cost", "reason": "…"}],
 "as_of": "2026-10-28T10:45:00Z"}
```

Distances are signed toward the level: a positive `stop_distance_atr` means the stop is still on the
safe side of mark. `tp1` is null once TP1 has filled. A null `proposal_id` with `status: "not_submitted"`
means the submit was refused.

## What the cards show, and what they leave out

- **Proposal link:** `/warroom?proposal=<id>` (`proposalHref`, ATL-180's focus path). The design's
  proposal status chip is not shown, because the card does not carry the proposal's live status.
- **Ladder:** compact, with no Now marker. This is a record of one check, and the design's `priceAt`
  label (mark at check time) is not built in `Ladder.tsx` yet, nor is `wasStop`.
- **Daily report limit bars:** daily loss and open positions (each has a cap in the card). Peak exposure
  is shown as USD and % of equity with no bar, because ATL-170's card carries no exposure cap. The
  design's "Trades of max per day" row is not there because the card does not carry it.
- **Breach sentence:** composed from `breaches[].limit/used/cap/at` (`pm.breach.*`). The card has no
  breach text from the model.
