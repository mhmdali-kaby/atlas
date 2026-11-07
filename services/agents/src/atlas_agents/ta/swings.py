"""ZigZag swing detector (ATL-132, brief §7.4 "Trend").

A swing is confirmed when price reverses at least ``mult x ATR`` from the running extreme of the
current leg, where ATR is the Wilder ATR of the *confirming* bar. The detector is a single forward
pass whose state at bar t depends only on bars 0..t, so a swing never moves once confirmed and
appending bars can only add swings (no look-ahead).

- Before the first swing both the highest high and the lowest low since bar 0 are tracked. Once
  their distance reaches the threshold, the earlier of the two is the first swing (a low if the
  high came after it, else a high), and the detector follows the leg away from it.
- In an up-leg it tracks the highest high (first bar on ties) and the lowest low *after* it. When
  ``high - low_after >= threshold`` the high is confirmed and a down-leg starts from that low. The
  down-leg mirrors this.
- Each swing is labelled against the previous swing of the same kind: HH / LH / EH for highs,
  HL / LL / EL for lows (E = equal), None for the first of its kind.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray

SwingKind = Literal["high", "low"]
SwingLabel = Literal["HH", "LH", "EH", "HL", "LL", "EL"]


@dataclass(frozen=True)
class Swing:
    kind: SwingKind
    pos: int  # bar position of the pivot in the frame
    ts: datetime  # open time of the pivot bar
    price: float  # the pivot high (kind high) or low (kind low)
    confirmed_pos: int  # bar on which the reversal reached the threshold
    label: SwingLabel | None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "ts": self.ts,
            "price": self.price,
            "label": self.label,
            "bar_index": self.pos,
            "confirmed_bar_index": self.confirmed_pos,
        }


def _label(kind: SwingKind, price: float, previous: float | None) -> SwingLabel | None:
    if previous is None:
        return None
    if kind == "high":
        return "HH" if price > previous else "LH" if price < previous else "EH"
    return "HL" if price > previous else "LL" if price < previous else "EL"


def zigzag(df: pd.DataFrame, threshold: NDArray[np.float64]) -> list[Swing]:
    """Confirmed swings of ``df`` (columns h, l) for a per-bar ``threshold`` (NaN = not yet known)."""
    h = df["h"].to_numpy(dtype="float64")
    lo = df["l"].to_numpy(dtype="float64")
    n = len(df)
    if n == 0:
        return []
    index = pd.DatetimeIndex(df.index)
    swings: list[Swing] = []
    last_price: dict[SwingKind, float | None] = {"high": None, "low": None}

    def confirm(kind: SwingKind, pos: int, price: float, at: int) -> None:
        swings.append(
            Swing(
                kind=kind,
                pos=pos,
                ts=index[pos].to_pydatetime(),
                price=price,
                confirmed_pos=at,
                label=_label(kind, price, last_price[kind]),
            )
        )
        last_price[kind] = price

    trend = 0  # 0 = no swing yet, 1 = up-leg (tracking a high), -1 = down-leg (tracking a low)
    hi, hi_pos = float(h[0]), 0
    lo_x, lo_pos = float(lo[0]), 0
    # In an up-leg: lowest low after hi_pos; in a down-leg: highest high after lo_pos.
    opp, opp_pos = math.nan, -1

    for i in range(n):
        if trend == 0:
            if i > 0:
                if h[i] > hi:
                    hi, hi_pos = float(h[i]), i
                if lo[i] < lo_x:
                    lo_x, lo_pos = float(lo[i]), i
            thr = float(threshold[i])
            if math.isnan(thr) or hi - lo_x < thr or hi_pos == lo_pos:
                continue
            if lo_pos < hi_pos:
                confirm("low", lo_pos, lo_x, i)
                trend = 1
                after = lo[hi_pos + 1 : i + 1]
                opp, opp_pos = _extreme(after, hi_pos + 1, low=True)
            else:
                confirm("high", hi_pos, hi, i)
                trend = -1
                after = h[lo_pos + 1 : i + 1]
                opp, opp_pos = _extreme(after, lo_pos + 1, low=False)
            continue

        if trend == 1:
            if h[i] > hi:
                hi, hi_pos = float(h[i]), i
                opp, opp_pos = math.nan, -1
            elif opp_pos < 0 or lo[i] < opp:
                opp, opp_pos = float(lo[i]), i
            thr = float(threshold[i])
            if opp_pos >= 0 and not math.isnan(thr) and hi - opp >= thr:
                confirm("high", hi_pos, hi, i)
                trend = -1
                lo_x, lo_pos = opp, opp_pos
                opp, opp_pos = _extreme(h[lo_pos + 1 : i + 1], lo_pos + 1, low=False)
        else:
            if lo[i] < lo_x:
                lo_x, lo_pos = float(lo[i]), i
                opp, opp_pos = math.nan, -1
            elif opp_pos < 0 or h[i] > opp:
                opp, opp_pos = float(h[i]), i
            thr = float(threshold[i])
            if opp_pos >= 0 and not math.isnan(thr) and opp - lo_x >= thr:
                confirm("low", lo_pos, lo_x, i)
                trend = 1
                hi, hi_pos = opp, opp_pos
                opp, opp_pos = _extreme(lo[hi_pos + 1 : i + 1], hi_pos + 1, low=True)
    return swings


def _extreme(values: NDArray[np.float64], offset: int, *, low: bool) -> tuple[float, int]:
    """(value, position) of the first minimum (low) or maximum of ``values``; (nan, -1) if empty."""
    if len(values) == 0:
        return math.nan, -1
    k = int(np.argmin(values)) if low else int(np.argmax(values))
    return float(values[k]), offset + k


__all__ = ["Swing", "SwingKind", "SwingLabel", "zigzag"]
