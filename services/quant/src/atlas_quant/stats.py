"""Descriptive statistics of one candle series (ATL-79, brief §7.3 ``compute_stats``).

Definitions (every one is pinned by the golden test in ``quant/tests/test_stats_golden.py``):

- Returns are close to close, attributed to the bar that ends them: ``r_t = c_t / c_{t-1} - 1`` and
  ``l_t = ln(c_t / c_{t-1})`` for t = 1..n-1, so a frame of n bars has n-1 returns.
- Standard deviations use ddof = 1; skew and excess kurtosis are the bias-adjusted sample estimators
  (Fisher-Pearson G1 and G2, the pandas convention) of the log returns.
- Annualisation: ``P = days_per_year[asset_class] * 86400 / bar_seconds`` bars a year
  (``quant.days_per_year``: 365 crypto, 252 stock/etf/index/commodity), volatility * sqrt(P).
- Realised vol = std(l) * sqrt(P). Parkinson = sqrt(mean(ln(h/l)²) / (4 ln 2)) * sqrt(P).
  Garman-Klass = sqrt(mean(½ ln(h/l)² - (2 ln 2 - 1) ln(c/o)²)) * sqrt(P). Both use all n bars.
- ATR is Wilder's: TR_0 = h_0 - l_0, TR_t = max(h-l, |h - c_{t-1}|, |l - c_{t-1}|); the first ATR is
  the mean of TR_0..TR_{w-1}, then ATR_t = (ATR_{t-1} (w - 1) + TR_t) / w. The last value is reported.
- Max drawdown is on closes: min over t of c_t / max(c_0..c_t) - 1 (a fraction ≤ 0), with the peak
  bar (the latest running high before the trough) and the trough bar (the first bar at the minimum).
- Average daily range % is the mean over complete UTC days (every grid slot of the day present) of
  (max h - min l) / (first o of the day) * 100.
- Hour tables group log returns by the hour of the ending bar's open time, in UTC and in
  ``quant.hour_table_timezone`` (Asia/Baghdad); the weekday table groups simple returns by the UTC
  weekday (0 = Monday) of the ending bar.

Inputs are float64; statistics are analysis, not money.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

import numpy as np
import pandas as pd
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_quant.grid import bar_seconds, slot
from atlas_quant.indicators import wilder_atr_values

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_REQUIRED = ("o", "h", "l", "c")


class ReturnsSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    count: int
    mean: float | None
    std: float | None
    min: float | None
    max: float | None
    # Whole-period return: c_last / c_first - 1 (simple) or ln(c_last / c_first) (log).
    total: float | None


class DrawdownSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_drawdown: float  # fraction, ≤ 0; 0 when closes never fell below a running high
    peak_ts: datetime | None
    trough_ts: datetime | None


class HourBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    hour: int
    n: int
    mean_log_return: float
    std_log_return: float | None  # None when n < 2


class WeekdayBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    weekday: int  # 0 = Monday
    name: str
    n: int
    mean_return: float  # mean simple return


class StatsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    bar: str
    asset_class: str
    periods_per_year: float
    n: int
    period_start: datetime | None
    period_end: datetime | None
    log_returns: ReturnsSummary
    simple_returns: ReturnsSummary
    realised_vol: float | None
    parkinson_vol: float | None
    garman_klass_vol: float | None
    atr_window: int
    atr: float | None
    atr_pct: float | None  # ATR / last close * 100
    skew: float | None
    excess_kurtosis: float | None
    drawdown: DrawdownSummary
    adr_pct: float | None
    adr_days: int
    hour_timezone: str
    hourly_utc: list[HourBucket]
    hourly_local: list[HourBucket]
    weekday: list[WeekdayBucket]


def _quant() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["quant"]
    return cfg


def periods_per_year(bar: str, asset_class: str | None = None) -> float:
    """Bars per year for annualising: days_per_year[asset_class] * 86400 / bar_seconds."""
    cfg = _quant()
    days: Mapping[str, int] = cfg["days_per_year"]
    cls = asset_class or str(cfg["default_asset_class"])
    return float(days.get(cls, days["other"])) * 86400.0 / float(bar_seconds(bar))


def _opt(x: float | np.floating[Any]) -> float | None:
    v = float(x)
    return None if math.isnan(v) or math.isinf(v) else v


def _summary(r: pd.Series[float], total: float | None) -> ReturnsSummary:
    if r.empty:
        return ReturnsSummary(count=0, mean=None, std=None, min=None, max=None, total=None)
    return ReturnsSummary(
        count=len(r),
        mean=_opt(r.mean()),
        std=_opt(r.std(ddof=1)) if len(r) > 1 else None,
        min=_opt(r.min()),
        max=_opt(r.max()),
        total=total,
    )


def _wilder_atr(df: pd.DataFrame, window: int) -> float | None:
    if len(df) < window:
        return None
    return float(wilder_atr_values(df, window)[-1])


def _drawdown(c: pd.Series[float]) -> DrawdownSummary:
    if c.empty:
        return DrawdownSummary(max_drawdown=0.0, peak_ts=None, trough_ts=None)
    running = c.cummax()
    dd = c / running - 1.0
    trough_pos = int(np.argmin(dd.to_numpy()))
    worst = float(dd.iloc[trough_pos])
    if worst >= 0.0:
        return DrawdownSummary(max_drawdown=0.0, peak_ts=None, trough_ts=None)
    upto = c.iloc[: trough_pos + 1].to_numpy()
    peak_value = float(running.iloc[trough_pos])
    peak_pos = int(np.flatnonzero(upto == peak_value)[-1])
    index = c.index
    return DrawdownSummary(
        max_drawdown=worst,
        peak_ts=index[peak_pos].to_pydatetime(),
        trough_ts=index[trough_pos].to_pydatetime(),
    )


def _adr(df: pd.DataFrame, bar: str) -> tuple[float | None, int]:
    per_day = 86400 // bar_seconds(bar)
    if per_day < 1:
        return None, 0
    days = df.groupby(pd.DatetimeIndex(df.index).floor("D"))
    ranges: list[float] = []
    for _, day in days:
        if per_day > 1:
            slots = {slot(bar, ts.to_pydatetime()) for ts in day.index}
            if len(slots) < per_day:
                continue
        ranges.append(float((day["h"].max() - day["l"].min()) / day["o"].iloc[0] * 100.0))
    if not ranges:
        return None, 0
    return float(np.mean(ranges)), len(ranges)


def _hour_table(log_r: pd.Series[float], tz: str | None) -> list[HourBucket]:
    idx = pd.DatetimeIndex(log_r.index)
    hours = (idx if tz is None else idx.tz_convert(tz)).hour
    out: list[HourBucket] = []
    for hour in sorted(set(hours.tolist())):
        group = log_r[np.asarray(hours == hour)]
        n = len(group)
        out.append(
            HourBucket(
                hour=int(hour),
                n=n,
                mean_log_return=float(group.mean()),
                std_log_return=_opt(group.std(ddof=1)) if n > 1 else None,
            )
        )
    return out


def _weekday_table(simple_r: pd.Series[float]) -> list[WeekdayBucket]:
    days = pd.DatetimeIndex(simple_r.index).day_of_week
    out: list[WeekdayBucket] = []
    for d in sorted(set(days.tolist())):
        group = simple_r[np.asarray(days == d)]
        out.append(
            WeekdayBucket(
                weekday=int(d), name=WEEKDAYS[int(d)], n=len(group), mean_return=float(group.mean())
            )
        )
    return out


def compute_stats(df: pd.DataFrame, bar: str, asset_class: str | None = None) -> StatsResult:
    """Statistics of a frame shaped like ``load_candles``'s (UTC ``ts`` index, float o/h/l/c columns).

    ``asset_class`` (crypto, stock, etf, index, commodity, other) picks the days per year used to
    annualise; it defaults to ``quant.default_asset_class``.
    """
    missing = [col for col in _REQUIRED if col not in df.columns]
    if missing:
        raise ValueError(f"frame lacks columns {missing}")
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        raise ValueError("frame index must be timezone-aware (UTC)")
    frame = df.sort_index()
    frame.index = pd.DatetimeIndex(frame.index).tz_convert("UTC")

    cfg = _quant()
    cls = asset_class or str(cfg["default_asset_class"])
    ppy = periods_per_year(bar, cls)
    ann = math.sqrt(ppy)
    window = int(cfg["atr_window"])
    tz = str(cfg["hour_table_timezone"])
    n = len(frame)

    o = frame["o"].astype("float64")
    h = frame["h"].astype("float64")
    lo = frame["l"].astype("float64")
    c = frame["c"].astype("float64")

    simple_r = (c / c.shift(1) - 1.0).iloc[1:]
    log_r = pd.Series(np.log((c / c.shift(1)).to_numpy()), index=c.index, dtype="float64").iloc[1:]

    total_simple = float(c.iloc[-1] / c.iloc[0] - 1.0) if n > 1 else None
    total_log = float(math.log(c.iloc[-1] / c.iloc[0])) if n > 1 else None

    hl2 = np.log(h / lo) ** 2
    co2 = np.log(c / o) ** 2
    parkinson = math.sqrt(float(hl2.mean()) / (4.0 * math.log(2.0))) * ann if n > 0 else None
    gk_var = float((0.5 * hl2 - (2.0 * math.log(2.0) - 1.0) * co2).mean()) if n > 0 else float("nan")
    garman_klass = math.sqrt(gk_var) * ann if gk_var >= 0 else None

    atr = _wilder_atr(frame, window)
    adr, adr_days = _adr(frame, bar)

    return StatsResult(
        bar=bar,
        asset_class=cls,
        periods_per_year=ppy,
        n=n,
        period_start=frame.index[0].to_pydatetime() if n else None,
        period_end=frame.index[-1].to_pydatetime() if n else None,
        log_returns=_summary(log_r, total_log),
        simple_returns=_summary(simple_r, total_simple),
        realised_vol=float(log_r.std(ddof=1)) * ann if len(log_r) > 1 else None,
        parkinson_vol=parkinson,
        garman_klass_vol=garman_klass,
        atr_window=window,
        atr=atr,
        atr_pct=atr / float(c.iloc[-1]) * 100.0 if atr is not None else None,
        skew=_opt(cast(float, log_r.skew())) if len(log_r) > 2 else None,
        excess_kurtosis=_opt(cast(float, log_r.kurt())) if len(log_r) > 3 else None,
        drawdown=_drawdown(c),
        adr_pct=adr,
        adr_days=adr_days,
        hour_timezone=tz,
        hourly_utc=_hour_table(log_r, None),
        hourly_local=_hour_table(log_r, tz),
        weekday=_weekday_table(simple_r),
    )


__all__ = [
    "WEEKDAYS",
    "DrawdownSummary",
    "HourBucket",
    "ReturnsSummary",
    "StatsResult",
    "WeekdayBucket",
    "compute_stats",
    "periods_per_year",
]
