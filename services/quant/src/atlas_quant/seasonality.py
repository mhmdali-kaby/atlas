"""Calendar seasonality of returns (ATL-81, brief §7.3 ``seasonality``).

Definitions (pinned by the golden test in ``quant/tests/test_seasonality_golden.py``):

- Returns are close-to-close log returns ``ln(c_t / c_{t-1})`` attributed to the bar that ends them
  (the same convention as ``compute_stats``'s hour tables), so n bars give n-1 returns.
- ``by="hour"``: the ending bar's open hour, in UTC (``buckets``) and in ``quant.hour_table_timezone``
  (Asia/Baghdad, UTC+3; ``local_buckets``). ``by="weekday"``: the UTC weekday, 0 = Monday.
  ``by="month"``: the UTC month, 1..12.
- ``by="funding"``: the offset of the bar's open time from the nearest settlement hour in
  ``quant.seasonality.funding_hours_utc`` (00/08/16 UTC), floored to whole hours. Offsets in
  ``[-w, w)`` hours (``w = funding_window_hours``) are buckets ``-w .. w-1``: bucket 0 is the hour
  that opens at settlement, -1 the hour before it. Every other bar is in the ``outside`` bucket
  (key ``None``).
- Per bucket: n, mean, median, and t-stat = mean / (std / sqrt(n)) with std ddof 1; the t-stat is
  ``None`` when n < 2 or std = 0.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import numpy as np
import pandas as pd
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_quant.stats import WEEKDAYS

SeasonBy = Literal["hour", "weekday", "month", "funding"]
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class SeasonBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: int | None  # hour 0-23, weekday 0-6, month 1-12, funding offset hour; None = outside
    label: str
    n: int
    mean_log_return: float
    median_log_return: float
    t_stat: float | None


class SeasonalityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    by: SeasonBy
    n: int  # returns in the sample
    timezone: str  # UTC, except local_buckets for by="hour"
    local_timezone: str | None
    buckets: list[SeasonBucket]
    local_buckets: list[SeasonBucket] | None  # by="hour" only


def _bucket(key: int | None, label: str, group: pd.Series[float]) -> SeasonBucket:
    n = len(group)
    values = group.to_numpy(dtype="float64")
    mean = float(values.mean())
    t_stat: float | None = None
    if n > 1:
        sd = float(values.std(ddof=1))
        if sd > 0:
            t_stat = mean / (sd / math.sqrt(n))
    return SeasonBucket(
        key=key, label=label, n=n, mean_log_return=mean, median_log_return=float(np.median(values)),
        t_stat=t_stat,
    )  # fmt: skip


def _table(log_r: pd.Series[float], keys: Sequence[int], label: Any) -> list[SeasonBucket]:
    arr = np.asarray(keys)
    return [_bucket(int(key), label(int(key)), log_r[arr == key]) for key in sorted(set(arr.tolist()))]


def _funding_offsets(idx: pd.DatetimeIndex, hours: Sequence[int]) -> list[int]:
    minutes = (idx.hour * 60 + idx.minute).to_numpy()
    best = np.full(len(minutes), 10_000)
    for h in hours:
        off = (minutes - int(h) * 60 + 720) % 1440 - 720  # signed, in [-720, 720)
        best = np.where(np.abs(off) < np.abs(best), off, best)
    return [int(x) for x in best]


def seasonality(df: pd.DataFrame, by: SeasonBy) -> SeasonalityResult:
    """Return seasonality of a frame shaped like ``load_candles``'s (UTC index, float ``c``)."""
    if "c" not in df.columns:
        raise ValueError("frame lacks column 'c'")
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        raise ValueError("frame index must be timezone-aware (UTC)")
    c = pd.Series(df["c"].to_numpy(dtype="float64"), index=index.tz_convert("UTC")).sort_index()
    log_r = pd.Series(np.log((c / c.shift(1)).to_numpy()), index=c.index, dtype="float64").iloc[1:]
    idx = pd.DatetimeIndex(log_r.index)
    quant: Mapping[str, Any] = DEFAULTS["quant"]
    local_tz = str(quant["hour_table_timezone"])

    local: list[SeasonBucket] | None = None
    if by == "hour":
        buckets = _table(log_r, idx.hour.tolist(), lambda h: f"{h:02d}:00")
        local = _table(log_r, idx.tz_convert(local_tz).hour.tolist(), lambda h: f"{h:02d}:00")
    elif by == "weekday":
        buckets = _table(log_r, idx.day_of_week.tolist(), lambda d: WEEKDAYS[d])
    elif by == "month":
        buckets = _table(log_r, idx.month.tolist(), lambda m: MONTHS[m - 1])
    elif by == "funding":
        cfg: Mapping[str, Any] = quant["seasonality"]
        w = int(cfg["funding_window_hours"])
        offsets = _funding_offsets(idx, [int(h) for h in cfg["funding_hours_utc"]])
        hour_off = np.asarray([math.floor(o / 60) for o in offsets])
        inside = (hour_off >= -w) & (hour_off < w)
        buckets = []
        for key in range(-w, w):
            group = log_r[inside & (hour_off == key)]
            if len(group):
                buckets.append(_bucket(key, f"{key:+d}h", group))
        if (~inside).any():
            buckets.append(_bucket(None, "outside", log_r[~inside]))
    else:
        raise ValueError(f"unknown seasonality grouping {by!r}")

    return SeasonalityResult(
        by=by,
        n=len(log_r),
        timezone="UTC",
        local_timezone=local_tz if by == "hour" else None,
        buckets=buckets,
        local_buckets=local,
    )


__all__ = ["MONTHS", "SeasonBucket", "SeasonBy", "SeasonalityResult", "seasonality"]
