# ATL-133 — TA structure precompute (levels, volume, patterns, microstructure)

For ATL-132 (`ta.table`) and ATL-135 (Technical Analyst agent). Brief §7.4, second part.

## Where it is

`services/agents/src/atlas_agents/ta/`

| Module | Public functions |
| --- | --- |
| `_structure.py` | `SwingPoint`, the `Swing` protocol, `cut`, `known_swings` |
| `levels.py` | `build_zones`, `prior_period_levels`, `round_numbers` |
| `volume.py` | `relative_volume`, `obv_series`, `obv_trend`, `session_vwap`, `anchored_vwap`, `volume_profile` |
| `patterns.py` | `breakout_retest`, `failed_breakout`, `trading_range`, `flag_pennant`, `double_top_bottom`, `detect_patterns` |
| `microstructure.py` | `imbalance`, `spread_bps`, `summarise_book`, `walk_book` |

`ta/__init__.py` holds only a docstring. If ATL-132 adds its own, keep either one; neither exports anything.

The exact rule for every number is in each module's docstring. The thresholds live in `agents.ta_structure.*` in `config/defaults.yaml`.

## Inputs you pass in

- **frame**: OHLCV with columns `o, h, l, c, vol` and a tz-aware index of bar open times. This is what `atlas_quant.load_candles` returns.
- **swings**: any objects with `ts` (the bar of the extreme), `price`, `kind` (`"high"`/`"low"`) and `confirmed_at` (the bar at which the swing became known). Map ATL-132's ZigZag output onto that, or build `SwingPoint(ts, price, kind, confirmed_at)`. Pass the real confirmation bar, not the extreme's bar. Otherwise a swing leaks into bars before it was knowable.
- **atr**: a float, the timeframe's Wilder ATR(14) at `at` (ATL-132 / `atlas_quant`). It must be > 0, otherwise `ValueError`.
- **at**: optional bar open time. Every function only sees bars `<= at` and swings with `confirmed_at <= at`, so you can pass the whole history. A hypothesis test pins this.

## Outputs (frozen pydantic models; call `.model_dump()` for the table)

```python
build_zones(frame, swings, atr, at=None, timeframe="1H") -> list[Zone]
# Zone(id="1H:z0", low, high, mid, touches, swing_highs, swing_lows, last_touch, bars_since_touch,
#      role="support"|"resistance", strength 0..1)   lowest zone first; id = TechnicalSetup.levels_used
prior_period_levels(frame, at=None) -> PeriodLevels(prior_day=PeriodHLC|None, prior_week=PeriodHLC|None)
round_numbers(price, asset_class) -> list[Decimal]   # [] unless stock/etf/index/commodity

relative_volume(frame, at=None) -> float | None
obv_trend(frame, at=None) -> ObvTrend(obv, slope, trend="up"|"down"|"flat"|None) | None
session_vwap(frame, at=None) -> float | None          # UTC day of `at`
anchored_vwap(frame, swings, at=None) -> AnchoredVwap(anchor_ts, anchor_kind, anchor_price, vwap) | None
volume_profile(frame, at=None) -> VolumeProfile(sessions, poc, vah, val, total_volume,
                                                value_area_volume, value_area_pct, rows[50]) | None

detect_patterns(frame, swings, atr, at=None) -> dict[name, PatternFlag]
# names: breakout_retest, failed_breakout, range, flag, double
# PatternFlag(name, detected, direction="long"|"short"|None, level, details={...})
# details: breakout_retest {breakout_ts, retest_ts, swing_ts}; failed_breakout {breakout_ts, failed_ts, swing_ts}
#          range {top, bottom, width_atr, top_touches, bottom_touches}
#          flag {shape: "flag"|"pennant", pole_move_atr, consolidation_bars, pole_start_ts, high_slope, low_slope}
#          double {kind: "top"|"bottom", first_ts, second_ts, first_price, second_price, confirmed}

summarise_book(bids, asks) -> BookSummary(best_bid, best_ask, mid, spread_bps, imbalance={10: D, 50: D})
walk_book(bids, asks, "buy"|"sell", notional_usdt, ct_val=inst.ctVal) -> BookFill(
    filled, filled_notional, base_qty, avg_price, levels_used, slippage_bps (vs mid), impact_bps (vs best))
```

The microstructure code is all `Decimal`. It takes OKX `books` rows as given (`[px, sz, "0", n]` strings, best first). It handles linear contracts only: notional = px × sz × ctVal. For the "estimated slippage at the Risk Engine's max size", pass the Risk Engine's max notional as `notional`. `slippage_bps` is the figure for `TechnicalSetup.est_slippage_bps`.

## The two-minute version

- Sessions are UTC days and weeks are ISO weeks. OKX `1D` candles roll at UTC+8. For prior-day levels, feed an intraday frame (1H), not `1D`.
- `detect_patterns` returns all five keys every time; `detected=False` means no flag. A range has no direction.
- Zones only cover swings from the last 120 bars of the frame. If you pass a 500-bar frame, the older swings are ignored on purpose.
- Not built here: the per-timeframe assembly (`ta.table`, ATL-132), loading books from Redis/REST (ATL-135), and inverse-contract notional.
