# ATL-85 — okx_market and atlas_data read tools

Modules: `services/agents/src/atlas_agents/tools/market.py` (okx_market) and `tools/account.py`
(atlas_data), imported from `atlas_agents/tools/__init__.py`. Tests:
`agents/tests/test_market_account_tools.py` (DB, each test in one rolled-back transaction).
Inputs are the catalogue models from ATL-84, unchanged.

## The two-minute version

- **Postgres only.** No OKX, no web, no `connections` table. A test parses both modules' imports and
  fails on trading_core/okx/httpx/ccxt or any write call.
- **`source` is `postgres:<table>[,<table>…]`.** Tools with no table yet (`get_open_orders`,
  `get_watchlist`) answer `available: false`, an empty list and a `reason`, with source
  `postgres:none (… arrives in …)`.
- **`as_of` = newest row's timestamp, capped at `ctx.now()`.** An empty result is stamped `ctx.now()`
  and has a `reason`. Empty tables (long/short, taker, funding for an instrument) are not errors.
- **Candles use the closed-bar rule** (`ts + bar <= ctx.now()`, confirmed only) in live *and* replay,
  same as `atlas_quant.load_candles`. So the bar still forming at the replay cutoff never leaks. Other
  series use `ctx.bound(ts)`.
- **Lists are newest first / most relevant first** (each result has an `order` field), so the
  framework's trimming keeps what matters.
- **Decimals reach the model as strings** (pydantic json mode); correlations are floats.

## Helpers you can reuse (from `atlas_agents.tools.market`)

`src(*tables)`, `newest(stamps, ctx)`, `closed_bars(ctx, bar)` (the WHERE clause for candles),
`listed(ctx)` (instruments known by the cutoff), `find_instrument(session, ctx, inst_id)` (raises
`not_found`), `compute_tickers(session, ctx, inst_ids)`, `market_config()`.
From `account`: `load_settings(session, ctx) -> (version, values, as_of)` (allow-listed numbers),
`SETTINGS_SNAPSHOT_KEYS`, `pearson(xs, ys)`.

## Per tool

| Tool | Reads | Notes |
| --- | --- | --- |
| `list_instruments` | instruments, candles | `volume_24h_quote` = sum of closed 1H quote volume over 24h; `min_24h_volume_usd` filters on it (USDT/USDC = USD); sorted by it |
| `get_tickers` | candles, instruments | live instruments of `inst_type`: `last`, `last_bar_ts`, `open/high/low_24h`, `change_24h_pct`, `volume_24h_quote` from closed 1H bars |
| `get_candles` | candles | `ts desc`, `start <= ts < end`, fields `ts,o,h,l,c,vol,vol_quote` |
| `get_orderbook` | instruments | always `available: false` + reason (`not_found` if the instrument is unknown) |
| `get_funding` | funding_rates | `latest`, `history` (last `history_days`; 0 = latest only), `mean_rate` |
| `get_open_interest` | open_interest | same shape; `change_pct` newest vs oldest (`oi_usd`, else `oi`) |
| `get_long_short_ratio` / `get_taker_volume` | long_short_ratio / taker_volume | last `series_limit` rows; taker adds `buy_sell_ratio` |
| `get_mark_price` | candles | **proxy**: newest closed 1m close, `is_proxy: true`, `age_seconds` |
| `get_instrument_spec` | instruments | full spec; `not_found` for unknown/not-yet-listed |
| `get_account_summary` | equity_curve, balances_snapshots, positions_snapshots | `total_equity_usd`, `unrealised_pnl_usd`, per-account USD, balances, `open_positions` |
| `get_positions` | positions_snapshots | positions with `pos != 0` at the current sync tick, largest margin first |
| `get_open_orders` | — | `available: false` until the Executor |
| `get_settings_snapshot` | settings (live) / settings_history (replay) | `version` + allow-listed numeric `values` only |
| `get_today_budget_left` | settings, equity_curve, positions_snapshots | USD from equity × pct; spend/loss used = 0 with a `note` saying why |
| `get_watchlist` | — | `available: false` until patch E |
| `get_correlations` | candles | Pearson of daily log returns of closed 1D closes, consecutive days only; `pairs` with `n`, `missing` |

"Current sync tick" = newest trading-account `balances_snapshots.taken_at` ≤ cutoff (each account sync
writes all rows with one `taken_at`); positions are read at exactly that instant, so a position closed
since an older tick does not show.

## Choices to know

- **Settings allow-list** (`SETTINGS_SNAPSHOT_KEYS`) = SettingsSchema's numeric fields minus
  `onboarding_completed`; `risk_profile` (text) is excluded. Any other key in `settings.data` is
  dropped. The test pins the set: adding a key means editing the test. No settings row → `no_data`.
- **No USDT money limits exist in Settings yet**; the budget uses the percentage limits only.
- **Replay of instruments:** only instruments listed by the cutoff (`list_time`, else `created_at`),
  but spec columns are today's values; the result carries a `note` saying so.
- Config: `agents.market_tools.{ticker_bar: 1H, ticker_window_hours: 24, mark_price_bar: 1m,
  series_limit: 200, min_correlation_obs: 3}` in `config/defaults.yaml`.
- `atlas-agents` now depends on `atlas-quant` (for `bar_delta`); ATL-87 needs it anyway.

## Not built

No order book, no real mark price, no orders, no watchlist (no tables). No War Room posting.
