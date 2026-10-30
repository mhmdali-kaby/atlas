"""The Strategist's numbers in code (brief §7.6, ATL-158): pure functions, no I/O.

* :func:`size_suggestion` — ``base_pct x conviction multiplier x volatility multiplier``.
* :func:`lowest_leverage` — the lowest whole leverage whose margin fits the available balance.
* :func:`clamp_probability` — the model's p within +/- ``max_adjust_points`` of the analog hit rate.
* :func:`analog_hit_rate` — that hit rate, from a Historian ``history_verdict`` card.
* :func:`default_time_stop_minutes` — 24 h for crypto, the end of the 2nd US session for stock perps.
* :func:`split_take_profits` — the default 50/30/20 close split when the model gives no pct.

Thresholds come from ``agents.strategist`` in config/defaults.yaml; every function takes the config
mapping so tests can pass their own.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from atlas_shared.defaults import DEFAULTS


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["strategist"]
    return cfg


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


# -- size ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sizing:
    """A size suggestion and how it was built (the model sees all of it, and never computes it)."""

    base_pct: Decimal
    conviction: Decimal | None
    conviction_multiplier: Decimal
    atr_percentile: Decimal | None
    volatility_multiplier: Decimal
    size_pct: Decimal

    def to_json(self) -> dict[str, Any]:
        return {
            "base_pct": format(self.base_pct, "f"),
            "conviction": None if self.conviction is None else format(self.conviction, "f"),
            "conviction_multiplier": format(self.conviction_multiplier, "f"),
            "atr_percentile": None if self.atr_percentile is None else format(self.atr_percentile, "f"),
            "volatility_multiplier": format(self.volatility_multiplier, "f"),
            "size_suggestion_pct_equity": format(self.size_pct, "f"),
        }


def conviction_multiplier(conviction: Decimal | None, cfg: Mapping[str, Any] | None = None) -> Decimal:
    """0.5 below 0.55, 0.75 from 0.55 to 0.7, 1.0 above 0.7 (brief §7.6)."""
    s = (cfg or config())["sizing"]
    if conviction is None:
        return _d(s["unknown_conviction_multiplier"])
    low, mid, high = (_d(m) for m in s["conviction_multipliers"])
    if conviction < _d(s["conviction_low"]):
        return low
    if conviction <= _d(s["conviction_high"]):
        return mid
    return high


def volatility_multiplier(percentile: Decimal | None, cfg: Mapping[str, Any] | None = None) -> Decimal:
    """1.0 below the 60th ATR% percentile, 0.75 from 60 to 85, 0.5 above 85 (brief §7.6)."""
    s = (cfg or config())["sizing"]
    if percentile is None:
        return _d(s["unknown_vol_multiplier"])
    calm, normal, wild = (_d(m) for m in s["vol_multipliers"])
    if percentile < _d(s["vol_low_percentile"]):
        return calm
    if percentile <= _d(s["vol_high_percentile"]):
        return normal
    return wild


def size_suggestion(
    base_pct: Decimal,
    conviction: Decimal | None,
    atr_percentile: Decimal | None,
    cfg: Mapping[str, Any] | None = None,
) -> Sizing:
    cm = conviction_multiplier(conviction, cfg)
    vm = volatility_multiplier(atr_percentile, cfg)
    return Sizing(
        base_pct=base_pct,
        conviction=conviction,
        conviction_multiplier=cm,
        atr_percentile=atr_percentile,
        volatility_multiplier=vm,
        size_pct=(base_pct * cm * vm).normalize(),
    )


def lowest_leverage(
    size_pct: Decimal,
    sizing_equity: Decimal | None,
    available_usd: Decimal | None,
    max_leverage: int | None,
    instrument_max: Decimal | None,
    cfg: Mapping[str, Any] | None = None,
) -> int:
    """The lowest whole leverage with ``notional / leverage <= available x margin_buffer``, at least 1
    and at most the owner's and the instrument's maximum. 1 when equity or balance is unknown."""
    caps = [int(c) for c in (max_leverage, instrument_max) if c is not None and int(c) >= 1]
    cap = min(caps) if caps else None
    if sizing_equity is None or available_usd is None or sizing_equity <= 0:
        return 1
    usable = available_usd * _d((cfg or config())["sizing"]["margin_buffer"])
    notional = sizing_equity * size_pct / 100
    # No usable balance: no leverage fits; 1 lets the Risk Engine's margin check refuse it.
    needed = 1 if usable <= 0 else max(1, math.ceil(notional / usable))
    return min(needed, cap) if cap is not None else needed


# -- probability ---------------------------------------------------------------------------------------


def analog_hit_rate(
    verdict_card: Mapping[str, Any] | None, side: Literal["buy", "sell"], cfg: Mapping[str, Any] | None = None
) -> tuple[Decimal, str] | None:
    """(hit rate 0..1, horizon) from a history_verdict card's ``distribution``; None without one."""
    if not verdict_card:
        return None
    dist = verdict_card.get("distribution")
    if not isinstance(dist, Mapping):
        return None
    for horizon in (cfg or config())["probability"]["analog_horizons"]:
        row = dist.get(horizon)
        if not isinstance(row, Mapping) or row.get("pct_positive") is None:
            continue
        try:
            positive = _d(row["pct_positive"]) / 100
        except ArithmeticError:
            continue
        if not positive.is_finite() or not 0 <= positive <= 1:
            continue
        return (positive if side == "buy" else 1 - positive), str(horizon)
    return None


def clamp_probability(
    probability: Decimal, hit_rate: Decimal, cfg: Mapping[str, Any] | None = None
) -> Decimal:
    band = _d((cfg or config())["probability"]["max_adjust_points"]) / 100
    low = max(Decimal(0), hit_rate - band)
    high = min(Decimal(1), hit_rate + band)
    return min(max(probability, low), high)


# -- defaults for the plan -----------------------------------------------------------------------------


def split_take_profits(count: int, cfg: Mapping[str, Any] | None = None) -> list[Decimal]:
    """The close % for ``count`` take-profits: the configured split, the last target taking the rest
    of it (3 targets: 50/30/20; 2: 50/50; 1: 100)."""
    split = [_d(p) for p in (cfg or config())["take_profit_split"]]
    if count <= 0:
        return []
    if count >= len(split):
        return split[:count]
    head = split[: count - 1]
    return [*head, sum(split[count - 1 :], Decimal(0))]


def _us_sessions(start: datetime, cfg: Mapping[str, Any]) -> list[tuple[datetime, datetime]]:
    t = cfg["time_stop"]
    zone = ZoneInfo(str(t["us_timezone"]))
    open_t = time.fromisoformat(str(t["us_open"]))
    close_t = time.fromisoformat(str(t["us_close"]))
    local = start.astimezone(zone)
    sessions: list[tuple[datetime, datetime]] = []
    day = local.date()
    while len(sessions) < int(t["us_sessions"]):
        if day.weekday() < 5:
            opens = datetime.combine(day, open_t, tzinfo=zone)
            closes = datetime.combine(day, close_t, tzinfo=zone)
            if closes > local:
                sessions.append((opens, closes))
        day += timedelta(days=1)
    return sessions


def default_time_stop_minutes(
    asset_class: str | None, now: datetime, cfg: Mapping[str, Any] | None = None
) -> int:
    """24 h for crypto (and anything that is not a US-session asset); for stock/ETF/index perps the
    minutes until the close of the 2nd US regular session (Mon-Fri, US/Eastern, DST-aware; a session
    in progress counts as the first). Exchange holidays are not known, so a holiday counts."""
    c = cfg or config()
    t = c["time_stop"]
    if asset_class not in set(t["us_session_asset_classes"]):
        return int(t["crypto_minutes"])
    sessions = _us_sessions(now, c)
    end = sessions[-1][1]
    return max(1, math.ceil((end - now).total_seconds() / 60))


def is_us_session_asset(asset_class: str | None, cfg: Mapping[str, Any] | None = None) -> bool:
    return asset_class in set((cfg or config())["time_stop"]["us_session_asset_classes"])


def multipliers_table(cfg: Mapping[str, Any] | None = None) -> Sequence[str]:
    """The sizing rule as text lines for the prompt."""
    s = (cfg or config())["sizing"]
    cm = s["conviction_multipliers"]
    vm = s["vol_multipliers"]
    return [
        f"conviction < {s['conviction_low']}: x{cm[0]}; {s['conviction_low']}-{s['conviction_high']}: "
        f"x{cm[1]}; > {s['conviction_high']}: x{cm[2]}",
        f"ATR% percentile < {s['vol_low_percentile']}: x{vm[0]}; {s['vol_low_percentile']}-"
        f"{s['vol_high_percentile']}: x{vm[1]}; > {s['vol_high_percentile']}: x{vm[2]}",
    ]
