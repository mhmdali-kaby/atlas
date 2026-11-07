"""How far price has moved since a moment, in ATR units (ATL-106, brief §7.2).

News Intelligence reports ``price_move_since_first_seen_atr`` for every instrument a story is
linked to: has the market already reacted? :func:`move_since_atr` answers

    (price_now - close of the last bar closed by ``at``) / Wilder ATR(``quant.atr_window``) at ``at``

where the ATR is computed over the bars closed by ``at`` (the same ATR as ``compute_stats`` and
``event_study``). Nothing after ``at`` enters the reference close or the ATR. When the reference
bar is missing or there is too little history for the ATR, the value is ``None`` with a reason.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_quant.grid import bar_delta, slot
from atlas_quant.stats import _wilder_atr


class MoveSince(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: float | None
    """Signed move in ATR units; positive means price is above the reference close."""
    reason: str | None
    """Why ``value`` is None; None when it is set."""
    ref_ts: datetime | None
    """Open time of the reference bar (the last bar closed by ``at``)."""
    ref_close: float | None
    atr: float | None
    price_now: float | None


def _none(
    reason: str,
    *,
    ref_ts: datetime | None = None,
    ref_close: float | None = None,
    price_now: float | None = None,
) -> MoveSince:
    return MoveSince(
        value=None, reason=reason, ref_ts=ref_ts, ref_close=ref_close, atr=None, price_now=price_now
    )


def move_since_atr(
    bars: pd.DataFrame,
    at: datetime,
    price_now: float | None,
    *,
    bar: str,
    window: int | None = None,
) -> MoveSince:
    """``bars``: ``bar`` candles indexed by UTC open time with columns h, l, c (as from
    :func:`atlas_quant.data.load_candles`). ``window`` defaults to ``quant.atr_window``."""
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    w = int(window if window is not None else DEFAULTS["quant"]["atr_window"])
    ref_ts = slot(bar, at.astimezone(UTC)) - bar_delta(bar)
    if price_now is None:
        return _none("no current price")
    index = pd.DatetimeIndex(bars.index)
    if bars.empty or pd.Timestamp(ref_ts) not in index:
        return _none(
            f"no {bar} candle closed at first_seen ({ref_ts.isoformat()} bar missing)", price_now=price_now
        )
    history = bars.loc[: pd.Timestamp(ref_ts)]
    ref_close = float(history["c"].iloc[-1])
    atr = _wilder_atr(history, w)
    if atr is None:
        return _none(
            f"fewer than {w} {bar} candles before first_seen for ATR({w})",
            ref_ts=ref_ts,
            ref_close=ref_close,
            price_now=price_now,
        )
    if not atr > 0:
        return _none(
            f"ATR({w}) at first_seen is zero", ref_ts=ref_ts, ref_close=ref_close, price_now=price_now
        )
    return MoveSince(
        value=(float(price_now) - ref_close) / atr,
        reason=None,
        ref_ts=ref_ts,
        ref_close=ref_close,
        atr=atr,
        price_now=float(price_now),
    )


__all__ = ["MoveSince", "move_since_atr"]
