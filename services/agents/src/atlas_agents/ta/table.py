"""The Technical Analyst's per-timeframe indicator table (ATL-132, brief §7.4).

:func:`table` takes closed-bar OHLC(V) frames per timeframe (indexed by bar open time, UTC, as
:func:`atlas_quant.load_candles` returns them) and reports the last bar of each as three groups,
``trend``, ``momentum`` and ``volatility``, plus the newest confirmed swings. Every timeframe in
``agents.ta.timeframes`` is present: one with no frame, or no bars, has ``bars = 0``, every value
None and a ``note``. A bar's close time is its open time + the bar length; ``as_of`` is that of the
timeframe's last bar and ``data_as_of`` the latest of them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pandas as pd
from atlas_quant.grid import bar_delta

from atlas_agents.ta.indicators import compute, ta_config

TREND_KEYS: tuple[str, ...] = (
    "alignment",
    "adx",
    "plus_di",
    "minus_di",
    "swing_high_label",
    "swing_low_label",
)
MOMENTUM_KEYS: tuple[str, ...] = (
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "macd_hist_slope",
    "roc",
    "bullish_divergence",
    "bearish_divergence",
)
VOLATILITY_KEYS: tuple[str, ...] = (
    "atr",
    "atr_pct",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_width_pct",
    "bb_width_percentile",
    "squeeze",
    "keltner_upper",
    "keltner_lower",
    "keltner_breakout",
)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    return value


def _trend_keys(cfg: Mapping[str, Any]) -> list[str]:
    spans = sorted(int(s) for s in cfg["ema_spans"])
    return [k for span in spans for k in (f"ema{span}", f"ema{span}_slope_pct")] + list(TREND_KEYS)


def _empty(bar: str, cfg: Mapping[str, Any], note: str) -> dict[str, Any]:
    return {
        "bar": bar,
        "bars": 0,
        "as_of": None,
        "last_close": None,
        "trend": {k: None for k in _trend_keys(cfg)} | {"swings": []},
        "momentum": {k: None for k in MOMENTUM_KEYS},
        "volatility": {k: None for k in VOLATILITY_KEYS},
        "note": note,
    }


def timeframe_table(bar: str, df: pd.DataFrame, cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else ta_config()
    if df.empty:
        return _empty(bar, cfg, "no closed bars")
    frame, swings = compute(df, cfg)
    last = frame.iloc[-1]
    as_of: datetime = (pd.Timestamp(frame.index[-1]) + bar_delta(bar)).to_pydatetime()
    reported = int(cfg["swings_reported"])
    trend = {k: _clean(last[k]) for k in _trend_keys(cfg)}
    trend["swings"] = [s.as_dict() for s in swings[-reported:]] if reported > 0 else []
    return {
        "bar": bar,
        "bars": len(frame),
        "as_of": as_of,
        "last_close": _clean(last["close"]),
        "trend": trend,
        "momentum": {k: _clean(last[k]) for k in MOMENTUM_KEYS},
        "volatility": {k: _clean(last[k]) for k in VOLATILITY_KEYS},
        "note": None,
    }


def table(
    inst_id: str, frames_by_tf: Mapping[str, pd.DataFrame], cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """``{inst_id, data_as_of, timeframes: {bar: {...}}}`` for every configured timeframe."""
    cfg = cfg if cfg is not None else ta_config()
    timeframes: dict[str, dict[str, Any]] = {}
    for bar in cfg["timeframes"]:
        df = frames_by_tf.get(bar)
        timeframes[bar] = (
            timeframe_table(bar, df, cfg) if df is not None else _empty(bar, cfg, "no frame given")
        )
    closes = [t["as_of"] for t in timeframes.values() if t["as_of"] is not None]
    return {"inst_id": inst_id, "data_as_of": max(closes) if closes else None, "timeframes": timeframes}


__all__ = ["MOMENTUM_KEYS", "TREND_KEYS", "VOLATILITY_KEYS", "table", "timeframe_table"]
