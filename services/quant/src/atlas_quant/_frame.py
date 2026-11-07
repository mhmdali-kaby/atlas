"""Shared input handling for the ATL-80 statistics: validate a candle frame and infer its bar."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any

import pandas as pd
from atlas_shared.defaults import DEFAULTS


def quant_cfg(section: str) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["quant"][section]
    return cfg


def prepare(df: pd.DataFrame, required: Iterable[str]) -> pd.DataFrame:
    """Sorted copy of ``df`` with a UTC index; raises ValueError for missing columns or a naive index."""
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"frame lacks columns {missing}")
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        raise ValueError("frame index must be timezone-aware (UTC)")
    frame = df.sort_index()
    frame.index = pd.DatetimeIndex(frame.index).tz_convert("UTC")
    return frame


def bar_spacing(index: pd.DatetimeIndex) -> timedelta:
    """The bar length, as the median spacing of the index (robust to the odd gap)."""
    if len(index) < 2:
        raise ValueError("need at least 2 bars to infer the bar length")
    diffs = pd.Series(index[1:] - index[:-1])
    spacing = pd.Timedelta(diffs.median()).to_pytimedelta()
    if spacing <= timedelta(0):
        raise ValueError("index must have increasing timestamps")
    return spacing
