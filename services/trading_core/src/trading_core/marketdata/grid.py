"""The bar grid every candle series sits on (ATL-47).

OKX bars open on fixed boundaries: 1m/5m/15m/1H on the UTC clock, 4H and 1D on UTC+8 (so a 1D bar
opens at 16:00 UTC). The offsets live in ``candle_grid.offset_minutes``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas_shared.defaults import DEFAULTS

from trading_core.okx.models import Candle

BAR_SECONDS: Mapping[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}

CANDLES_PREFIX = "candles:"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def bar_delta(bar: str) -> timedelta:
    return timedelta(seconds=BAR_SECONDS[bar])


def _offset(bar: str) -> timedelta:
    offsets: Mapping[str, Any] = DEFAULTS["candle_grid"]["offset_minutes"]
    return timedelta(minutes=int(offsets.get(bar, 0)))


def slot(bar: str, ts: datetime) -> datetime:
    """The open time of the grid slot that contains ``ts``."""
    step = BAR_SECONDS[bar]
    shifted = ts - _offset(bar) - _EPOCH
    seconds = int(shifted.total_seconds() // step) * step
    return _EPOCH + _offset(bar) + timedelta(seconds=seconds)


def last_close(bar: str, at: datetime) -> datetime:
    """The most recent bar close at or before ``at`` (= the open time of the bar forming at ``at``)."""
    return slot(bar, at)


def dataset(bar: str) -> str:
    return CANDLES_PREFIX + bar


def candle_rows(env: str, inst_id: str, bar: str, candles: Sequence[Candle]) -> list[dict[str, Any]]:
    """``candles`` rows for the confirmed bars of an OKX page (forming bars are skipped; the NOT NULL
    price/volume columns need values)."""
    return [
        {
            "env": env,
            "inst_id": inst_id,
            "bar": bar,
            "ts": c.ts,
            "o": c.o,
            "h": c.h,
            "l": c.l,
            "c": c.c,
            "vol": c.vol,
            "vol_ccy": c.vol_ccy,
            "vol_ccy_quote": c.vol_ccy_quote,
            "confirmed": True,
        }
        for c in candles
        if c.confirm and None not in (c.o, c.h, c.l, c.c, c.vol)
    ]


__all__ = ["BAR_SECONDS", "bar_delta", "candle_rows", "dataset", "last_close", "slot"]
