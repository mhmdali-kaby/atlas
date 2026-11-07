# ATL-81 — find_analogs, seasonality, cross_asset_lead_lag (atlas_quant)

Three pure functions in `services/quant/src/atlas_quant/`, re-exported from `atlas_quant`. Config in
`config/defaults.yaml` → `quant.analogs` and `quant.seasonality` (regenerate with
`cd backend && php artisan atlas:defaults`).

## The two-minute version

- `find_analogs` takes `as_of` and **drops everything not closed by it itself** (`ts + bar <= as_of`,
  the load_candles rule; funding rows `ts <= as_of`). You can pass it a frame that runs past as_of and
  it still cannot look ahead — the hypothesis test mutates, deletes and appends bars after as_of and
  asserts the result is bit-for-bit equal. The tool layer (ATL-87) should still load with `as_of`.
- The query is the window ending at the **last closed bar**, not a caller-supplied window.
- Neighbours never overlap each other (greedy, nearest first), so `n` counts independent cases. With
  short histories you can get fewer than `k`; read `distribution[*].n`.
- Forward returns are **simple** fractions; seasonality and lead/lag use **log** returns.
- No result raises on thin data: `find_analogs` returns empty neighbours with a `note`, lead/lag gives
  `None` correlations. They raise `ValueError` only for bad input (naive datetimes, bad horizon, etc.).

## find_analogs

```python
from atlas_quant import find_analogs, load_candles
df, quality = load_candles(session, "BTC-USDT-SWAP", "1H", start, end, as_of=as_of)
res = find_analogs(df, "1H", window=24, as_of=as_of, k=20,
                   horizons=("1h", "4h", "24h", "72h"), funding=funding_series)  # funding optional
```

- `df`: UTC index on the bar grid, float `c` and `vol`. Missing slots are allowed; any window or
  forward horizon touching one is skipped. Off-grid timestamps raise.
- `funding`: `pd.Series` of funding rates indexed by settlement time (UTC). The feature is the last
  rate at or before the window's close time.
- `k`, `horizons` default from `quant.analogs` (20; 1h/4h/24h/72h). Horizons are pandas Timedelta
  strings and must be whole bars.

Features per window (each column z-scored over the eligible windows, std 0 → 1): `returns_path` (the
`window` log returns), `realised_vol` (their std ddof 1), `volume_z` (last bar's volume vs the window,
0 when flat), `funding`. Distance = sqrt(Σ weight_group × mean squared z-difference of the group).

Eligible = complete, every horizon's forward close exists (so it ended by as_of), and
`end <= query_end - window bars` (shares no bar with the query).

`AnalogsResult`:

| field | meaning |
| --- | --- |
| `bar`, `window`, `k`, `as_of`, `horizons`, `features` | as used |
| `query_start`, `query_end` | open times of the query window's first and last bar |
| `n_eligible` | eligible past windows (the base-rate sample) |
| `neighbours` | `Analog(rank, start_ts, end_ts, distance, forward_returns={horizon: float})` |
| `distribution` | per horizon `Distribution(horizon, n, median, p10, p90, pct_positive)` over neighbours |
| `base_rate` | the same over every eligible window (the unconditional base rate) |
| `note` | why there are no neighbours, else `None` |

`pct_positive` is a percent (0–100). Quantiles are numpy linear.

## seasonality

```python
seasonality(df, "hour" | "weekday" | "month" | "funding") -> SeasonalityResult
```

`SeasonalityResult(by, n, timezone="UTC", local_timezone, buckets, local_buckets)`;
`SeasonBucket(key, label, n, mean_log_return, median_log_return, t_stat)`. t = mean / (std ddof 1 /
√n), `None` if n < 2 or std 0. Returns are attributed to the ending bar's open time.

- `hour`: `buckets` are UTC hours, `local_buckets` Asia/Baghdad hours (`quant.hour_table_timezone`).
- `weekday`: UTC, 0 = Mon. `month`: UTC, 1..12.
- `funding`: offset from the nearest of `quant.seasonality.funding_hours_utc` (00/08/16), floored to
  hours; keys `-2..+1` (window `funding_window_hours` = 2; key 0 = the hour opening at settlement), and
  a last bucket with `key=None`, `label="outside"`. Meaningful on 1H or finer bars.

## cross_asset_lead_lag

```python
cross_asset_lead_lag(a, b, max_lag) -> LeadLagResult   # a, b: price Series or frames with "c"
```

Inner-joined on timestamp, log returns, `corr(lag) = pearson(ra_t, rb_{t+lag})`. **Positive lag = a
leads b.** `LeadLagResult(max_lag, n, lags=[LagCorrelation(lag, n, corr)], best_lag, best_corr,
corr_lag0, leader="a"|"b"|"none"|None)`. Best = max |corr|, ties to the smaller |lag|. Shifts are
positional on the joined index, so pass series on the same grid.

## Not built

No MCP tool wrapping (ATL-87), no event_study / backtest (other tasks).
