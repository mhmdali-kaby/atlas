# ATL-134 — derivatives data poller and derivatives features

For: the Derivatives & Sentiment Analyst agent task (and anything else that wants funding z, the
OI-price quadrant, L/S, taker flow or basis).

## What fills the tables now

`trading_core.marketdata.derivatives.DerivativesPoller`, scheduler job `derivatives_poller`, every
`derivatives_poller.interval_seconds` (300). Registered in `trading_core.app.default_scheduler`,
sharing the public `RateLimiter` with the backfill / candle poller / quality checker.

Each tick reads the market feed's instrument set (`trading_core.ws.subscriptions.desired_instruments`:
`market_ws.always_subscribe` + today's Baghdad watchlist minus owner-removed + open positions) and:

| table              | key                          | source                                               | for |
| ------------------ | ---------------------------- | ---------------------------------------------------- | --- |
| `long_short_ratio` | (env, ccy, period, ts)       | `rubik/stat/contracts/long-short-account-ratio`, period `5m` | `always_ccys` [BTC, ETH] + crypto base ccys of the set |
| `taker_volume`     | (env, ccy, period, ts)       | `rubik/stat/taker-volume`, instType `CONTRACTS`, `5m` | same ccys |
| `open_interest`    | (env, inst_id, ts)           | `public/open-interest` instType SWAP, one row per poll (OKX `ts`) | every `-SWAP` in the set, stock/commodity perps too |
| Redis `atlas:md:<env>:<instId>` fields `index`, `index_ms` | hash per perp (ATL-125's snapshot hash) | `market/index-tickers?quoteCcy=` once per `derivatives_poller.index_quote_ccys` entry (USDT, USDC) that a watched perp's index uses | every `-SWAP` in the set |

**Index fields.** A perp's index is its `instFamily` (`BTC-USDT`; from `instruments.inst_family`,
else the instId without `-SWAP`), matched to the index-tickers row's `instId`. The poller HSETs
`index` = `{"idxPx": "114312.4", "ts": "1793207700512"}` (JSON, strings as OKX sends them) and
`index_ms` = `"1793207700512"` (OKX's ts of the index, not the poll time), then re-applies
`market_ws.snapshot_ttl_seconds` to the key. The WS feed's fields in the same hash are untouched.
`read_snapshot` now returns it as `MarketSnapshot.index` (dict or None) and `channel_at["index"]`
(the generic `<name>_ms` rule). `last_msg_ms` is not touched, so the index never makes a stale
instrument look fresh. A perp whose index is missing from the response keeps the previous value,
which then ages into `stale`. Rate limit `market/index-tickers`: 20 / 2 s (`okx_rest.rate_limits`).
Client method: `OkxClient.get_index_tickers(quote_ccy=..., inst_id=...) -> list[IndexTicker]`
(`inst_id`, `idx_px` Decimal, `ts`).

Base ccy = the part of the instId before the first `-` (OKX leaves `baseCcy` empty on SWAP rows).
Instruments whose `instruments.asset_class` is not `crypto` get no rubik calls (rubik is crypto-only)
but still get OI. All upserts, so a repeated payload adds nothing. One failing ccy/instrument is
logged and counted in `failed`; the others still run. Return value of `tick()`:

```json
{"env":"demo","ccys":["BTC","ETH","SOL"],"inst_ids":["BTC-USDT-SWAP","SOL-USDT-SWAP"],
 "long_short_rows":6,"taker_rows":6,"oi_rows":2,"index_rows":2,"failed":0}
```

Why OI is polled here too: the backfill only stores rubik's 1D currency OI series, which cannot give
a 1h or 4h change. Funding is **not** polled here — `funding_rates` is still the backfill's settled
history (fine: rates change every 8h).

## `atlas_agents.derivatives.features`

Pure functions over frames, plus a loader. Config: `agents.derivatives` in `config/defaults.yaml`.

```python
from atlas_agents.derivatives.features import load_frames, compute_features
frames = load_frames(session, env, "BTC-USDT-SWAP", as_of)          # rows ts <= as_of only
snap = read_snapshot(redis, env, "BTC-USDT-SWAP")                    # atlas_shared.market_data
feats = compute_features("BTC-USDT-SWAP", frames, as_of, snapshot=snap, index_px=None)
feats.model_dump(mode="json")
```

`DerivativesFeatures`:

- `funding`: `current`, `annualised` (current × periods/year, period = median gap of stored rates,
  else `default_interval_hours` 8), `interval_hours`, `z` = `{"7d": float|None, "30d": float|None}`
  ((current − mean)/std ddof 1 over rates with `as_of − N d < ts ≤ as_of`, null below
  `min_funding_obs` 3 or zero spread), `sign_flips` (consecutive non-zero rates of opposite sign in the
  last `sign_flip_days` 3; zeros skipped), `sign_flip_days`, `as_of`, `missing`.
- `open_interest`: `series` (the instId, or the bare ccy when only rubik's series exists), `oi`,
  `as_of`, `windows: [{hours, oi_change_pct, price_change_pct, quadrant, from_ts, to_ts, missing}]`
  for 1/4/24h, `unreliable`, `unreliable_reasons`, `missing`. The reference row is the newest at least
  N h older than the newest row and no more than N × `window_stretch` (1.5) older; the price change is
  between the same two times from closed 5m candle closes. `quadrant` ∈ new_longs / short_covering /
  new_shorts / long_liquidation / flat (flat when |price %| < 0.25 or |OI %| < 0.5). Newest OI older
  than `max_age_minutes` (30) → every window null, `missing.windows` starts with `stale`.
- `long_short`: `ratio`, `change_24h` (absolute), `change_24h_pct`, `as_of`, `missing` (stale → null).
- `taker`: `windows: [{hours, buy_sell_ratio, rows, missing}]` for 1/4h = sum(buy)/sum(sell).
- `basis`: `basis_bps`, `mark_px`, `index_px`, `reason`, `as_of` (mark channel time), `index_as_of`
  (OKX ts of the index used). The index is the snapshot's stored `index` unless the caller passes
  `index_px` (then `index_at=` sets its time). `reason`, checked in this order: `stale` (no snapshot,
  or the `mark-price` channel older than `market_ws.market_data_max_age_seconds` 30 s) / `no_mark` /
  `index_unavailable` (no index stored) / `stale` (index older than 30 s ×
  `agents.derivatives.index_max_age_multiplier` 10 = 300 s, `index_max_age_seconds()`) /
  `bad_index` (≤ 0) / null with a numeric `basis_bps`.
- `unreliable` + `unreliable_reasons` (code → sentence): `zero_open_interest`, `zero_volume` (5m
  candle volume 0 over 24h), `zero_taker_volume`. The affected numbers are null, never computed on.
- `data_as_of`.

## Two-minute version

- **basis reads the index the poller stores**: `compute_features(..., snapshot=read_snapshot(...))`
  is enough; no `index_px` argument needed. The index is at most one 5-min poll old, so its limit is
  10 × the market-data max age; anything older is `stale`, never computed on.
- No predicted next funding rate: it is not stored (only settled rates are).
- Nothing calls `compute_features` yet; the agent task wires it into the tool/precompute table.
- Units: floats for these analytic features (as in the Scout feature table); `mark_px`/`index_px`
  are Decimal.
