"""Moves around scheduled events (ATL-82, brief §7.3 ``event_study``).

Definitions (pinned by ``quant/tests/test_event_study.py``):

- The price "at" a time x is the close of the last bar that had closed by x: the bar opening at
  ``slot(bar, x) - bar`` (the same closed-by rule as ``load_candles``: ``ts + bar <= x``). The bar still
  forming at x is never used, so nothing after x leaks in.
- For each event e and each offset k (every bar step from ``window[0]`` to ``window[1]``), the return is
  ``P(e + k) / P(e) - 1``. Negative offsets give the pre-event path relative to the event price.
- Per offset: n, mean, median, p10 and p90 (numpy linear interpolation) over every usable case. With a
  dict of frames (an asset class) every (instrument, event) pair is one case.
- ATR moves: for each horizon h in ``quant.event_study.atr_horizons_hours``, the mean of
  ``|P(e + h) - P(e)| / ATR(e)``, where ATR(e) is Wilder ATR(``quant.atr_window``) over the bars closed by
  e (the same ATR as ``compute_stats``). Horizons outside the window are skipped.
- A case is dropped when any bar its window needs is missing from the frame (``dropped_missing_bars``),
  or when the window runs off either end of the data (``dropped_out_of_range``), or when fewer than
  ``atr_window`` bars closed before the event (``dropped_short_history``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_quant.grid import BAR_SECONDS, bar_delta, bar_seconds, slot
from atlas_quant.stats import _wilder_atr

_SINGLE = "_single"


class OffsetStat(BaseModel):
    model_config = ConfigDict(frozen=True)

    offset_hours: float
    n: int
    mean: float | None  # fraction, P(e + k) / P(e) - 1
    median: float | None
    p10: float | None
    p90: float | None


class AtrMove(BaseModel):
    model_config = ConfigDict(frozen=True)

    horizon_hours: float
    n: int
    mean_abs_move_atr: float | None


class EventStudyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    bar: str
    window_hours: tuple[float, float]
    inst_ids: list[str]
    n_events: int  # events passed in
    n_cases: int  # usable (instrument, event) pairs
    dropped_missing_bars: int
    dropped_out_of_range: int
    dropped_short_history: int
    offsets: list[OffsetStat]
    atr_moves: list[AtrMove]


def _cfg() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["quant"]
    return cfg


def infer_bar(index: pd.DatetimeIndex) -> str:
    """The bar whose length equals the median spacing of ``index``."""
    if len(index) < 2:
        raise ValueError("cannot infer the bar from fewer than 2 rows; pass bar=")
    ns = index.to_numpy(dtype="datetime64[ns]").astype("int64")
    spacing = float(np.median(np.diff(ns))) / 1e9
    for name, seconds in BAR_SECONDS.items():
        if seconds == round(spacing):
            return name
    raise ValueError(f"median bar spacing {spacing}s matches no bar; pass bar=")


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError("frame index must be a timezone-aware DatetimeIndex")
    missing = [col for col in ("h", "l", "c") if col not in df.columns]
    if missing:
        raise ValueError(f"frame is missing columns {missing}")
    out = df.sort_index()
    out.index = pd.DatetimeIndex(out.index).tz_convert(UTC)
    return out


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("event timestamps must be timezone-aware")
    return ts.astimezone(UTC)


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype="float64"), q))


def event_study(
    prices: pd.DataFrame | Mapping[str, pd.DataFrame],
    events: Sequence[datetime],
    window: tuple[timedelta, timedelta] | None = None,
    *,
    bar: str | None = None,
) -> EventStudyResult:
    """Return distribution around ``events`` for one frame or a dict of frames (an asset class)."""
    cfg = _cfg()
    es: Mapping[str, Any] = cfg["event_study"]
    if window is None:
        lo_h, hi_h = (float(x) for x in es["window_hours"])
        window = (timedelta(hours=lo_h), timedelta(hours=hi_h))
    if window[0] > timedelta(0) or window[1] < timedelta(0):
        raise ValueError("window must contain the event time (start <= 0 <= end)")

    frames = {_SINGLE: prices} if isinstance(prices, pd.DataFrame) else dict(prices)
    prepared = {key: _prepare(df) for key, df in frames.items()}
    if bar is None:
        first = next(iter(prepared.values()))
        bar = infer_bar(pd.DatetimeIndex(first.index))
    bar_name: str = bar
    step = bar_delta(bar_name)
    step_s = bar_seconds(bar_name)

    def closed_bar(x: datetime) -> datetime:
        return slot(bar_name, x) - step

    k_lo = int(window[0].total_seconds() // step_s)
    k_hi = int(window[1].total_seconds() // step_s)
    offsets = [step * k for k in range(k_lo, k_hi + 1)]
    horizons = [
        timedelta(hours=float(h))
        for h in es["atr_horizons_hours"]
        if window[0] <= timedelta(hours=float(h)) <= window[1]
    ]
    atr_window = int(cfg["atr_window"])
    event_times = [_aware(e) for e in events]

    per_offset: list[list[float]] = [[] for _ in offsets]
    per_horizon: list[list[float]] = [[] for _ in horizons]
    dropped_missing = dropped_range = dropped_short = 0
    cases = 0

    for df in prepared.values():
        closes = df["c"]
        index = pd.DatetimeIndex(df.index)
        if index.empty:
            dropped_range += len(event_times)
            continue
        first_ts, last_ts = index[0].to_pydatetime(), index[-1].to_pydatetime()
        present = set(index.to_numpy(dtype="datetime64[ns]").astype("int64").tolist())

        for e in event_times:
            needed = [closed_bar(e + off) for off in offsets]
            needed += [closed_bar(e + h) for h in horizons]
            if min(needed) < first_ts or max(needed) > last_ts:
                dropped_range += 1
                continue
            if any(pd.Timestamp(ts).value not in present for ts in needed):
                dropped_missing += 1
                continue
            anchor_ts = closed_bar(e)
            history = df.loc[:anchor_ts]
            atr = _wilder_atr(history, atr_window)
            if atr is None or atr <= 0:
                dropped_short += 1
                continue
            p0 = float(closes.loc[anchor_ts])
            cases += 1
            for i, off in enumerate(offsets):
                per_offset[i].append(float(closes.loc[closed_bar(e + off)]) / p0 - 1.0)
            for j, h in enumerate(horizons):
                move = abs(float(closes.loc[closed_bar(e + h)]) - p0) / atr
                per_horizon[j].append(move)

    offset_stats = [
        OffsetStat(
            offset_hours=off.total_seconds() / 3600.0,
            n=len(vals),
            mean=float(np.mean(vals)) if vals else None,
            median=float(np.median(vals)) if vals else None,
            p10=_pct(vals, 10) if vals else None,
            p90=_pct(vals, 90) if vals else None,
        )
        for off, vals in zip(offsets, per_offset, strict=True)
    ]
    atr_moves = [
        AtrMove(
            horizon_hours=h.total_seconds() / 3600.0,
            n=len(vals),
            mean_abs_move_atr=float(np.mean(vals)) if vals else None,
        )
        for h, vals in zip(horizons, per_horizon, strict=True)
    ]
    return EventStudyResult(
        bar=bar,
        window_hours=(window[0].total_seconds() / 3600.0, window[1].total_seconds() / 3600.0),
        inst_ids=[k for k in prepared if k != _SINGLE],
        n_events=len(event_times),
        n_cases=cases,
        dropped_missing_bars=dropped_missing,
        dropped_out_of_range=dropped_range,
        dropped_short_history=dropped_short,
        offsets=offset_stats,
        atr_moves=atr_moves,
    )
