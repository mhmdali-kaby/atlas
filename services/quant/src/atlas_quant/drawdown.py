"""Drawdown episodes of one candle series (ATL-80, brief §7.3 ``drawdown_profile``).

Definitions (pinned by ``quant/tests/test_drawdown_golden.py``), all on closes:

- A drawdown episode starts at a running high (``start`` = the last bar that set or equalled the high)
  when a later close falls below it, and ends at ``recovery``: the first later bar whose close is at
  or above that high. An episode still open at the last bar has ``recovery = None``.
- ``trough`` is the first bar at the episode's lowest close; ``depth = c_trough / c_start - 1`` (a
  fraction < 0).
- ``duration`` runs from start to recovery, or to the last bar for an open episode;
  ``time_to_recover`` runs from trough to recovery (None when open). Both in bars and days.
- The profile lists the ``quant.drawdown.top_n`` deepest episodes (ties: earlier first), the current
  drawdown (last close against the running high, 0 at a high), and the median time to recover, in
  days, over the listed episodes that recovered (None when none did).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

import pandas as pd
from pydantic import BaseModel, ConfigDict

from atlas_quant._frame import prepare, quant_cfg


class DrawdownEpisode(BaseModel):
    model_config = ConfigDict(frozen=True)

    depth: float  # fraction < 0
    start: datetime  # the high
    trough: datetime
    recovery: datetime | None
    duration_bars: int
    duration_days: float
    time_to_recover_bars: int | None
    time_to_recover_days: float | None


class CurrentDrawdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    depth: float  # fraction ≤ 0; 0 at a running high
    peak: datetime  # the running high's bar
    duration_bars: int  # bars since the high
    duration_days: float


class DrawdownProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int
    period_start: datetime
    period_end: datetime
    n_drawdowns: int  # all episodes, not only the listed ones
    top: list[DrawdownEpisode]
    current: CurrentDrawdown
    median_time_to_recover_days: float | None


def _days(a: datetime, b: datetime) -> float:
    return (b - a) / timedelta(days=1)


def drawdown_profile(df: pd.DataFrame, top_n: int | None = None) -> DrawdownProfile:
    """Drawdown episodes of a frame with a tz-aware index and a float ``c`` column."""
    frame = prepare(df, ("c",))
    if frame.empty:
        raise ValueError("frame is empty")
    limit = int(quant_cfg("drawdown")["top_n"]) if top_n is None else top_n
    closes = [float(v) for v in frame["c"].tolist()]
    ts = [t.to_pydatetime() for t in pd.DatetimeIndex(frame.index)]
    last = len(closes) - 1

    # (start, trough, recovery or None)
    raw: list[tuple[int, int, int | None]] = []
    peak_i = 0
    trough_i: int | None = None
    for i in range(1, len(closes)):
        if closes[i] >= closes[peak_i]:
            if trough_i is not None:
                raw.append((peak_i, trough_i, i))
                trough_i = None
            peak_i = i
        elif trough_i is None or closes[i] < closes[trough_i]:
            trough_i = i
    if trough_i is not None:
        raw.append((peak_i, trough_i, None))

    episodes: list[DrawdownEpisode] = []
    for s, t, r in raw:
        end = last if r is None else r
        episodes.append(
            DrawdownEpisode(
                depth=closes[t] / closes[s] - 1.0,
                start=ts[s],
                trough=ts[t],
                recovery=None if r is None else ts[r],
                duration_bars=end - s,
                duration_days=_days(ts[s], ts[end]),
                time_to_recover_bars=None if r is None else r - t,
                time_to_recover_days=None if r is None else _days(ts[t], ts[r]),
            )
        )
    top = sorted(episodes, key=lambda e: (e.depth, e.start))[: max(limit, 0)]
    recover = [e.time_to_recover_days for e in top if e.time_to_recover_days is not None]

    return DrawdownProfile(
        n=len(closes),
        period_start=ts[0],
        period_end=ts[-1],
        n_drawdowns=len(episodes),
        top=top,
        current=CurrentDrawdown(
            depth=closes[last] / closes[peak_i] - 1.0,
            peak=ts[peak_i],
            duration_bars=last - peak_i,
            duration_days=_days(ts[peak_i], ts[last]),
        ),
        median_time_to_recover_days=statistics.median(recover) if recover else None,
    )
