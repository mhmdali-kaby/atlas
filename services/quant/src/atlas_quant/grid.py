"""The OKX bar grid (same rule as trading_core.marketdata.grid, ATL-47).

1m/5m/15m/1H/4H bars open on the UTC clock and 1D on UTC+8 (16:00 UTC); the offsets come from
``candle_grid.offset_minutes``. atlas_quant does not import trading_core (trading_core will import
atlas_quant), so the slot rule is restated here from the same defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas_shared.defaults import DEFAULTS

BAR_SECONDS: Mapping[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def bar_seconds(bar: str) -> int:
    try:
        return BAR_SECONDS[bar]
    except KeyError:
        raise ValueError(f"unknown bar {bar!r}; expected one of {sorted(BAR_SECONDS)}") from None


def bar_delta(bar: str) -> timedelta:
    return timedelta(seconds=bar_seconds(bar))


def offset(bar: str) -> timedelta:
    offsets: Mapping[str, Any] = DEFAULTS["candle_grid"]["offset_minutes"]
    return timedelta(minutes=int(offsets.get(bar, 0)))


def slot(bar: str, ts: datetime) -> datetime:
    """Open time of the grid slot containing ``ts`` (``ts`` must be timezone-aware)."""
    step = bar_seconds(bar)
    shifted = ts.astimezone(UTC) - offset(bar) - _EPOCH
    seconds = int(shifted.total_seconds() // step) * step
    return _EPOCH + offset(bar) + timedelta(seconds=seconds)


def first_slot_at_or_after(bar: str, ts: datetime) -> datetime:
    s = slot(bar, ts)
    return s if s >= ts.astimezone(UTC) else s + bar_delta(bar)


__all__ = ["BAR_SECONDS", "bar_delta", "bar_seconds", "first_slot_at_or_after", "offset", "slot"]
