# ATL-79 — atlas_quant: candles loader and compute_stats

Package `services/quant` (`atlas_quant`, uv workspace member `atlas-quant`), per decision #245.
Tests: `services/quant/tests/`. Config: `config/defaults.yaml` → `quant` (regenerate with
`cd backend && php artisan atlas:defaults`).

## The two-minute version

- Pure functions over pandas frames; the only I/O is `load_candles`. Everything is float64 (analysis,
  never money or order sizes).
- `atlas_quant` must not import `trading_core` or `atlas_agents` (trading_core's Risk Engine will import
  `atlas_quant`). That is why `atlas_quant.grid` restates ATL-47's bar grid from the same
  `candle_grid.offset_minutes` defaults instead of importing `trading_core.marketdata.grid`.
- Dependencies are all in now: numpy 2.5, pandas 3.1, scipy, statsmodels, arch, hmmlearn and
  **vectorbt 1.2.0 (it resolves and imports on Python 3.12 / numpy 2)**. `pandas-stubs` is in the root dev
  group for mypy. ATL-80/81/82 should not need to touch the lockfile.
- `uv run mypy` (bare) now checks `quant/src` too (`[tool.mypy] files`). With pandas-stubs, `Series.skew()`
  and friends return a wide union, so wrap them in `cast(float, ...)`; `groupby` keys come back as
  `Hashable` tuples, so the code loops over `sorted(set(index.hour.tolist()))` with a boolean mask instead.
- Look-ahead rule: with `as_of`, a bar is returned only if it had **closed** by then, `ts + bar <= as_of`.
  That is stricter than `ts <= as_of`: the bar still forming at `as_of` is never returned. Build replay
  bounds (ATL-85, ATL-87) on this, not on your own `ts <= as_of` filter.

## Layout

```
services/quant/
  pyproject.toml
  src/atlas_quant/
    __init__.py   re-exports load_candles, Candles, DataQuality, GapRange, compute_stats and the result models
    grid.py       BAR_SECONDS, bar_seconds(bar), bar_delta(bar), slot(bar, ts), first_slot_at_or_after(bar, ts)
    data.py       load_candles, Candles, DataQuality, GapRange
    stats.py      compute_stats, periods_per_year(bar, asset_class), StatsResult and its parts
  tests/
    test_load_candles.py     DB tests (this checkout's atlas_test_<hash>; run `php artisan test` first)
    test_stats_golden.py     golden test, 1e-9 relative
    fixtures/golden_4h.csv           28 bars of 4H, Mon 2026-10-26 00:00 .. Fri 2026-10-30 12:00 UTC
    fixtures/make_golden.py          the hand calculation: plain-Python loops, no numpy/pandas
    fixtures/golden_4h_worked.csv    spreadsheet: every intermediate per bar (returns, ln(h/l)^2, TR, ATR, drawdown...)
    fixtures/golden_4h_expected.json the figures the test compares to
```

A test re-runs `make_golden.py` and fails if the worked CSV or the expected JSON drift from it. Reuse
this pattern for the golden tests in ATL-80/81/82: an independent loop-based reference in `fixtures/`,
its output committed, and the test comparing at 1e-9.

## load_candles

```python
from atlas_quant import load_candles

df, quality = load_candles(session, "BTC-USDT-SWAP", "1H", start, end, as_of=None, env=None)
```

- `session`: a SQLAlchemy `Session` or `Connection` (engine from `atlas_shared.db.make_engine`).
- Window: `start <= ts < end`, confirmed bars only, `env` defaults to `env.default` (`demo`). All datetimes
  must be timezone-aware (ValueError otherwise); they are converted to UTC.
- `df`: index `ts` (UTC `DatetimeIndex`, ascending, unique); float64 columns
  `o, h, l, c, vol, vol_ccy, vol_ccy_quote` (the last two NaN where OKX gave none).
- Duplicates: the PK forbids identical ts, so a "duplicate" is an off-grid ts in the same grid slot as
  another row. One row per slot is kept (the on-grid one, else the earliest) and the rest are counted.

`DataQuality` (frozen pydantic):

| field | meaning |
| --- | --- |
| `inst_id`, `bar`, `env` | the series |
| `window_start`, `window_end`, `as_of` | as passed, in UTC |
| `expected_bars` | grid slots with `start <= slot < end` and, with `as_of`, `slot + bar <= as_of` |
| `returned_bars` | rows in `df` |
| `missing_bars` | expected slots with no bar |
| `gaps` | `list[GapRange]`, one per run of consecutive missing slots |
| `duplicate_count` | extra rows dropped because their slot was already held |
| `zero_volume_count` | rows in `df` with `vol == 0` |
| `gap_in_window` | any missing slot anywhere in the window |
| `interior_gap` | a missing slot between the first and last returned bar (the series has a hole) |

`GapRange`: `from_ts`, `to_ts` (first and last **missing** bar open times, inclusive, the same convention as
ATL-47's `data_quality_issues` gaps), `missing_bars`, `position`: `leading` (before the first returned bar:
backfill doesn't reach that far), `interior`, `trailing` (after the last: not polled yet), or `empty`
(nothing returned at all). Example, 3 bars deleted at 10:00–12:00 from a 24-bar 1H window:

```python
DataQuality(expected_bars=24, returned_bars=21, missing_bars=3, duplicate_count=0, zero_volume_count=0,
            gaps=[GapRange(from_ts=2026-10-01T10:00Z, to_ts=2026-10-01T12:00Z, missing_bars=3,
                           position="interior")],
            gap_in_window=True, interior_gap=True, ...)
```

For the Historian's rule "unrepaired gaps in the window → does_not_support for a backtest claim" (ATL-87),
`interior_gap` is the flag to read; leading/trailing gaps only mean the sample is shorter.

## compute_stats

```python
from atlas_quant import compute_stats
result = compute_stats(df, "4H", asset_class="crypto")   # -> StatsResult
```

`df` needs a tz-aware index and float `o, h, l, c` columns (it is sorted first). `asset_class` is one of
`crypto, stock, etf, index, commodity, other`; default `quant.default_asset_class` (crypto). Use the
instrument's `asset_class` column.

`StatsResult` (frozen pydantic, all floats are fractions unless the name ends in `_pct`):

| field | definition |
| --- | --- |
| `bar`, `asset_class`, `n`, `period_start`, `period_end` | sample; period = first/last bar open time |
| `periods_per_year` | `days_per_year[asset_class] * 86400 / bar_seconds` (365 crypto/other, 252 the rest) |
| `log_returns`, `simple_returns` | `ReturnsSummary(count, mean, std (ddof 1), min, max, total)`; n bars give n-1 close-to-close returns; `total` = ln(c_last/c_first) or c_last/c_first - 1 |
| `realised_vol` | std(log r) * sqrt(P) |
| `parkinson_vol` | sqrt(mean(ln(h/l)^2) / (4 ln 2)) * sqrt(P) |
| `garman_klass_vol` | sqrt(mean(0.5 ln(h/l)^2 - (2 ln 2 - 1) ln(c/o)^2)) * sqrt(P) |
| `atr_window`, `atr`, `atr_pct` | Wilder ATR(14): seed = mean of the first 14 TRs (TR_0 = h-l), then (prev*13 + TR)/14; last value, and as % of the last close |
| `skew`, `excess_kurtosis` | bias-adjusted sample G1 / G2 of log returns (pandas convention) |
| `drawdown` | `DrawdownSummary(max_drawdown ≤ 0, peak_ts, trough_ts)` on closes; peak = latest running high before the trough; 0 / None / None when there is none |
| `adr_pct`, `adr_days` | mean over complete UTC days of (day high - day low) / day's first open * 100 |
| `hour_timezone`, `hourly_utc`, `hourly_local` | `HourBucket(hour, n, mean_log_return, std_log_return or None if n < 2)` by the open hour of the bar that ends each return, in UTC and Asia/Baghdad |
| `weekday` | `WeekdayBucket(weekday 0=Mon, name, n, mean_return)` of simple returns, UTC weekday |

Too-short frames give `None` rather than raising (ATR needs 14 bars, skew 3 returns, kurtosis 4, std 2).

## Config (`quant:` in defaults.yaml)

`days_per_year` (map), `default_asset_class`, `atr_window` (14), `hour_table_timezone` (Asia/Baghdad).

## Not built

- No other Historian statistics (ATL-80/81/82) and no MCP tool (ATL-87).
- `compute_stats` does not read DataQuality; callers pass both on (the tool layer reports gaps).
- The lint command in the CCQ-managed CLAUDE.md still lists the mypy paths without `quant/src`; bare
  `uv run mypy` covers it through pyproject.
