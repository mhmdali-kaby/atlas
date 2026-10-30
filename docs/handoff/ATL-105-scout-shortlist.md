# ATL-105 — Market Scout pre-score, shortlist and correlation flag

For ATL-108 (the 08:00 pipeline) and ATL-109 (intraday re-run). Builds on
`docs/handoff/ATL-104-scout-features.md`.

## Entry points

```python
from atlas_agents.scout.prescore import build_shortlist, shortlist_correlations

table = build_feature_table(ctx, universe)                 # ATL-104
shortlist = build_shortlist(table, pinned=["SOL-USDT-SWAP"])  # pure, no DB
shortlist = shortlist_correlations(ctx, shortlist)          # sync, reads 1D candles (bounded by ctx.now())
payload = shortlist.model_dump(mode="json")
```

- `build_shortlist(table, pinned=(), defaults=DEFAULTS) -> Shortlist` is pure and deterministic. Pass
  the owner's pins (today's `watchlist_items.pinned_by_owner`, or whatever ATL-108 decides the source
  is); it does not read the DB.
- `shortlist_correlations(ctx, shortlist) -> Shortlist` returns a copy with `correlated_pairs` filled.
  It is DB-bound: call it with `asyncio.to_thread` from the supervisor, like the feature table.
- `flag_correlated_pairs(pairs, threshold)` is the pure core if you already hold `CorrelationPair`s.
- The correlation math is now `atlas_agents.tools.account.compute_correlations(ctx, inst_ids,
  lookback_days)`; the `atlas_data.get_correlations` tool calls it too, so the Scout and the tool can
  never disagree.

Nothing is stored. ATL-108 decides whether the shortlist goes into the Scout's prompt only, or also
into a research_report.

## Config (`agents.scout.prescore` in config/defaults.yaml)

```yaml
prescore:
  shortlist_size: 25
  weights: { momentum_z: "0.25", vol_expansion_z: "0.20", catalyst_score: "0.20",
             liquidity_score: "0.15", derivatives_signal: "0.10", regime_clarity: "0.10" }
  clip_z: 3
  earnings_points: 3
  oi_scale_pct: 10
  correlation: { threshold: "0.8", lookback_days: 30 }
```

An unknown weight key raises `ValueError` (a typo cannot silently drop a component).

## Components

Each component has a raw value per instrument, z-scored cross-sectionally over every feature-table
row that has one (population std), clipped to ±3. Contribution = weight × z. Score = sum.

| component | raw value | null when |
| --- | --- | --- |
| `momentum_z` | `abs(returns_pct.7d) / atr_pct_1d`: last week's move in daily ATRs, direction-free | either is null, or ATR is 0 |
| `vol_expansion_z` | `vol_ratio_24h_30d` | null feature |
| `catalyst_score` | `news_max_importance_24h` (0 with no news) + `ln(1 + news_count_24h)` + 3 if own earnings in 48h | never (0 news is a real 0) |
| `liquidity_score` | `log10(volume_24h_usd)` | volume unknown or 0 |
| `derivatives_signal` | mean of the parts present among `abs(funding_z_7d)` and `abs(oi_change_24h_pct) / 10` | both null (spot) |
| `regime_clarity` | `regime_probability` | null feature |

Shared economic events are the same for every instrument, so they would cancel in the z-score; they
are not in `catalyst_score`. The Scout session still sees them in the feature table's
`shared_catalysts`.

**Nulls:** a null raw value means its z is null and its contribution is 0, i.e. the
cross-sectional mean. It is never treated as a low value. The row's `null_components` names it with
the reason, taken from the feature row's `missing`. When fewer than two rows have a value, or every
value is equal, the component's z is 0 everywhere and `notes` says so.

## Shortlist JSON

```json
{
  "env": "demo", "as_of": "2026-10-30T05:00:00Z", "feature_table_valid_until": "2026-10-30T05:15:00Z",
  "weights": {"momentum_z": 0.25, "vol_expansion_z": 0.2, "catalyst_score": 0.2,
              "liquidity_score": 0.15, "derivatives_signal": 0.1, "regime_clarity": 0.1},
  "shortlist_size": 25, "clip_z": 3.0, "universe_size": 30,
  "rows": [
    {"inst_id": "SOL-USDT-SWAP", "rank": 1, "score": 0.787, "in_top": true, "pinned": false,
     "components": {
       "momentum_z": {"raw": 2.7, "z": 1.41, "weight": 0.25, "contribution": 0.3525},
       "derivatives_signal": {"raw": null, "z": null, "weight": 0.1, "contribution": 0.0},
       "...": "one entry per component, all six always present"},
     "null_components": {"derivatives_signal": "funding_z_7d: no funding (spot)"},
     "features": {"...": "the full ATL-104 InstrumentFeatures row"}},
    {"inst_id": "XAU-USDT-SWAP", "rank": 28, "score": -0.54, "in_top": false, "pinned": true, "...": "..."}
  ],
  "pinned_missing": [{"inst_id": "PEPE-USDT-SWAP",
                      "reason": "not in the feature table (filtered out of the universe or blocklisted)"}],
  "correlated_pairs": [{"a": "MSTR-USDT-SWAP", "b": "BTC-USDT-SWAP", "corr": 0.91, "n": 29}],
  "correlation_threshold": 0.8,
  "notes": []
}
```

- `rows`: the top `shortlist_size` by rank (score desc, then inst_id asc), then pinned rows outside
  the top, also by rank. `rank` is the position in the whole scored universe, so a pin shows how far
  below the cut it was.
- A pin inside the top 25 is just marked `pinned: true`; it is not duplicated.
- A pin that is not in the feature table goes to `pinned_missing`, so the blocklist and universe
  filter always win over a pin.
- `correlated_pairs`: `null` until `shortlist_correlations` ran; then the pairs with correlation
  **strictly above** 0.8 (positive only), highest first. A 30-day window holds 30 closed 1D bars,
  so `n` is at most 29 daily returns. Pairs with too few overlapping returns have null corr and are
  never flagged. Instruments with no 1D candles are named in `notes`.

## For ATL-108

- Step 4's rule ("no more than 2 picks with 30-day correlation > 0.8") is data here, not enforcement.
  Enforce it after the model on the chosen picks using `correlated_pairs`, like the
  partial-evidence cap. The pairs cover the whole shortlist, so they also cover any pick.
- The correlation with **open positions** (also step 4) is not computed here. Call
  `compute_correlations(ctx, picks + held_inst_ids, 30)` and run `flag_correlated_pairs` on the
  result.
- Spread is still unknown in Phase 1 (ATL-104), so liquidity is volume only.
