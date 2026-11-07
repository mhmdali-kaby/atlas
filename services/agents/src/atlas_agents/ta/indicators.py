"""Per-bar trend, momentum and volatility indicators for one timeframe (ATL-132, brief §7.4).

:func:`indicator_frame` returns one row per input bar. Every value at bar t is computed from bars
0..t only, so appending bars never changes an earlier row (the hypothesis test in
``agents/tests/test_ta_no_lookahead.py`` pins this). Values are NaN (or None in object columns)
until defined. Parameters come from ``agents.ta`` in config/defaults.yaml.

EMA, RSI, ATR and ADX are :mod:`atlas_quant.indicators` (the definitions the backtester and
``compute_stats`` use); see that module for their exact seeding. The rest:

- ``ema<N>_slope_pct``: (EMA_t / EMA_{t-k} - 1) x 100 / k with k = ``slope_bars`` (% per bar).
- ``alignment``: sign(c - EMA20) + sign(EMA20 - EMA50) + sign(EMA50 - EMA200), -3..+3 (spans from
  ``ema_spans``, shortest first); NaN until every EMA is defined.
- MACD = EMA_fast - EMA_slow; signal = EMA_signal of MACD (seeded at the first MACD value);
  histogram = MACD - signal; ``macd_hist_slope`` = histogram_t - histogram_{t-1}.
- ``roc``: (c_t / c_{t-k} - 1) x 100.
- Bollinger: mid = SMA(window) of closes, band = mid ± std x population stdev (ddof 0);
  ``bb_width_pct`` = (upper - lower) / mid x 100; ``bb_width_percentile`` = share of the last
  ``bb_width_lookback`` widths that are <= the current one, x 100 (needs the full lookback);
  ``squeeze`` = percentile < ``squeeze_percentile``.
- Keltner: EMA(ema) ± mult x ATR(atr); ``keltner_breakout`` = "up" when c > upper, "down" when
  c < lower, else "none".
- Swings: :func:`atlas_agents.ta.swings.zigzag` with threshold ``zigzag_atr_mult`` x ATR(atr_window).
  ``swing_high_label`` / ``swing_low_label`` are the labels of the newest swing high / low confirmed
  by bar t.
- Divergence, from the two newest swings of a kind confirmed by bar t: ``bearish_divergence`` when the
  later high is higher (HH) but RSI at it is lower than at the earlier high; ``bullish_divergence``
  when the later low is lower (LL) but RSI at it is higher. The later pivot must be at most
  ``divergence_max_age_bars`` bars before t, and RSI defined at both pivots.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from atlas_quant.indicators import adx, ema, rsi, wilder_atr
from atlas_shared.defaults import DEFAULTS

from atlas_agents.ta.swings import Swing, zigzag

REQUIRED_COLUMNS = ("o", "h", "l", "c")


def ta_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["ta"]
    return cfg


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Sorted float copy with a UTC index; ValueError on missing columns or a naive index."""
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"frame lacks columns {missing}")
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        raise ValueError("frame index must be timezone-aware (UTC)")
    frame = df.sort_index().astype({col: "float64" for col in REQUIRED_COLUMNS})
    frame.index = pd.DatetimeIndex(frame.index).tz_convert("UTC")
    return frame


def _sign(x: pd.Series[float]) -> pd.Series[float]:
    return pd.Series(np.sign(x.to_numpy(dtype="float64")), index=x.index, dtype="float64")


def _rolling_percentile(x: pd.Series[float], lookback: int) -> pd.Series[float]:
    def share_le_last(window: np.ndarray[Any, np.dtype[np.float64]]) -> float:
        return float(np.mean(window <= window[-1]) * 100.0)

    return x.rolling(lookback, min_periods=lookback).apply(share_le_last, raw=True)


def _swing_columns(
    swings: list[Swing], rsi_values: np.ndarray[Any, np.dtype[np.float64]], n: int, max_age: int
) -> dict[str, list[Any]]:
    high_label: list[Any] = [None] * n
    low_label: list[Any] = [None] * n
    bull: list[Any] = [False] * n
    bear: list[Any] = [False] * n
    highs: list[Swing] = []
    lows: list[Swing] = []
    by_bar: dict[int, list[Swing]] = {}
    for s in swings:
        by_bar.setdefault(s.confirmed_pos, []).append(s)

    def diverges(pair: list[Swing], t: int, bullish: bool) -> bool:
        if len(pair) < 2:
            return False
        a, b = pair[-2], pair[-1]
        if t - b.pos > max_age:
            return False
        ra, rb = float(rsi_values[a.pos]), float(rsi_values[b.pos])
        if np.isnan(ra) or np.isnan(rb):
            return False
        if bullish:
            return b.price < a.price and rb > ra
        return b.price > a.price and rb < ra

    for t in range(n):
        for s in by_bar.get(t, []):
            (highs if s.kind == "high" else lows).append(s)
        high_label[t] = highs[-1].label if highs else None
        low_label[t] = lows[-1].label if lows else None
        bull[t] = diverges(lows, t, bullish=True)
        bear[t] = diverges(highs, t, bullish=False)
    return {
        "swing_high_label": high_label,
        "swing_low_label": low_label,
        "bullish_divergence": bull,
        "bearish_divergence": bear,
    }


def compute(df: pd.DataFrame, cfg: Mapping[str, Any] | None = None) -> tuple[pd.DataFrame, list[Swing]]:
    """The per-bar indicator frame and the confirmed swings of one prepared OHLC frame."""
    cfg = cfg if cfg is not None else ta_config()
    frame = prepare(df)
    c = frame["c"]
    out = pd.DataFrame(index=frame.index)
    out["close"] = c

    spans = sorted(int(s) for s in cfg["ema_spans"])
    k = int(cfg["slope_bars"])
    for span in spans:
        e = ema(c, span)
        out[f"ema{span}"] = e
        out[f"ema{span}_slope_pct"] = (e / e.shift(k) - 1.0) * 100.0 / k
    chain = [c] + [out[f"ema{span}"] for span in spans]
    alignment = sum((_sign(a - b) for a, b in itertools.pairwise(chain)), pd.Series(0.0, index=c.index))
    out["alignment"] = alignment.astype("float64")

    trend = adx(frame, int(cfg["adx_window"]))
    out["adx"] = trend.adx
    out["plus_di"] = trend.plus_di
    out["minus_di"] = trend.minus_di

    rsi_series = rsi(c, int(cfg["rsi_window"]))
    out["rsi"] = rsi_series
    m = cfg["macd"]
    macd = ema(c, int(m["fast"])) - ema(c, int(m["slow"]))
    signal = ema(macd, int(m["signal"]))
    out["macd"] = macd
    out["macd_signal"] = signal
    out["macd_hist"] = macd - signal
    out["macd_hist_slope"] = out["macd_hist"].diff()
    roc_k = int(cfg["roc_window"])
    out["roc"] = (c / c.shift(roc_k) - 1.0) * 100.0

    atr = wilder_atr(frame, int(cfg["atr_window"]))
    out["atr"] = atr
    out["atr_pct"] = atr / c * 100.0
    bb = cfg["bollinger"]
    bb_w = int(bb["window"])
    mid = c.rolling(bb_w, min_periods=bb_w).mean()
    dev = c.rolling(bb_w, min_periods=bb_w).std(ddof=0) * float(bb["std"])
    out["bb_middle"] = mid
    out["bb_upper"] = mid + dev
    out["bb_lower"] = mid - dev
    out["bb_width_pct"] = (2.0 * dev) / mid * 100.0
    out["bb_width_percentile"] = _rolling_percentile(out["bb_width_pct"], int(cfg["bb_width_lookback"]))
    pct = out["bb_width_percentile"]
    out["squeeze"] = pd.Series(
        [None if np.isnan(v) else bool(v < float(cfg["squeeze_percentile"])) for v in pct],
        index=c.index,
        dtype=object,
    )
    kc = cfg["keltner"]
    kc_mid = ema(c, int(kc["ema"]))
    kc_band = wilder_atr(frame, int(kc["atr"])) * float(kc["mult"])
    out["keltner_upper"] = kc_mid + kc_band
    out["keltner_lower"] = kc_mid - kc_band
    breakout: list[Any] = []
    for close, up, down in zip(c, out["keltner_upper"], out["keltner_lower"], strict=True):
        if np.isnan(up) or np.isnan(down):
            breakout.append(None)
        else:
            breakout.append("up" if close > up else "down" if close < down else "none")
    out["keltner_breakout"] = pd.Series(breakout, index=c.index, dtype=object)

    threshold = np.asarray(atr * float(cfg["zigzag_atr_mult"]), dtype=np.float64)
    swings = zigzag(frame, threshold)
    columns = _swing_columns(
        swings, np.asarray(rsi_series, dtype=np.float64), len(frame), int(cfg["divergence_max_age_bars"])
    )
    for name, values in columns.items():
        out[name] = pd.Series(values, index=c.index, dtype=object)
    return out, swings


def indicator_frame(df: pd.DataFrame, cfg: Mapping[str, Any] | None = None) -> pd.DataFrame:
    return compute(df, cfg)[0]


__all__ = ["REQUIRED_COLUMNS", "compute", "indicator_frame", "prepare", "ta_config"]
