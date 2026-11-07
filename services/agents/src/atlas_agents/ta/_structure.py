"""Shared input handling for the ATL-133 structure precompute (levels, volume, patterns).

Every function evaluates "at bar t": it sees only the bars whose open time is ``<= at`` (bar t itself
counts as closed) and only the swings whose ``confirmed_at`` is ``<= at``. Callers may pass the whole
history; :func:`cut` and :func:`known_swings` drop everything later, which is what keeps the results
free of look-ahead (pinned by the hypothesis property test).

A frame is OHLCV with columns ``o, h, l, c, vol`` and a timezone-aware index of bar open times, the
shape :func:`atlas_quant.load_candles` returns.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

import pandas as pd
from atlas_shared.defaults import DEFAULTS

SwingKind = Literal["high", "low"]


class Swing(Protocol):
    """What the structure functions need from a swing point (ATL-132's ZigZag output fits it)."""

    @property
    def ts(self) -> datetime: ...  # the bar that made the extreme

    @property
    def price(self) -> float: ...

    @property
    def kind(self) -> SwingKind: ...

    @property
    def confirmed_at(self) -> datetime: ...  # the bar at which the swing became known


@dataclass(frozen=True)
class SwingPoint:
    ts: datetime
    price: float
    kind: SwingKind
    confirmed_at: datetime


def cfg(section: str, defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["ta_structure"][section]
    return config


def prepare(frame: pd.DataFrame, columns: Iterable[str] = ("o", "h", "l", "c", "vol")) -> pd.DataFrame:
    missing = [col for col in columns if col not in frame.columns]
    if missing:
        raise ValueError(f"frame lacks columns {missing}")
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        raise ValueError("frame index must be timezone-aware (UTC)")
    out = frame.sort_index()
    out.index = pd.DatetimeIndex(out.index).tz_convert("UTC")
    return out


def cut(frame: pd.DataFrame, at: datetime | None) -> pd.DataFrame:
    """The prepared frame up to and including the bar opened at ``at`` (all bars when None)."""
    out = prepare(frame)
    if at is None:
        return out
    return out.loc[out.index <= pd.Timestamp(at)]


def known_swings(swings: Iterable[Swing], at: datetime | None) -> list[Swing]:
    """Swings confirmed by ``at``, oldest first (by extreme time, then price)."""
    kept = [s for s in swings if at is None or pd.Timestamp(s.confirmed_at) <= pd.Timestamp(at)]
    return sorted(kept, key=lambda s: (pd.Timestamp(s.ts), s.price))


def bar_position(index: pd.DatetimeIndex, ts: datetime) -> int:
    """Position of the last bar opened at or before ``ts`` (-1 when before the first bar)."""
    return int(index.searchsorted(pd.Timestamp(ts), side="right")) - 1


def as_float(value: Any) -> float:
    return float(value)


def last_ts(frame: pd.DataFrame) -> datetime:
    stamp: pd.Timestamp = pd.Timestamp(frame.index[-1])
    return stamp.to_pydatetime()


def positive_atr(atr: float) -> float:
    if not atr > 0:
        raise ValueError("atr must be positive")
    return float(atr)


def by_kind(swings: Sequence[Swing], kind: SwingKind) -> list[Swing]:
    return [s for s in swings if s.kind == kind]
