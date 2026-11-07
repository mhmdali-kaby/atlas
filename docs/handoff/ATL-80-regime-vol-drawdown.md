# ATL-80 — detect_regime, volatility_forecast, drawdown_profile (atlas_quant)

Builds on `docs/handoff/ATL-79-atlas-quant.md`. All three take a frame shaped like `load_candles`'s
(tz-aware index, float columns), sort it, convert to UTC, and infer the bar from the **median index
spacing** (no `bar` argument). Config: `config/defaults.yaml` → `quant.regime`, `quant.volatility`,
`quant.drawdown`. Each has a frozen params model (`RegimeParams`, `VolParams`) with `from_defaults()`;
override per call with `params.model_copy(update={...})`.

## The two-minute version

- Every result says which **method** produced it and, for a fallback, a human `fallback_reason`. The
  Historian tool (ATL-87) should pass both on: "rule-based regime (HMM did not converge)".
- `detect_regime` is seeded (`random_state`), but floats can differ in the last ulp between runs (BLAS);
  labels, counts and timestamps are exact. Compare floats with a tolerance.
- The fallback regime ranks current vol **within the last 250 bars**: a storm that has lasted longer than
  the lookback becomes "normal" there (the HMM, fitted on the whole frame, still calls it high_vol).
- `volatility_forecast(...).sigma_atr` is the number the Risk Engine's stop sanity check wants: horizon
  sigma in price divided by Wilder ATR(14) of the same bars.

## detect_regime(df, params=None) -> RegimeResult

Needs `h, l, c`. HMM (hmmlearn GaussianHMM, `n_states` 3, full covariance, `hmm_n_iter` 1000) on
`[100·ln(c_t/c_{t-1}), ln(rolling std of that over vol_window=20)]`.

State labels: `high_vol` if exp(mean ln vol) ≥ `high_vol_ratio` (2.0) × the calmest state's; else
`trend_up`/`trend_down` if |t| ≥ `trend_t` (3.0) where t = mean/std·sqrt(bars in the state); else
`low_vol` for the calmest state when a high_vol state exists; else `range`.

Fallback when feature rows < `min_bars` (200), the fit raises, or EM hits `hmm_n_iter` without the gain
dropping under `hmm_tol` (hmmlearn's own `converged` counts hitting n_iter as converged; we don't). Rule
per bar: vol percentile ≥ 80 → high_vol; ADX(14) ≥ 25 → trend_up/down by +DI vs −DI; percentile ≤ 20 →
low_vol; else range. Raises ValueError only when not even one rolling vol exists (≤ vol_window bars).

| field | meaning |
| --- | --- |
| `method` | `hmm` or `fallback`; `fallback_reason` str or None |
| `label` | trend_up / trend_down / range / high_vol / low_vol |
| `probability` | HMM posterior of the last bar being in any state with that label; None for fallback |
| `days_in_regime`, `bars_in_regime`, `regime_start` | final run of the same **label** (not state); days = last bar end − run start |
| `as_of`, `n` | last bar open time, bars in the frame |
| `n_states`, `states` | HMM only: `RegimeState(state, label, mean_return_pct, return_std_pct, typical_vol_pct, occupancy)` |
| `adx`, `vol_percentile` | current values, both methods (None when too short) |

## volatility_forecast(df, horizon_hours, params=None) -> VolForecast

Needs `h, l, c`. Percent log returns. h = ceil(horizon_hours / bar_hours) bars. GARCH(1,1), normal,
`garch_mean` constant (or zero), `rescale=False`; horizon variance = sum of arch's 1..h step variances.
EWMA fallback (λ = `ewma_lambda` 0.94, seed r₁²; horizon var = h × next-bar var) when returns <
`garch_min_bars` (100), arch's `convergence_flag != 0`, or alpha+beta ≥ 1. Needs ≥ 3 bars.

Fields: `method` (garch|ewma), `fallback_reason`, `horizon_hours`, `horizon_bars`, `bar_hours`,
`n_returns`, `sigma_pct` (horizon), `sigma_next_bar_pct`, `sigma_atr` (None if < 14 bars), `atr`,
`last_close`, `as_of`, `params` (`omega, alpha, beta, mu` or `lambda`).
`VolParams.max_iter` exists only to force non-convergence in tests.

## drawdown_profile(df, top_n=None) -> DrawdownProfile

Needs `c`. Episode = from a running high (the last bar setting or equalling it) to the first close ≥ that
high. `DrawdownEpisode(depth<0, start, trough, recovery|None, duration_bars/_days, time_to_recover_bars/_days|None)`;
`DrawdownProfile(n, period_start, period_end, n_drawdowns (all), top (deepest top_n=5), current:
CurrentDrawdown(depth≤0, peak, duration_bars, duration_days), median_time_to_recover_days)` — the median of
trough→recovery over the **listed** episodes that recovered.

## Tests

`quant/tests/test_regime.py` (calm→4× storm, 2 and 3 states, 5 seeds; forced non-convergence; fit error;
short series), `test_volatility.py` (simulated GARCH ω=0.02 α=0.08 β=0.90, 3000 1H bars, 5 seeds within
15% of the true conditional 24h sigma; 10 bars → ewma; EWMA hand calc), `test_drawdown_golden.py`
(hand-built 20 bars, expected values written in the test docstring).

## Not built

No MCP tool wrapper (ATL-87), no persistence, no DataQuality handling (callers pass gaps on).
