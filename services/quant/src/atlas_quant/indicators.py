"""Per-bar indicator series shared by the statistics, the backtester and the TA precompute (ATL-132).

These are the indicator definitions atlas_quant already used inside ``compute_stats`` (ATR),
``detect_regime`` (ADX) and ``backtest_rule`` (EMA, RSI), exposed as whole series so callers that
need every bar (``atlas_agents.ta``) do not re-implement them. Each value at bar t depends only on
bars 0..t (no look-ahead), and is NaN until it is defined.

- EMA(span): ``pandas.ewm(span, adjust=False)`` seeded with the first value, NaN for the first
  ``span - 1`` bars.
- RSI(w): ``100 - 100 / (1 + G / L)`` where G and L are ``ewm(alpha=1/w, adjust=False)`` means of
  the close-to-close gains and losses, seeded with the first change, NaN until w changes exist.
- ATR(w), Wilder: TR_0 = h_0 - l_0, TR_t = max(h-l, |h - c_{t-1}|, |l - c_{t-1}|); the first ATR (bar
  w - 1) is the mean of TR_0..TR_{w-1}, then ATR_t = (ATR_{t-1} (w - 1) + TR_t) / w.
- ADX(w), Wilder: +DM/-DM and TR from bar 1, each smoothed with a Wilder running mean seeded by the
  mean of bars 1..w (defined at bar w); DX = 100 |+DI - -DI| / (+DI + -DI); ADX seeded by the mean of
  DX over bars w..2w-1 (defined at bar 2w - 1), then Wilder-smoothed.
"""

from __future__ import annotations

import math
from typing import NamedTuple, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray


def ema(c: pd.Series[float], span: int) -> pd.Series[float]:
    return c.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(c: pd.Series[float], window: int) -> pd.Series[float]:
    delta = c.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = gain / loss
    return cast("pd.Series[float]", 100.0 - 100.0 / (1.0 + rs))


def true_range(df: pd.DataFrame) -> NDArray[np.float64]:
    h = df["h"].to_numpy(dtype="float64")
    lo = df["l"].to_numpy(dtype="float64")
    c = df["c"].to_numpy(dtype="float64")
    prev = np.concatenate(([np.nan], c[:-1]))
    tr: NDArray[np.float64] = np.fmax(h - lo, np.fmax(np.abs(h - prev), np.abs(lo - prev)))
    return tr


def wilder_atr_values(df: pd.DataFrame, window: int) -> NDArray[np.float64]:
    """Wilder ATR per bar as an array (NaN before bar ``window - 1``)."""
    tr = true_range(df)
    out = np.full(len(tr), np.nan)
    if len(tr) < window:
        return out
    atr = float(np.mean(tr[:window]))
    out[window - 1] = atr
    for i in range(window, len(tr)):
        atr = (atr * (window - 1) + float(tr[i])) / window
        out[i] = atr
    return out


def wilder_atr(df: pd.DataFrame, window: int) -> pd.Series[float]:
    return pd.Series(wilder_atr_values(df, window), index=df.index, dtype="float64")


def wilder_smooth(x: NDArray[np.float64], window: int) -> NDArray[np.float64]:
    """Wilder's running mean of x[1:] (x[0] is undefined): seed = mean of the first ``window``."""
    out = np.full(len(x), np.nan)
    if len(x) < window + 1:
        return out
    value = float(np.mean(x[1 : window + 1]))
    out[window] = value
    for i in range(window + 1, len(x)):
        value = (value * (window - 1) + float(x[i])) / window
        out[i] = value
    return out


class Adx(NamedTuple):
    adx: pd.Series[float]
    plus_di: pd.Series[float]
    minus_di: pd.Series[float]


def adx(frame: pd.DataFrame, window: int) -> Adx:
    """Wilder ADX, +DI and -DI per bar; ADX is NaN until bar 2 x window - 1."""
    h = frame["h"].to_numpy(dtype="float64")
    lo = frame["l"].to_numpy(dtype="float64")
    c = frame["c"].to_numpy(dtype="float64")
    n = len(frame)
    up = np.zeros(n)
    down = np.zeros(n)
    tr = np.zeros(n)
    up[1:] = h[1:] - h[:-1]
    down[1:] = lo[:-1] - lo[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr[1:] = np.fmax(h[1:] - lo[1:], np.fmax(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    s_tr = wilder_smooth(tr, window)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * wilder_smooth(plus_dm, window) / s_tr
        minus_di = 100.0 * wilder_smooth(minus_dm, window) / s_tr
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    out = np.full(n, np.nan)
    first = window  # first defined dx
    if n >= first + window:
        value = float(np.nanmean(dx[first : first + window]))
        out[first + window - 1] = value
        for i in range(first + window, n):
            d = float(dx[i])
            if not math.isnan(d):
                value = (value * (window - 1) + d) / window
            out[i] = value
    index = frame.index
    return Adx(
        pd.Series(out, index=index, dtype="float64"),
        pd.Series(plus_di, index=index, dtype="float64"),
        pd.Series(minus_di, index=index, dtype="float64"),
    )


__all__ = [
    "Adx",
    "adx",
    "ema",
    "rsi",
    "true_range",
    "wilder_atr",
    "wilder_atr_values",
    "wilder_smooth",
]
