# ATL-87 — history tools and the Historian's verdict rules

Code: `services/agents/src/atlas_agents/tools/history.py` (the nine `history.*` tools, registered on
import of `atlas_agents.tools`), `services/agents/src/atlas_agents/schemas.py` (`HistoryVerdict`,
`enforce_verdict_rules`). Config: `config/defaults.yaml` → `agents.history`. Tests:
`agents/tests/test_history_tools.py` (DB), `test_history_verdict.py`.

## The two-minute version

- Every tool loads candles with `atlas_quant.load_candles(..., as_of=ctx.now())`, so in replay only
  bars closed by `replay_as_of` reach the statistic. The envelope `as_of` is the close time of the
  newest bar used (`ctx.now()` when nothing was loaded).
- **Every result has the same outer shape**: `{sample, status, note, result}` (+ a few tool extras).
  `status` is `ok` | `no_data` | `insufficient_data`. No candles is `no_data` with `result: null` —
  never an exception. Bad arguments are `ToolError`s (`invalid_input`, `unknown_event_type`,
  `invalid_rule`).
- `sample.n` is the number of **cases** behind the statistic and `sample.n_meaning` says what they
  are (bars, returns, analogs, (instrument, event) cases, trades). `sample.weak_sample` = n < 20.
- To enforce the Historian's rules, build `VerdictEvidence(n=sample.n, backtest_based=..., unrepaired_gap=sample.unrepaired_gap)`
  and call `enforce_verdict_rules(verdict, evidence)`.
- The catalogue inputs changed deliberately: `find_analogs` lost the free-form `pattern_spec` for
  explicit `bar, window, k, horizons, include_funding`; `detect_regime`/`drawdown_profile` take
  `HistorySeriesInput` (inst_id, bar?, lookback?); every series tool gained optional `bar`/`lookback`.

## The sample block

```json
"sample": {
  "inst_id": "BTC-USDT-SWAP", "inst_ids": ["BTC-USDT-SWAP"], "bar": "1H",
  "period": {"start": "2026-08-01T00:00:00Z", "end": "2026-08-30T04:00:00Z"},
  "n": 5, "n_meaning": "independent analog windows (neighbours)", "bars_loaded": 700,
  "weak_sample": true, "unrepaired_gap": false, "reference_data": false,
  "data_quality": [{"inst_id": "BTC-USDT-SWAP", "bar": "1H", "expected_bars": 26280,
    "returned_bars": 700, "missing_bars": 25580, "duplicate_count": 0, "zero_volume_count": 0,
    "gap_in_window": true, "interior_gap": false,
    "gaps": [{"from_ts": "...", "to_ts": "...", "missing_bars": 25580, "position": "leading"}]}]
}
```

`period.end` is the close of the last bar. `unrepaired_gap` = any series has an `interior_gap`
(leading/trailing gaps only mean a shorter sample). `reference_data` is always false for now.

## Tools (input → `result` type, `n`)

| tool | input (beyond inst_id) | result | n |
| --- | --- | --- | --- |
| compute_stats | bar, lookback | `StatsResult` | bars |
| detect_regime | bar, lookback | `RegimeResult` (method, fallback_reason) | bars |
| volatility_forecast | bar, lookback, horizon (h) | `VolForecast` (sigma_atr) | returns |
| drawdown_profile | bar, lookback | `DrawdownProfile` | bars |
| seasonality | bar, lookback, by | `SeasonalityResult` | returns |
| find_analogs | bar, window, k, horizons, lookback_years, include_funding | `AnalogsResult` | neighbours |
| cross_asset_lead_lag | a, b, max_lag, bar, lookback | `LeadLagResult` | joined returns |
| event_study | inst_id **or** asset_class, event_type, window (h,h), bar, lookback_years | `EventStudyResult` + `event_type`, `events_used` | cases |
| backtest_rule | inst_ids, rule_spec (RuleSpec JSON), period (dates, end inclusive), costs, bar, splits | `BacktestResult` + `taker_fee_source` | trades |

- Defaults per tool: `agents.history.<tool>.{bar, lookback_bars}`.
- **event_study** event types come from `agents.history.event_study.event_types` (cpi, ppi, fomc,
  nfp, gdp, retail_sales, unemployment: name regexes + countries over `economic_events`), or
  `earnings` (needs inst_id; `earnings_events.symbol` = the instrument's base_ccy, time mapped by
  `earnings_hour_utc`, an approximation ignoring DST). Events are bounded `scheduled_at <= as_of`.
  `asset_class` uses the first `max_instruments` live instruments by inst_id.
- **backtest_rule** fee: `costs.taker_fee_rate` from the caller, else
  `agents.history.backtest.taker_fee_rate` (0.0005) — there is no stored trade-fee read yet; swap it
  in when one exists. Slippage: `costs.slippage_bps` or quant's 2 bps. Funding rows (`funding_rates`,
  bounded by as_of) are included for each instrument. `warmup_bars` load before the period.
  Instruments with no candles are excluded with a note; all missing → `no_data`.
- Bodies run in `asyncio.to_thread` (HMM/GARCH/vectorbt are CPU-bound).

## HistoryVerdict / enforce_verdict_rules

`HistoryVerdict` (extra=forbid, frozen): `question`, `inst_id|null`, `bar|null`, `period: str|null`,
`n ≥ 0`, `distribution` and `base_rate`: `{horizon: {median, p10, p90, pct_positive 0-100, n?}}`,
`regime {label, probability?, days_in_regime?}|null`, `stop_noise_check {stop_distance_atr,
median_4h_range_atr, verdict: ok|inside_noise}|null`, `overfit_warning=false`, `verdict:
supports|weakly_supports|does_not_support|contradicts` (required), `key_stat`, `data_quality_notes=[]`,
`data_as_of` (tz-aware, required).

`enforce_verdict_rules(verdict, evidence=None) -> (HistoryVerdict, list[VerdictCorrection])`:
1. `n < agents.history.verdict.min_n` (20; `evidence.n` or else `verdict.n`) caps `supports` at
   `weakly_supports` (weaker verdicts untouched).
2. `evidence.backtest_based and evidence.unrepaired_gap` → `does_not_support` (even from
   `contradicts`) and a note appended to `data_quality_notes` (with `gap_note` if given).
`VerdictCorrection(rule: small_sample|backtest_on_gapped_data, from_verdict, to_verdict, reason)`.
The input verdict is never modified.

## Not built

- The Historian agent/prompt calling these (ATL-88 or later), and wiring `enforce_verdict_rules`
  into its output path.
- The overfit rule is reported (`overfit_warning`) but not enforced by `enforce_verdict_rules`.
- Underlying reference data (`reference_data: true`).
