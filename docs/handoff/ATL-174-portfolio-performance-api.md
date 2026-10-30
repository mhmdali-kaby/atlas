# ATL-174 — Portfolio performance API

For whoever builds the Performance page (brief §13) and anyone reusing the statistics.

## Two-minute version

- `GET /api/portfolio/performance?env=demo|live&range=1M|3M|YTD|ALL` (auth:sanctum). `range` is
  case-insensitive (`All` works) and defaults to `ALL` (`portfolio_performance.default_range`). 1D/1W → 422.
- Reads Postgres only: `positions_closed` (+ `proposals` for R, + `instruments` for asset class),
  `equity_curve`, `deposits_withdrawals`. Code: `App\Support\Portfolio\PerformanceService`.
- Every metric is `{value, sample_size, reason}`. `value` is a decimal string or null; `reason` is set
  only when `value` is null. Show the reason, not "0".
- Trades are in range by `closed_at`. Only USD-settled rows (`ccy` USDT/USDC/USD) are counted;
  coin-margined rows are skipped and counted in `trades.excluded_non_usd` (no FX yet).
- R is `HistoryQuery::rMultiple` (2 dp, the same figure the History page shows); external
  positions have no proposal stop, so they have no R and are left out of `expectancy_r`.

## Definitions

| field | definition | scale |
| --- | --- | --- |
| `win_rate_pct` | wins ÷ (wins + losses) × 100 on `realised_pnl`; flats (0) are neither | 2 dp, fixed (`"66.67"`, `"100.00"`) |
| `avg_win_usd` / `avg_loss_usd` | mean PnL of winners / of losers (loss is negative) | ≤ 8 dp, trailing zeros stripped |
| `profit_factor` | gross profit ÷ abs(gross loss); null `no_losses` with no losers | 4 dp, fixed |
| `expectancy_r` | mean R over trades that have R; null `no_r` if none | 4 dp, fixed |
| `max_drawdown` | deepest % fall from the running peak over every equity_curve point in range; `usd` = that episode's peak − trough | pct 2 dp fixed (`"10.00"`), usd stripped |
| `sharpe` / `sortino` | daily, annualised × √365, risk-free 0 | 4 dp, fixed |

Sharpe/Sortino: a day is an **Asia/Baghdad** calendar day; its value is the last equity point of that
day. Daily return = (E_d − E_{d−1} − net USD-stable deposits on day d) ÷ E_{d−1}, so deposits and
withdrawals are not returns. Sharpe = mean ÷ sample stdev (n−1). Sortino = mean ÷ √(Σ min(r,0)² ÷ n).
`sample_size` is the number of days of equity; under `min_days_for_ratios` (10) → null
`insufficient_data`. Other null reasons: `zero_volatility`, `no_downside`.

Reasons used: `no_trades`, `no_wins`, `no_losses`, `no_r`, `insufficient_data`, `zero_volatility`, `no_downside`.

## Response (abridged real shape)

```json
{ "data": {
  "env": "demo", "range": "ALL", "from": null, "to": "2026-10-30T09:00:00.000000Z", "timezone": "Asia/Baghdad",
  "trades": { "total": 6, "wins": 4, "losses": 2, "flats": 0, "excluded_non_usd": 0,
              "gross_profit_usd": "70", "gross_loss_usd": "-15", "net_pnl_usd": "55" },
  "win_rate_pct":  { "value": "66.67",  "sample_size": 6, "reason": null },
  "avg_win_usd":   { "value": "17.5",   "sample_size": 4, "reason": null },
  "avg_loss_usd":  { "value": "-7.5",   "sample_size": 2, "reason": null },
  "profit_factor": { "value": "4.6667", "sample_size": 6, "reason": null },
  "expectancy_r":  { "value": "0.9167", "sample_size": 6, "reason": null },
  "max_drawdown": { "usd": "11", "pct": "10.00", "peak_usd": "110", "trough_usd": "99",
                    "peak_at": "2025-05-01T00:01:00.000000Z", "trough_at": "2025-05-01T00:03:00.000000Z",
                    "sample_size": 6, "reason": null },
  "sharpe":  { "value": null, "sample_size": 7, "reason": "insufficient_data" },
  "sortino": { "value": null, "sample_size": 7, "reason": "insufficient_data" },
  "breakdowns": {
    "by_instrument":  [ { "inst_id": "BTC-USDT-SWAP", "pnl_usd": "20", "trades": 2, "wins": 1, "losses": 1 } ],
    "by_asset_class": [ { "asset_class": "crypto", "pnl_usd": "20", "trades": 2, "wins": 1, "losses": 1 } ],
    "by_agent":       [ { "agent": "strategist", "pnl_usd": "55", "trades": 6, "wins": 4, "losses": 2 },
                        { "agent": null, "pnl_usd": "7", "trades": 1, "wins": 1, "losses": 0 } ],
    "by_weekday": [ { "weekday": 1, "pnl_usd": "0", "trades": 0, "wins": 0, "losses": 0 } /* … always 7, ISO 1 = Monday */ ],
    "by_hour":    [ { "hour": 0, "pnl_usd": "20", "trades": 1, "wins": 1, "losses": 0 } /* … always 24, 0..23 */ ],
    "heatmap":    [ { "weekday": 4, "hour": 0, "pnl_usd": "20", "trades": 1, "wins": 1, "losses": 0 } ]
  }
} }
```

- `max_drawdown` with no equity: `usd`/`pct` null, reason `insufficient_data`; a curve that never
  falls gives `usd "0"`, `pct "0.00"`.
- by_instrument / by_asset_class / by_agent are sorted by PnL, best first, ties by key.
  `agent: null` = positions Atlas did not open (external). Missing `instruments` row → `asset_class "other"`.
- Weekday and hour are **Baghdad** (UTC+3, no DST): a trade closed 21:30 UTC Wednesday is weekday 4, hour 0.
  `heatmap` lists only non-empty cells, ordered by (weekday, hour).

## Config (`config/defaults.yaml` → `portfolio_performance`)

`ranges`, `default_range`, `min_days_for_ratios` (10), `annualisation_days` (365), `pct_scale` (2),
`ratio_scale` (4), `money_scale` (8), `calc_scale` (24, internal).

## Not built

- No caching: the drawdown scans every equity_curve point in range in SQL (window function); trades
  and day closes are aggregated in PHP. Fine at demo volumes.
- Drawdown is on raw equity (deposits are not removed); only the daily returns are flow-adjusted.
- No strategy-tag breakdown (setup_tag is never filled yet; see ATL-172 handoff).
