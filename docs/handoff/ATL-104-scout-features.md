# ATL-104 — Market Scout universe filter and feature table

For ATL-105 (pre-score), ATL-108 (08:00 pipeline) and ATL-109 (intraday re-run).

## Entry points

```python
from atlas_agents.scout.universe import build_universe, filter_universe, config_from, Candidate
from atlas_agents.scout.features import build_feature_table, save_feature_table, compute_features

ctx = ToolContext(env="demo", session_factory=..., agent="market_scout", replay_as_of=T_or_None)
universe = build_universe(ctx)                  # UniverseResult, at ctx.now()
table = build_feature_table(ctx, universe)      # FeatureTable; raises if universe.as_of != ctx.now()
with ctx.session_factory() as s:
    report = save_feature_table(s, ctx, table)  # adds + flushes a research_reports row; caller commits
    s.commit()
```

Both are synchronous (plain SQLAlchemy). From the async supervisor, call them with
`asyncio.to_thread`. A full table loads candles and runs `detect_regime` (HMM) for every kept
instrument, so it is DB-bound: keep it off the event loop.

## UniverseResult

```json
{
  "as_of": "2026-10-30T05:00:00Z", "settings_version": 3,
  "volume_basis": "24h quote volume summed from closed 1H candles; USDC and USD quotes counted 1:1 as USD",
  "kept": [{"inst_id": "BTC-USDT-SWAP", "inst_type": "SWAP", "asset_class": "crypto",
            "market": "crypto_perp", "volume_24h_usd": "900000000", "spread_bps": null,
            "notes": ["spread_unknown"]}],
  "rejected": [{"inst_id": "XAU-USDT-SWAP", "market": "commodity_perp",
                "reasons": ["state", "spread"],
                "detail": ["state is suspend, not live", "spread 9 bps is above the 8 bps maximum"]}]
}
```

Reason codes: `market`, `state`, `blocklist`, `age`, `volume`, `volume_unknown` (quote currency not
USDT/USDC/USD), `spread`. A rejected row lists every failed check, not only the first. `kept` is
sorted by `volume_24h_usd` descending.

Two things are deliberate and worth knowing:

- **Spread is unknown in Phase 1.** Atlas stores no order book (the `get_orderbook` tool says the same),
  so `build_universe(ctx)` passes no spreads and every instrument is kept with the
  `spread_unknown` note. `build_universe(ctx, spreads_bps={...})` applies the 8 bps check once a spread
  source exists (Phase 2 WS books).
- **Settings.** `min_24h_volume_usd_<market>` and `max_spread_bps` come from the settings row in force
  at as_of (settings_history in replay). Settings → Markets has no enabled-markets or blocklist field yet,
  so those come from `agents.scout.universe` in config/defaults.yaml. If a later screen writes
  `enabled_markets` / `blocklist` into settings data, `config_from` already reads them.

## FeatureTable (stored as `research_reports.data`, kind `feature_table`, agent `market_scout`)

Top level: `env, as_of, valid_until (as_of + 15 min), settings_version, btc_reference, spy_reference,
shared_catalysts[], rows[], rejected[] (from the universe), notes[]`.

Each row (`InstrumentFeatures`):

| field | meaning |
| --- | --- |
| `returns_pct` | `{"1d","3d","7d","30d"}` simple close-to-close %, 1D bars |
| `atr_pct_1h`, `atr_pct_1d` | Wilder ATR(14) % of last close, last 100 bars |
| `vol_ratio_24h_30d` | realised vol of last 24 1H returns / last 720 |
| `volume_z_30d` | latest day's quote volume vs the 30 days before (ddof 1) |
| `ma_distance_pct` | `{"20d","50d","200d"}` (close / SMA − 1) × 100 |
| `breakout_high_20d`, `breakout_low_20d` | last close beyond the prior 20 bars' high / low |
| `funding_rate`, `funding_z_7d` | newest rate; z of newest vs last 7 days |
| `oi_change_24h_pct`, `oi_series` | `oi_series` is the instId, or the base ccy when only the rubik ccy series exists |
| `long_short_ratio` | base ccy, 1H period, null if older than 48 h |
| `corr_btc_30d`, `corr_spy_30d` | 30 daily log returns; `spy_reference` names SPY or US100 |
| `news_count_24h`, `news_max_importance_24h` | linked news, published in the last 24 h |
| `catalysts` | this instrument's own earnings in 48 h; economic events are in `shared_catalysts` |
| `catalyst_count_48h`, `catalyst_max_importance_48h` | shared + own (earnings carry no importance) |
| `regime_label`, `regime_probability`, `regime_method` | `detect_regime` on the Historian's series |
| `market`, `volume_24h_usd` | copied from the universe |
| `data_as_of` | newest input timestamp used, ≤ as_of |
| `missing` | `{feature: reason}` for every null feature (keys like `returns_pct.30d`) |
| `quality_notes` | gaps/duplicates in the candles, spread/listing unknown |

**For the pre-score:** a null is "unknown", never 0. Read `missing` to decide how to treat it; do not
coerce to 0. `news_count_24h` is a real 0 when nothing was linked.

Every read is bounded by as_of (candles closed by it, `ts <= as_of`, news `published_at` and
`fetched_at <= as_of`, calendar rows `created_at <= as_of`), so a replay table cannot see the future.
All parameters live under `agents.scout` in config/defaults.yaml.
