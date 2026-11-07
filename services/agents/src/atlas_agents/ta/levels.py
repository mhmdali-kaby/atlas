"""Support/resistance zones, prior-period levels and round numbers (brief §7.4 "Levels", ATL-133).

Rules (numbers from ``agents.ta_structure.levels`` in config/defaults.yaml):

* **Zones** (:func:`build_zones`). Take the swings confirmed by ``at`` whose extreme bar lies in the
  last ``lookback_bars`` bars up to ``at``. Sort them by price (then time) and sweep upwards: a swing
  joins the current zone while ``price - zone low <= merge_atr_mult x ATR``; otherwise it starts a new
  zone. So no zone is wider than ``merge_atr_mult x ATR`` and the result does not depend on the order
  the swings were passed in. Each zone carries

  - ``id``: ``"<timeframe>:z<n>"``, n counting zones from the lowest price up (``"z<n>"`` when no
    timeframe is given),
  - ``low`` / ``high`` / ``mid`` (mean swing price), ``touches`` (swings in it, highs and lows both
    count), ``last_touch`` (newest swing time) and ``bars_since_touch``,
  - ``role``: ``resistance`` when ``mid`` is above the close at ``at``, else ``support``,
  - ``strength`` in [0, 1]::

        strength = touch_weight x min(touches / touch_cap, 1)
                 + recency_weight x (1 - bars_since_touch / lookback_bars)

    with the recency term clipped to [0, 1] (weights 0.6 / 0.4, cap 4 by default: a zone touched 4+
    times on the latest bar scores 1.0).

* **Prior periods** (:func:`prior_period_levels`): high, low and close of the most recent UTC day and
  ISO week (Monday 00:00 UTC) before the one containing bar ``at``, from the bars opened in them.
  Null when the frame holds no bar of that period.

* **Round numbers** (:func:`round_numbers`): for ``round_asset_classes`` only (stock and commodity
  perps); step = 10^(floor(log10(price)) - 1), e.g. 100 for a 4,062 gold price and 10 for a 250 stock.
  ``round_count`` multiples of the step at or below the price and as many strictly above it.

ATR is passed in (the timeframe's Wilder ATR(14), ATL-132); swings are passed in too (ATL-132's ZigZag
or anything with ``ts, price, kind, confirmed_at``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_agents.ta._structure import (
    Swing,
    bar_position,
    cfg,
    cut,
    known_swings,
    positive_atr,
)


class Zone(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    low: float
    high: float
    mid: float
    touches: int
    swing_highs: int
    swing_lows: int
    last_touch: datetime
    bars_since_touch: int
    role: Literal["support", "resistance"]
    strength: float


class PeriodHLC(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime
    high: float
    low: float
    close: float


class PeriodLevels(BaseModel):
    model_config = ConfigDict(frozen=True)

    prior_day: PeriodHLC | None
    prior_week: PeriodHLC | None


def build_zones(
    frame: pd.DataFrame,
    swings: Iterable[Swing],
    atr: float,
    *,
    at: datetime | None = None,
    timeframe: str = "",
    defaults: Mapping[str, Any] = DEFAULTS,
) -> list[Zone]:
    """Zones from the swings of the last ``lookback_bars`` bars at bar ``at`` (lowest first)."""
    config = cfg("levels", defaults)
    lookback = int(config["lookback_bars"])
    merge = float(config["merge_atr_mult"]) * positive_atr(atr)
    touch_cap = int(config["touch_cap"])
    touch_w, recency_w = float(config["touch_weight"]), float(config["recency_weight"])

    bars = cut(frame, at)
    if bars.empty:
        return []
    index = pd.DatetimeIndex(bars.index)
    window_start = index[max(0, len(index) - lookback)]
    last_pos = len(index) - 1
    close = float(bars["c"].iloc[-1])
    cutoff = index[-1]
    candidates = [
        s
        for s in known_swings(swings, cutoff.to_pydatetime())
        if window_start <= pd.Timestamp(s.ts) <= cutoff
    ]
    candidates.sort(key=lambda s: (float(s.price), pd.Timestamp(s.ts)))

    groups: list[list[Swing]] = []
    for swing in candidates:
        if groups and float(swing.price) - float(groups[-1][0].price) <= merge:
            groups[-1].append(swing)
        else:
            groups.append([swing])

    prefix = f"{timeframe}:" if timeframe else ""
    zones: list[Zone] = []
    for n, group in enumerate(groups):
        prices = [float(s.price) for s in group]
        last_touch = max(pd.Timestamp(s.ts) for s in group)
        since = last_pos - bar_position(index, last_touch.to_pydatetime())
        recency = min(max(1.0 - since / lookback, 0.0), 1.0)
        strength = touch_w * min(len(group) / touch_cap, 1.0) + recency_w * recency
        mid = sum(prices) / len(prices)
        zones.append(
            Zone(
                id=f"{prefix}z{n}",
                low=min(prices),
                high=max(prices),
                mid=mid,
                touches=len(group),
                swing_highs=sum(1 for s in group if s.kind == "high"),
                swing_lows=sum(1 for s in group if s.kind == "low"),
                last_touch=last_touch.to_pydatetime(),
                bars_since_touch=since,
                role="resistance" if mid > close else "support",
                strength=min(max(strength, 0.0), 1.0),
            )
        )
    return zones


def _period(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> PeriodHLC | None:
    rows = bars.loc[(bars.index >= start) & (bars.index < end)]
    if rows.empty:
        return None
    return PeriodHLC(
        start=start.to_pydatetime(),
        high=float(rows["h"].max()),
        low=float(rows["l"].min()),
        close=float(rows["c"].iloc[-1]),
    )


def prior_period_levels(frame: pd.DataFrame, *, at: datetime | None = None) -> PeriodLevels:
    """Prior UTC day and ISO week high/low/close relative to the day/week of bar ``at``."""
    bars = cut(frame, at)
    if bars.empty:
        return PeriodLevels(prior_day=None, prior_week=None)
    current = pd.Timestamp(bars.index[-1])
    day = current.normalize()
    week = day - pd.Timedelta(days=int(day.weekday()))
    earlier_days = bars.loc[bars.index < day]
    prior_day = None
    if not earlier_days.empty:
        last_day = pd.Timestamp(earlier_days.index[-1]).normalize()
        prior_day = _period(bars, last_day, last_day + pd.Timedelta(days=1))
    earlier_weeks = bars.loc[bars.index < week]
    prior_week = None
    if not earlier_weeks.empty:
        last = pd.Timestamp(earlier_weeks.index[-1]).normalize()
        last_week = last - pd.Timedelta(days=int(last.weekday()))
        prior_week = _period(bars, last_week, last_week + pd.Timedelta(days=7))
    return PeriodLevels(prior_day=prior_day, prior_week=prior_week)


def round_numbers(
    price: float | Decimal,
    asset_class: str,
    *,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> list[Decimal]:
    """Round-number levels around ``price`` for stock/commodity perps, ascending; [] otherwise."""
    config = cfg("levels", defaults)
    if asset_class not in set(config["round_asset_classes"]):
        return []
    value = Decimal(str(price))
    if value <= 0:
        return []
    count = int(config["round_count"])
    step = Decimal(10) ** (value.adjusted() - 1)
    base = (value / step).to_integral_value(rounding="ROUND_FLOOR") * step
    below = [base - step * k for k in range(count)]
    above = [base + step * k for k in range(1, count + 1)]
    return sorted(level for level in below + above if level > 0)
