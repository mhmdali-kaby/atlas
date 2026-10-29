"""Candles loader with a data-quality report (ATL-79, brief §7.3).

``load_candles`` reads one series of the ``candles`` hypertable into a UTC-indexed DataFrame and
reports what is missing against the bar grid. It is look-ahead safe: with ``as_of`` set it returns
only bars that had **closed** by ``as_of`` (``ts + bar <= as_of``), so a bar with ``ts > as_of`` can
never appear, and neither can the bar still forming at ``as_of``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple

import pandas as pd
from atlas_shared.db import Candle
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from atlas_quant.grid import bar_delta, first_slot_at_or_after, slot

COLUMNS = ("o", "h", "l", "c", "vol", "vol_ccy", "vol_ccy_quote")

# Postgres timestamptz resolution. ``ts < as_of - bar + 1 µs`` is ``ts + bar <= as_of``.
_EPSILON = timedelta(microseconds=1)

GapPosition = Literal["leading", "interior", "trailing", "empty"]


class GapRange(BaseModel):
    """A run of consecutive missing grid slots. ``from_ts``/``to_ts`` are the first and last missing
    bar open times (inclusive), the same convention as ATL-47's ``data_quality_issues`` gaps."""

    model_config = ConfigDict(frozen=True)

    from_ts: datetime
    to_ts: datetime
    missing_bars: int
    # leading: before the first returned bar; trailing: after the last; interior: between two bars;
    # empty: no bar was returned at all.
    position: GapPosition


class DataQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    inst_id: str
    bar: str
    env: str
    window_start: datetime
    window_end: datetime
    as_of: datetime | None
    # Grid slots in the effective window: start <= slot < end, and slot + bar <= as_of.
    expected_bars: int
    returned_bars: int
    missing_bars: int
    gaps: list[GapRange]
    # Extra rows dropped because another row already held their grid slot.
    duplicate_count: int
    zero_volume_count: int
    # True when any expected slot of the window has no bar, wherever it is.
    gap_in_window: bool
    # True when a slot is missing between the first and last returned bars: the series has a hole,
    # rather than only starting late or not being polled up to the window end yet.
    interior_gap: bool


class Candles(NamedTuple):
    df: pd.DataFrame
    quality: DataQuality


def _as_utc(ts: datetime, name: str) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return ts.astimezone(UTC)


def _float(v: object) -> float:
    if v is None:
        return float("nan")
    return float(str(v))


def load_candles(
    session: Session | Connection,
    inst_id: str,
    bar: str,
    start: datetime,
    end: datetime,
    as_of: datetime | None = None,
    *,
    env: str | None = None,
) -> Candles:
    """Confirmed bars of ``inst_id``/``bar`` with ``start <= ts < end`` that had closed by ``as_of``.

    The frame is indexed by ``ts`` (UTC, ascending, unique) with float64 columns o, h, l, c, vol,
    vol_ccy, vol_ccy_quote (the last two NaN when OKX gave none). When two rows fall in one grid slot
    (an off-grid ts beside the on-grid bar), the on-grid row is kept, else the earliest, and the
    extra rows are counted in ``quality.duplicate_count``. ``env`` defaults to ``env.default``.
    """
    series_env = env or str(DEFAULTS["env"]["default"])
    start = _as_utc(start, "start")
    end = _as_utc(end, "end")
    cutoff = _as_utc(as_of, "as_of") if as_of is not None else None
    step = bar_delta(bar)

    # Exclusive upper bound on the bar open time.
    stop = end if cutoff is None else min(end, cutoff - step + _EPSILON)

    stmt = (
        select(
            Candle.ts,
            Candle.o,
            Candle.h,
            Candle.l,
            Candle.c,
            Candle.vol,
            Candle.vol_ccy,
            Candle.vol_ccy_quote,
        )
        .where(
            Candle.env == series_env,
            Candle.inst_id == inst_id,
            Candle.bar == bar,
            Candle.confirmed.is_(True),
            Candle.ts >= start,
            Candle.ts < stop,
        )
        .order_by(Candle.ts)
    )
    rows = [(_as_utc(r[0], "ts"), tuple(_float(v) for v in r[1:])) for r in session.execute(stmt).all()]

    kept: dict[datetime, tuple[datetime, tuple[float, ...]]] = {}
    duplicates = 0
    for row in rows:
        s = slot(bar, row[0])
        if s not in kept:
            kept[s] = row
            continue
        duplicates += 1
        if row[0] == s:  # the on-grid bar wins over an earlier off-grid one
            kept[s] = row

    slots = sorted(kept)
    df = pd.DataFrame(
        [kept[s][1] for s in slots],
        index=pd.DatetimeIndex([kept[s][0] for s in slots], name="ts").tz_convert(UTC)
        if slots
        else pd.DatetimeIndex([], tz=UTC, name="ts"),
        columns=list(COLUMNS),
        dtype="float64",
    )

    expected = _expected_slots(bar, start, stop)
    present = set(slots)
    missing = [s for s in expected if s not in present]
    gaps = _gap_ranges(bar, missing, slots[0] if slots else None, slots[-1] if slots else None)
    zero_volume = int((df["vol"] == 0).sum())

    quality = DataQuality(
        inst_id=inst_id,
        bar=bar,
        env=series_env,
        window_start=start,
        window_end=end,
        as_of=cutoff,
        expected_bars=len(expected),
        returned_bars=len(df),
        missing_bars=len(missing),
        gaps=gaps,
        duplicate_count=duplicates,
        zero_volume_count=zero_volume,
        gap_in_window=bool(missing),
        interior_gap=any(g.position == "interior" for g in gaps),
    )
    return Candles(df, quality)


def _expected_slots(bar: str, start: datetime, stop: datetime) -> list[datetime]:
    step = bar_delta(bar)
    out: list[datetime] = []
    s = first_slot_at_or_after(bar, start)
    while s < stop:
        out.append(s)
        s += step
    return out


def _gap_ranges(
    bar: str, missing: list[datetime], first: datetime | None, last: datetime | None
) -> list[GapRange]:
    step = bar_delta(bar)
    runs: list[list[datetime]] = []
    for s in missing:
        if runs and s - runs[-1][-1] == step:
            runs[-1].append(s)
        else:
            runs.append([s])
    out: list[GapRange] = []
    for run in runs:
        position: GapPosition
        if first is None or last is None:
            position = "empty"
        elif run[-1] < first:
            position = "leading"
        elif run[0] > last:
            position = "trailing"
        else:
            position = "interior"
        out.append(GapRange(from_ts=run[0], to_ts=run[-1], missing_bars=len(run), position=position))
    return out


__all__ = ["COLUMNS", "Candles", "DataQuality", "GapPosition", "GapRange", "load_candles"]
