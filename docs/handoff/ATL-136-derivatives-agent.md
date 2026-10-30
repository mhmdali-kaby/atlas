# ATL-136 — Derivatives & Sentiment Analyst (brief §7.5)

For ATL-138 (its evals), ATL-140 (War Room PositioningReport card) and ATL-158/ATL-159 (Strategist and
Devil's Advocate read the card).

## Where it is

| File | What |
| --- | --- |
| `services/shared/src/atlas_shared/positioning.py` | `PositioningReport` (pydantic, frozen, extra=forbid) |
| `services/agents/src/atlas_agents/tools/positioning.py` | `okx_market.get_positioning_features(inst_id)` + `build_positioning_features(ctx, inst_id)` |
| `services/agents/src/atlas_agents/derivatives/report.py` | `analyse()`, `ReportReviewer`, `enforce_report()` (code rules), `load_features()` |
| `services/agents/src/atlas_agents/agents/derivatives.py` | the four triggers, the debounce, `settled()`, `reset()` |
| `config/defaults.yaml` → `agents.derivatives_sentiment` | thresholds, slot times, debounce, regime windows |

Slug `derivatives_sentiment`, handle `@Derivatives`. **Disabled by default**
(`agents.roster.derivatives_sentiment: {}`); the owner enables it in Settings → Agents. Prompt:
`services/agents/prompts/derivatives_sentiment.md` (the brief's §7.5 role prompt, unchanged; versioned
by hash like every prompt).

## The tool

`okx_market.get_positioning_features({"inst_id": "BTC-USDT-SWAP"})` → `{as_of, source, data}` where
`data` is ATL-134's `DerivativesFeatures.model_dump(mode="json")` (shape in
`docs/handoff/ATL-134-derivatives-features.md`). `not_found` for an instrument not in `instruments`.
In replay mode the live Redis snapshot is never read (look-ahead), so `basis.reason` is `stale`.
It is in `okx_market`, so every "all read" agent gets it (Scout, Historian, TA, Derivatives, DA).

## The card (the `research_report` message's `card`)

```json
{
  "type": "positioning_report",
  "inst_id": "BTC-USDT-SWAP",
  "regime": "short_covering",
  "crowding_score": 0.7,
  "crowded_side": "short",
  "squeeze_risk": {"long_squeeze": 0.2, "short_squeeze": 0.6},
  "funding_view": "Funding is mildly positive (z 0.4 over 7d).",
  "basis_bps": "4.25",
  "underlying_premium_pct": null,
  "sentiment_score": 0.1,
  "supports_direction": {"long": "supports", "short": "opposes"},
  "key_evidence": ["OI -6.0% in 24h with price +3.0% (as_of 2026-10-30T10:00Z)"],
  "data_as_of": "2026-10-30T10:00:00Z",
  "basis_reason": null,
  "quadrants": {"1h": "flat", "4h": "short_covering", "24h": "short_covering"},
  "data_reliable": true,
  "unreliable_reasons": {},
  "notes": [],
  "meta": {"unmatched_numbers": [], "checked": 9,
           "corrections": [{"rule": "regime_from_features", "model": "new_longs", "code": "short_covering"}]}
}
```

`basis_bps` / `underlying_premium_pct` are Decimals → strings. Written by **code only** (whatever the
model sends is dropped or overwritten): `type`, `inst_id`, `data_as_of` (the features'), `regime`,
`quadrants`, `basis_bps`, `basis_reason`, `underlying_premium_pct` (always null: no underlying
reference price is stored yet), `data_reliable`, `unreliable_reasons`, `notes`, `meta`.

Code rules (`enforce_report`, applied to the raw card **before** validation; each override is in
`meta.corrections[*].rule`):

- `regime_from_features`: regime = quadrant of the first of `regime_windows_hours` [24, 4, 1] that has
  one, else `flat`.
- `basis_from_features`: the model sent a basis different from the features'.
- `no_underlying_reference`: the model sent an underlying premium.
- `squeeze_risk_clamped`: a squeeze value outside 0..1 (clamped, never rejected).
- `unreliable_data_neutral`: features unreliable → `supports_direction` = neutral/neutral,
  `data_reliable` false, `unreliable_reasons` = the features' codes → sentences, and `notes[0]` =
  `**Data unreliable for X:** <reasons>. Atlas does not read positioning from this data: neutral for
  both long and short.` The note is also prepended to `content_md`. The model validator refuses a
  non-neutral direction when `data_reliable` is false.

Invalid card (e.g. crowding_score 1.4, missing supports_direction key) → one retry with the errors;
a second invalid card ends the run `degraded` and nothing is posted.

Finding the latest card for X: `warroom_messages` with `author = 'derivatives_sentiment'`, `kind =
'research_report'`, `card->>'type' = 'positioning_report'`, `card->>'inst_id' = X`, newest
`created_at`.

## Triggers (all background; `await agents.derivatives.settled()` in tests, `reset()` between tests)

| name (debounce key) | when | instruments | run trigger / correlation_id |
| --- | --- | --- | --- |
| `watchlist` | `warroom.message.watchlist` card by `market_scout` | picks, best rank first, ≤ `watchlist_max_runs` | `alert` / envelope's, else `derivatives:watchlist:<id>` |
| `mention` | @Derivatives | instIds in the text, ≤ 3 | `mention` / the mention's; replies to it |
| `funding` | schedule hook `derivatives_funding`, checked every 60 s; fires at 00:05/08:05/16:05 UTC, skipped if > 30 min late | today's watchlist (owner-removed left out) then held | `schedule` / `derivatives:funding:2031-03-04T08:05Z` |
| `extreme` | schedule hook `derivatives_extremes`, every 300 s | watchlist + held whose features have \|OI 1h\| > 10 % or \|funding z 7d\| > 2.5 | `alert` / `derivatives:extreme:<inst>:<ts>` |

Debounce: once per (env, trigger, instrument) per `debounce_minutes` (60), in the supervisor process's
memory (a restart resets it, as News Intelligence's alert counter does). An **owner** @mention is
never debounced; an agent @mention is.

## Two-minute version

- The model never decides regime or basis: they come from the features and the card records any
  disagreement in `meta.corrections`.
- Unreliable data is never a direction: neutral both sides, said in the first line of the message.
- Tests patch `atlas_agents.derivatives.report.load_features` to hand the agent fixed features.

## Not built here

Evals (ATL-138), the card UI (ATL-140), Fear & Greed input (no store yet), the "predicted next funding
rate" (not stored, see ATL-134), and a sentiment-only view of `news.search_news` (the matrix grants the
tool; it returns full items with sentiment fields).
