"""Rule-based chart pattern flags (brief §7.4 "Pattern flags", ATL-133). No ML.

Each pattern is a strict geometric rule evaluated at bar ``at`` from the bars opened up to it and the
swings confirmed by it (look-ahead free). ``A`` is the ATR passed in (the timeframe's Wilder
ATR(14)); the multiples come from ``agents.ta_structure.patterns`` in config/defaults.yaml. Bars are
counted by position in the frame. "Close" is the bar's close.

* **breakout_retest** (long; short mirrors with swing lows and inverted inequalities). Level L = the
  price of a swing high. The breakout bar b is the first bar after the swing's confirmation bar whose
  close > L + ``breakout_buffer_atr`` x A, and b is at most ``breakout_max_age_bars`` bars before
  ``at``. Retest: some bar after b has low <= L + ``retest_tol_atr`` x A. Hold: every close from b to
  ``at`` is >= L - ``retest_tol_atr`` x A, and the close at ``at`` is > L. Swings are tried newest
  first; the long/short hit with the later breakout bar wins.
* **failed_breakout** (an upside failure points short; a downside failure points long). Same L and
  breakout bar b as above; then a close back below L (above L for a breakdown) within
  ``failed_within_bars`` bars after b, with b at most ``breakout_max_age_bars`` bars before ``at``.
* **range**: the last ``range_bars`` bars (top = highest high, bottom = lowest low) span at most
  ``range_max_atr`` x A, and the window holds at least ``range_min_touches`` swing highs within
  ``range_edge_tol_atr`` x A of the top and as many swing lows within that of the bottom.
  ``direction`` is null: a range is not a side.
* **flag** (flag or pennant). For a consolidation length k from ``flag_min_bars`` to
  ``flag_max_bars`` (the shortest match wins): the last k bars are the consolidation, the
  ``pole_bars`` bars before them the pole. Pole move = close at the pole's last bar - close of the bar
  before the pole; |move| >= ``pole_min_atr`` x A (up = bull flag, long). The consolidation's
  high-low span is <= ``flag_max_range_frac`` x |move|, and it retraces at most
  ``flag_max_retrace_frac`` of the move (bull: lowest low >= pole close - frac x move; bear mirrored).
  ``shape`` is ``pennant`` when the least-squares slopes of the consolidation's highs and lows converge
  (highs falling and lows rising), else ``flag``.
* **double** (top or bottom). Top: the two newest swing highs H1 < H2 in time are within
  ``double_peak_tol_atr`` x A of each other and at least ``double_min_separation_bars`` bars apart;
  the lowest swing low between them (the neckline) sits at least ``double_min_depth_atr`` x A below the
  lower peak; and no high after H2 exceeds the higher peak by more than the peak tolerance. Points
  short; ``confirmed`` is true once a close is below the neckline. Bottom mirrors with swing lows and
  points long. When both match, the one whose second extreme is newer wins.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_agents.ta._structure import (
    Swing,
    bar_position,
    by_kind,
    cfg,
    cut,
    known_swings,
    positive_atr,
)

PatternName = Literal["breakout_retest", "failed_breakout", "range", "flag", "double"]
PATTERNS: tuple[PatternName, ...] = ("breakout_retest", "failed_breakout", "range", "flag", "double")


class PatternFlag(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: PatternName
    detected: bool
    direction: Literal["long", "short"] | None = None
    level: float | None = None  # the defining level: L, the range top, the flag pole end, the neckline
    details: dict[str, Any] = {}


def _none(name: PatternName) -> PatternFlag:
    return PatternFlag(name=name, detected=False)


class _Bars:
    def __init__(self, frame: pd.DataFrame, swings: Iterable[Swing], at: datetime | None) -> None:
        bars = cut(frame, at)
        self.index = pd.DatetimeIndex(bars.index)
        self.h = bars["h"].to_numpy(dtype="float64")
        self.l = bars["l"].to_numpy(dtype="float64")
        self.c = bars["c"].to_numpy(dtype="float64")
        self.n = len(bars)
        self.swings = known_swings(swings, self.index[-1].to_pydatetime()) if self.n else []

    def pos(self, ts: datetime) -> int:
        return bar_position(self.index, ts)

    def ts(self, i: int) -> datetime:
        stamp: datetime = self.index[i].to_pydatetime()
        return stamp


def _breakout_bar(b: _Bars, swing: Swing, sign: int, buffer: float) -> int | None:
    level = float(swing.price)
    for i in range(b.pos(swing.confirmed_at) + 1, b.n):
        if sign * (b.c[i] - level) > buffer:
            return i
    return None


def breakout_retest(
    frame: pd.DataFrame,
    swings: Iterable[Swing],
    atr: float,
    *,
    at: datetime | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> PatternFlag:
    config = cfg("patterns", defaults)
    a = positive_atr(atr)
    buffer, tol = float(config["breakout_buffer_atr"]) * a, float(config["retest_tol_atr"]) * a
    max_age = int(config["breakout_max_age_bars"])
    b = _Bars(frame, swings, at)
    hits: list[tuple[int, PatternFlag]] = []
    for kind, sign, direction in (("high", 1, "long"), ("low", -1, "short")):
        for swing in reversed(by_kind(b.swings, kind)):  # type: ignore[arg-type]
            level = float(swing.price)
            bo = _breakout_bar(b, swing, sign, buffer)
            if bo is None or b.n - 1 - bo > max_age:
                continue
            after = range(bo + 1, b.n)
            retest = next(
                (j for j in after if (b.l[j] - level <= tol if sign > 0 else level - b.h[j] <= tol)),
                None,
            )
            held = all(sign * (b.c[j] - level) >= -tol for j in range(bo, b.n))
            if retest is None or not held or not sign * (b.c[-1] - level) > 0:
                continue
            flag = PatternFlag(
                name="breakout_retest",
                detected=True,
                direction=direction,
                level=level,
                details={"breakout_ts": b.ts(bo), "retest_ts": b.ts(retest), "swing_ts": swing.ts},
            )
            hits.append((bo, flag))
            break
    if not hits:
        return _none("breakout_retest")
    return max(hits, key=lambda hit: hit[0])[1]


def failed_breakout(
    frame: pd.DataFrame,
    swings: Iterable[Swing],
    atr: float,
    *,
    at: datetime | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> PatternFlag:
    config = cfg("patterns", defaults)
    a = positive_atr(atr)
    buffer = float(config["breakout_buffer_atr"]) * a
    within, max_age = int(config["failed_within_bars"]), int(config["breakout_max_age_bars"])
    b = _Bars(frame, swings, at)
    hits: list[tuple[int, PatternFlag]] = []
    for kind, sign, direction in (("high", 1, "short"), ("low", -1, "long")):
        for swing in reversed(by_kind(b.swings, kind)):  # type: ignore[arg-type]
            level = float(swing.price)
            bo = _breakout_bar(b, swing, sign, buffer)
            if bo is None or b.n - 1 - bo > max_age:
                continue
            back = next(
                (j for j in range(bo + 1, min(bo + within, b.n - 1) + 1) if sign * (b.c[j] - level) < 0),
                None,
            )
            if back is None:
                continue
            flag = PatternFlag(
                name="failed_breakout",
                detected=True,
                direction=direction,
                level=level,
                details={"breakout_ts": b.ts(bo), "failed_ts": b.ts(back), "swing_ts": swing.ts},
            )
            hits.append((bo, flag))
            break
    if not hits:
        return _none("failed_breakout")
    return max(hits, key=lambda hit: hit[0])[1]


def trading_range(
    frame: pd.DataFrame,
    swings: Iterable[Swing],
    atr: float,
    *,
    at: datetime | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> PatternFlag:
    config = cfg("patterns", defaults)
    a = positive_atr(atr)
    window, tol = int(config["range_bars"]), float(config["range_edge_tol_atr"]) * a
    max_width, need = float(config["range_max_atr"]) * a, int(config["range_min_touches"])
    b = _Bars(frame, swings, at)
    if b.n < window:
        return _none("range")
    start = b.n - window
    top, bottom = float(b.h[start:].max()), float(b.l[start:].min())
    if top - bottom > max_width:
        return _none("range")
    inside = [s for s in b.swings if b.pos(s.ts) >= start]
    top_touches = sum(1 for s in inside if s.kind == "high" and float(s.price) >= top - tol)
    bottom_touches = sum(1 for s in inside if s.kind == "low" and float(s.price) <= bottom + tol)
    if top_touches < need or bottom_touches < need:
        return _none("range")
    return PatternFlag(
        name="range",
        detected=True,
        level=top,
        details={
            "top": top,
            "bottom": bottom,
            "width_atr": (top - bottom) / a,
            "top_touches": top_touches,
            "bottom_touches": bottom_touches,
        },
    )


def flag_pennant(
    frame: pd.DataFrame,
    swings: Iterable[Swing],
    atr: float,
    *,
    at: datetime | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> PatternFlag:
    config = cfg("patterns", defaults)
    a = positive_atr(atr)
    pole_bars, pole_min = int(config["pole_bars"]), float(config["pole_min_atr"]) * a
    k_min, k_max = int(config["flag_min_bars"]), int(config["flag_max_bars"])
    range_frac, retrace_frac = float(config["flag_max_range_frac"]), float(config["flag_max_retrace_frac"])
    b = _Bars(frame, swings, at)
    for k in range(k_min, k_max + 1):
        end = b.n - k - 1  # last pole bar
        start = end - pole_bars  # the bar before the pole
        if start < 0:
            break
        move = float(b.c[end] - b.c[start])
        if abs(move) < pole_min:
            continue
        hi, lo = b.h[end + 1 :], b.l[end + 1 :]
        if float(hi.max() - lo.min()) > range_frac * abs(move):
            continue
        pole_close = float(b.c[end])
        if move > 0 and float(lo.min()) < pole_close - retrace_frac * move:
            continue
        if move < 0 and float(hi.max()) > pole_close - retrace_frac * move:
            continue
        x = np.arange(k, dtype="float64")
        slope_h = float(np.polyfit(x, hi, 1)[0])
        slope_l = float(np.polyfit(x, lo, 1)[0])
        return PatternFlag(
            name="flag",
            detected=True,
            direction="long" if move > 0 else "short",
            level=pole_close,
            details={
                "shape": "pennant" if slope_h < 0 < slope_l else "flag",
                "pole_move_atr": move / a,
                "consolidation_bars": k,
                "pole_start_ts": b.ts(start),
                "high_slope": slope_h,
                "low_slope": slope_l,
            },
        )
    return _none("flag")


def double_top_bottom(
    frame: pd.DataFrame,
    swings: Iterable[Swing],
    atr: float,
    *,
    at: datetime | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> PatternFlag:
    config = cfg("patterns", defaults)
    a = positive_atr(atr)
    tol, depth = float(config["double_peak_tol_atr"]) * a, float(config["double_min_depth_atr"]) * a
    min_sep = int(config["double_min_separation_bars"])
    b = _Bars(frame, swings, at)
    hits: list[tuple[pd.Timestamp, PatternFlag]] = []
    for kind, other, sign, direction, label in (
        ("high", "low", 1, "short", "top"),
        ("low", "high", -1, "long", "bottom"),
    ):
        peaks = by_kind(b.swings, kind)  # type: ignore[arg-type]
        if len(peaks) < 2:
            continue
        first, second = peaks[-2], peaks[-1]
        p1, p2 = float(first.price), float(second.price)
        if abs(p1 - p2) > tol or b.pos(second.ts) - b.pos(first.ts) < min_sep:
            continue
        between = [
            s
            for s in by_kind(b.swings, other)  # type: ignore[arg-type]
            if pd.Timestamp(first.ts) < pd.Timestamp(s.ts) < pd.Timestamp(second.ts)
        ]
        if not between:
            continue
        neck = (min if sign > 0 else max)(float(s.price) for s in between)
        weaker = min(p1, p2) if sign > 0 else max(p1, p2)
        if sign * (weaker - neck) < depth:
            continue
        extreme = max(p1, p2) if sign > 0 else min(p1, p2)
        after = slice(b.pos(second.ts) + 1, b.n)
        beyond = b.h[after].max() - extreme if sign > 0 else extreme - b.l[after].min()
        if b.n > b.pos(second.ts) + 1 and float(beyond) > tol:
            continue
        flag = PatternFlag(
            name="double",
            detected=True,
            direction=direction,
            level=neck,
            details={
                "kind": label,
                "first_ts": first.ts,
                "second_ts": second.ts,
                "first_price": p1,
                "second_price": p2,
                "confirmed": bool(sign * (neck - b.c[-1]) > 0),
            },
        )
        hits.append((pd.Timestamp(second.ts), flag))
    if not hits:
        return _none("double")
    return max(hits, key=lambda hit: hit[0])[1]


def detect_patterns(
    frame: pd.DataFrame,
    swings: Iterable[Swing],
    atr: float,
    *,
    at: datetime | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> dict[PatternName, PatternFlag]:
    """All five flags at bar ``at``."""
    known = list(swings)
    if cut(frame, at).empty:
        return {name: _none(name) for name in PATTERNS}
    return {
        "breakout_retest": breakout_retest(frame, known, atr, at=at, defaults=defaults),
        "failed_breakout": failed_breakout(frame, known, atr, at=at, defaults=defaults),
        "range": trading_range(frame, known, atr, at=at, defaults=defaults),
        "flag": flag_pennant(frame, known, atr, at=at, defaults=defaults),
        "double": double_top_bottom(frame, known, atr, at=at, defaults=defaults),
    }
