# ATL-132 — TA precompute: trend, momentum, volatility per timeframe

Package: `services/agents/src/atlas_agents/ta/` (`indicators.py`, `swings.py`, `table.py`).
Config: `agents.ta` in `config/defaults.yaml` (regenerated into `atlas_shared.defaults` and
`backend/config/atlas_defaults.php`).

## Entry points

```python
from atlas_agents import ta

result = ta.table("BTC-USDT-SWAP", {"1D": df_1d, "4H": df_4h, "1H": df_1h, "15m": df_15m, "5m": df_5m})
frame, swings = ta.compute(df_1h)          # per-bar DataFrame + list[Swing]
frame = ta.indicator_frame(df_1h)          # per-bar DataFrame only
```

Input frames are what `atlas_quant.load_candles(...).frame` returns: **closed bars only**, index =
bar **open** time (tz-aware UTC), float columns `o, h, l, c` (`vol` is ignored here). A naive index or
missing column raises `ValueError`.

## `ta.table` output (real shape, one timeframe shown)

```python
{
  "inst_id": "BTC-USDT-SWAP",
  "data_as_of": datetime(2026, 10, 30, 12, 0, tzinfo=UTC),  # latest last-bar CLOSE time over all TFs
  "timeframes": {
    "1H": {
      "bar": "1H", "bars": 300,
      "as_of": datetime(2026, 10, 30, 12, 0, tzinfo=UTC),    # last bar open + bar length
      "last_close": 101.23,
      "trend": {
        "ema20": 100.9, "ema20_slope_pct": 0.04, "ema50": ..., "ema50_slope_pct": ...,
        "ema200": ..., "ema200_slope_pct": ...,
        "alignment": 2.0,              # -3..+3 (float)
        "adx": 23.1, "plus_di": 25.0, "minus_di": 18.2,
        "swing_high_label": "HH",      # newest confirmed swing high: HH|LH|EH|None
        "swing_low_label": "HL",       # newest confirmed swing low:  HL|LL|EL|None
        "swings": [                    # newest `swings_reported` (10), oldest first
          {"kind": "low", "ts": datetime(...), "price": 99.1, "label": "HL",
           "bar_index": 271, "confirmed_bar_index": 276},
        ],
      },
      "momentum": {"rsi": 58.2, "macd": 0.21, "macd_signal": 0.15, "macd_hist": 0.06,
                   "macd_hist_slope": 0.01, "roc": 1.4,
                   "bullish_divergence": False, "bearish_divergence": False},
      "volatility": {"atr": 0.82, "atr_pct": 0.81, "bb_upper": ..., "bb_middle": ..., "bb_lower": ...,
                     "bb_width_pct": 2.9, "bb_width_percentile": 41.7, "squeeze": False,
                     "keltner_upper": ..., "keltner_lower": ..., "keltner_breakout": "none"},  # up|down|none
      "note": None,
    },
    # every TF in agents.ta.timeframes is always present; a missing frame gives bars=0,
    # as_of=None, all values None, swings=[] and note="no frame given" ("no closed bars" if empty).
  },
}
```

Numbers are plain floats (NaN/inf → `None`); timestamps are `datetime`, so JSON-encode them in the
tool layer (ATL-135). A value is `None` while it is undefined (EMA200 needs 200 bars, the width
percentile needs 19 + 180 bars, ADX needs 28).

## Definitions worth knowing

- **No TA library.** Neither pandas-ta nor TA-Lib is in `services/uv.lock` (TA-Lib needs a C lib,
  pandas-ta is unmaintained on numpy 2). EMA, RSI, ATR and ADX are **atlas_quant's** existing
  definitions, now public in `atlas_quant.indicators` (moved out of `stats._wilder_atr`,
  `regime._adx`, `backtest._rsi`, which now call it; their results are unchanged and the quant suite
  passes). So an "RSI < 30" rule in `backtest_rule` and the TA table's RSI are the same number.
- Seeding differs from TA-Lib: EMA is `ewm(span, adjust=False)` seeded with the **first close**
  (not an SMA); RSI is `ewm(alpha=1/14)` seeded with the first change. They converge to TA-Lib's
  after ~100 bars; don't compare early bars against TradingView.
- ZigZag threshold = `1.5 × ATR(14)` **of the confirming bar**; a swing's `ts`/`price` is the pivot,
  `confirmed_bar_index` when the reversal reached the threshold. Swings never move once confirmed.
- Divergence uses the two newest confirmed swings of a kind and RSI at the pivot bars; the later pivot
  must be ≤ `divergence_max_age_bars` (60) bars old.
- Squeeze = Bollinger(20, 2σ population) width percentile among the last 180 widths < 10.
- Keltner = EMA20 ± 2 × ATR(10).

## Tests

`agents/tests/test_ta_golden.py` (golden file from the plain-Python reference
`agents/tests/fixtures/ta/make_golden.py`, 1e-8), `test_ta_signals.py` (ZigZag, divergence,
squeeze, Keltner), `test_ta_table.py` (all 5 TFs, data_as_of, hypothesis no-look-ahead).

## Not built here

Levels/zones, volume profile, VWAP, microstructure and pattern flags (ATL-133); loading candles and
the agent tool wrapper (ATL-135).
