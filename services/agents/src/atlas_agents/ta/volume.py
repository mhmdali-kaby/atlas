"""Volume features: relative volume, OBV trend, VWAPs and the volume profile (brief §7.4, ATL-133).

Rules (numbers from ``agents.ta_structure.volume`` in config/defaults.yaml); every function looks at
the bars opened up to and including ``at`` only:

* **Relative volume**: volume of bar ``at`` / mean volume of the ``rel_volume_bars`` bars before it.
  Null with fewer bars or a zero mean.
* **OBV**: starts at 0 on the frame's first bar; each later bar adds its volume when its close is above
  the previous close, subtracts it when below, adds nothing when equal. Trend: the least-squares slope
  of OBV over the last ``obv_bars`` bars divided by those bars' mean volume (so 1.0 = one average
  bar's volume per bar); ``up`` above ``obv_flat_slope``, ``down`` below minus it, else ``flat``.
* **Session VWAP**: sum(typical x vol) / sum(vol) over the bars of the UTC day of bar ``at``, typical =
  (h + l + c) / 3.
* **Anchored VWAP**: the same sum from the bar of the newest swing confirmed by ``at`` (the "last major
  swing": the ZigZag only emits swings of at least 1.5 x ATR) through bar ``at``.
* **Volume profile**: the bars of the last ``profile_sessions`` UTC days that have bars (the day of
  ``at`` included). Rows are ``profile_bins`` equal price bins from the lowest low to the highest high.
  Each bar's volume is spread evenly over its own high-low range (a bar with high == low puts it all in
  the bin holding that price). POC = the row with the most volume (ties: the row nearest the middle
  row, then the lower one); ``poc`` is its mid-price. Value area: start at the POC row and repeatedly
  add the neighbouring row (above or below) with more volume (tie: above) until the rows hold at least
  ``value_area_pct`` of the volume. VAH = the top edge of the highest row in it, VAL = the bottom edge
  of the lowest.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_agents.ta._structure import Swing, cfg, cut, known_swings


class ObvTrend(BaseModel):
    model_config = ConfigDict(frozen=True)

    obv: float
    slope: float | None  # OBV slope per bar / mean bar volume
    trend: Literal["up", "down", "flat"] | None


class AnchoredVwap(BaseModel):
    model_config = ConfigDict(frozen=True)

    anchor_ts: datetime
    anchor_kind: Literal["high", "low"]
    anchor_price: float
    vwap: float


class ProfileRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    low: float
    high: float
    volume: float


class VolumeProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    sessions: list[datetime]  # UTC midnights of the sessions used
    poc: float
    vah: float
    val: float
    total_volume: float
    value_area_volume: float
    value_area_pct: float
    rows: list[ProfileRow]


def relative_volume(
    frame: pd.DataFrame, *, at: datetime | None = None, defaults: Mapping[str, Any] = DEFAULTS
) -> float | None:
    window = int(cfg("volume", defaults)["rel_volume_bars"])
    vol = cut(frame, at)["vol"].to_numpy(dtype="float64")
    if len(vol) < window + 1:
        return None
    mean = float(np.mean(vol[-window - 1 : -1]))
    return None if mean <= 0 else float(vol[-1]) / mean


def obv_series(frame: pd.DataFrame, *, at: datetime | None = None) -> pd.Series:
    bars = cut(frame, at)
    close = bars["c"].to_numpy(dtype="float64")
    vol = bars["vol"].to_numpy(dtype="float64")
    steps = np.zeros(len(close))
    if len(close) > 1:
        steps[1:] = np.sign(np.diff(close)) * vol[1:]
    return pd.Series(np.cumsum(steps), index=bars.index, name="obv")


def obv_trend(
    frame: pd.DataFrame, *, at: datetime | None = None, defaults: Mapping[str, Any] = DEFAULTS
) -> ObvTrend | None:
    config = cfg("volume", defaults)
    window, flat = int(config["obv_bars"]), float(config["obv_flat_slope"])
    obv = obv_series(frame, at=at)
    if obv.empty:
        return None
    value = float(obv.iloc[-1])
    if len(obv) < window:
        return ObvTrend(obv=value, slope=None, trend=None)
    tail = obv.to_numpy()[-window:]
    mean_vol = float(np.mean(cut(frame, at)["vol"].to_numpy(dtype="float64")[-window:]))
    if mean_vol <= 0:
        return ObvTrend(obv=value, slope=None, trend=None)
    raw = float(np.polyfit(np.arange(window, dtype="float64"), tail, 1)[0])
    slope = raw / mean_vol
    trend: Literal["up", "down", "flat"] = "up" if slope > flat else "down" if slope < -flat else "flat"
    return ObvTrend(obv=value, slope=slope, trend=trend)


def _vwap(bars: pd.DataFrame) -> float | None:
    vol = bars["vol"].to_numpy(dtype="float64")
    total = float(vol.sum())
    if total <= 0:
        return None
    typical = (bars["h"] + bars["l"] + bars["c"]).to_numpy(dtype="float64") / 3.0
    return float((typical * vol).sum()) / total


def session_vwap(frame: pd.DataFrame, *, at: datetime | None = None) -> float | None:
    bars = cut(frame, at)
    if bars.empty:
        return None
    day = pd.Timestamp(bars.index[-1]).normalize()
    return _vwap(bars.loc[bars.index >= day])


def anchored_vwap(
    frame: pd.DataFrame, swings: Iterable[Swing], *, at: datetime | None = None
) -> AnchoredVwap | None:
    bars = cut(frame, at)
    if bars.empty:
        return None
    known = known_swings(swings, pd.Timestamp(bars.index[-1]).to_pydatetime())
    if not known:
        return None
    anchor = known[-1]
    start = pd.Timestamp(anchor.ts)
    value = _vwap(bars.loc[bars.index >= start])
    if value is None:
        return None
    return AnchoredVwap(
        anchor_ts=start.to_pydatetime(),
        anchor_kind=anchor.kind,
        anchor_price=float(anchor.price),
        vwap=value,
    )


def _distribute(bars: pd.DataFrame, edges: np.ndarray) -> np.ndarray:
    bins = len(edges) - 1
    volume = np.zeros(bins)
    lows, highs = edges[:-1], edges[1:]
    for h, lo, v in zip(
        bars["h"].to_numpy(dtype="float64"),
        bars["l"].to_numpy(dtype="float64"),
        bars["vol"].to_numpy(dtype="float64"),
        strict=True,
    ):
        if v <= 0:
            continue
        if h <= lo:
            idx = int(np.clip(np.searchsorted(edges, lo, side="right") - 1, 0, bins - 1))
            volume[idx] += v
            continue
        overlap = np.clip(np.minimum(highs, h) - np.maximum(lows, lo), 0.0, None)
        volume += v * overlap / (h - lo)
    return volume


def volume_profile(
    frame: pd.DataFrame,
    *,
    at: datetime | None = None,
    sessions: int | None = None,
    bins: int | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> VolumeProfile | None:
    config = cfg("volume", defaults)
    n_sessions = int(sessions if sessions is not None else config["profile_sessions"])
    n_bins = int(bins if bins is not None else config["profile_bins"])
    target_pct = float(config["value_area_pct"])
    bars = cut(frame, at)
    if bars.empty:
        return None
    days = pd.DatetimeIndex(bars.index).normalize().unique()[-n_sessions:]
    bars = bars.loc[bars.index >= days[0]]
    lo, hi = float(bars["l"].min()), float(bars["h"].max())
    if hi <= lo:
        n_bins = 1
        hi = lo
    edges = np.linspace(lo, hi, n_bins + 1)
    rows = _distribute(bars, edges)
    total = float(rows.sum())
    if total <= 0:
        return None

    peak = float(rows.max())
    centre = (n_bins - 1) / 2.0
    candidates = [i for i in range(n_bins) if rows[i] == peak]
    poc_i = min(candidates, key=lambda i: (abs(i - centre), i))
    top = bottom = poc_i
    covered = float(rows[poc_i])
    goal = total * target_pct / 100.0
    while covered < goal and (top < n_bins - 1 or bottom > 0):
        up = float(rows[top + 1]) if top < n_bins - 1 else -1.0
        down = float(rows[bottom - 1]) if bottom > 0 else -1.0
        if up >= down:
            top += 1
            covered += up
        else:
            bottom -= 1
            covered += down
    return VolumeProfile(
        sessions=[pd.Timestamp(d).to_pydatetime() for d in days],
        poc=float((edges[poc_i] + edges[poc_i + 1]) / 2.0),
        vah=float(edges[top + 1]),
        val=float(edges[bottom]),
        total_volume=total,
        value_area_volume=covered,
        value_area_pct=covered / total * 100.0,
        rows=[
            ProfileRow(low=float(edges[i]), high=float(edges[i + 1]), volume=float(rows[i]))
            for i in range(n_bins)
        ],
    )
