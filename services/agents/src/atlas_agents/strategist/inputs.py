"""What the Strategist gets for one instrument, assembled in code (brief §7.6 Inputs, ATL-158).

:func:`gather` reads, as of ``ctx.now()`` (bounded in replay):

* the watchlist pick (today's Asia/Baghdad watchlist, owner pins included, owner removals left out);
* the newest TechnicalSetup (technical_analyst), PositioningReport (derivatives_sentiment), News
  Intelligence ``news_brief`` naming the instrument, and Historian ``history_verdict`` cards, each
  with its age in minutes; the ones that do not exist are listed in ``missing``;
* the portfolio: equity, sizing equity (decision #250: min(equity, bot capital cap)), available
  balance in the instrument's settle currency, open positions and today's proposals on it;
* the read-only settings snapshot (the same numbers-only allow-list as get_settings_snapshot);
* the sizing guidance and the lowest leverage that fits, computed here so the model receives the
  numbers and never computes them, plus the analog hit rate per side and the default time stop and
  take-profit split.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

import numpy as np
from atlas_quant import load_candles
from atlas_quant.grid import bar_delta
from atlas_quant.indicators import wilder_atr
from atlas_shared.db import (
    BalanceSnapshot,
    Instrument,
    Proposal,
    Watchlist,
    WatchlistItem,
    owner_day,
)
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from atlas_agents import proposals as props
from atlas_agents.strategist import sizing
from atlas_agents.tools.account import (
    _latest_equity,
    _positions_at,
    _sync_instant,
    load_settings,
)
from atlas_agents.tools.framework import ToolContext, ToolError

TECHNICAL: Final = "technical_setup"
POSITIONING: Final = "positioning_report"
NEWS: Final = "news_brief"
HISTORY: Final = "history_verdict"
INPUT_NAMES: Final = (TECHNICAL, POSITIONING, NEWS, HISTORY)
AUTHORS: Final[Mapping[str, str]] = {
    TECHNICAL: "technical_analyst",
    NEWS: "news_intelligence",
    HISTORY: "historian",
}


@dataclass(frozen=True)
class Item:
    """One research input: the card, its War Room message id and how old it is."""

    card: dict[str, Any]
    message_id: str | None
    posted_at: datetime
    age_minutes: int

    def to_json(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "posted_at": self.posted_at.isoformat(),
            "age_minutes": self.age_minutes,
            "card": self.card,
        }


@dataclass(frozen=True)
class StrategistInputs:
    inst_id: str
    as_of: datetime
    asset_class: str | None
    watchlist_pick: dict[str, Any] | None
    items: dict[str, Item | None]
    portfolio: dict[str, Any]
    settings: dict[str, Any]
    sizing: sizing.Sizing | None
    leverage: int
    hit_rates: dict[str, dict[str, Any] | None]
    time_stop_minutes: int
    take_profit_split: list[Decimal]
    notes: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        return [name for name in INPUT_NAMES if self.items.get(name) is None]

    def item(self, name: str) -> Item | None:
        return self.items.get(name)

    def to_json(self) -> dict[str, Any]:
        """The JSON the model receives (decimals as strings)."""
        return {
            "inst_id": self.inst_id,
            "as_of": self.as_of.isoformat(),
            "asset_class": self.asset_class,
            "watchlist_pick": self.watchlist_pick,
            "inputs": {name: (i.to_json() if i else None) for name, i in self.items.items()},
            "missing": self.missing,
            "portfolio": self.portfolio,
            "settings": self.settings,
            "sizing": None if self.sizing is None else self.sizing.to_json(),
            "leverage_suggestion": self.leverage,
            "analog_hit_rate": self.hit_rates,
            "default_time_stop_minutes": self.time_stop_minutes,
            "default_take_profit_split_pct": [format(p, "f") for p in self.take_profit_split],
            "notes": self.notes,
        }


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


# -- reads ---------------------------------------------------------------------------------------------


def watchlist_items(session: Session, ctx: ToolContext) -> list[WatchlistItem]:
    """Today's (Asia/Baghdad) current watchlist items, best rank first, owner removals left out."""
    wl = session.scalars(
        select(Watchlist)
        .where(
            Watchlist.env == ctx.env, Watchlist.date == owner_day(ctx.now()), ctx.bound(Watchlist.created_at)
        )
        .order_by(Watchlist.version.desc())
        .limit(1)
    ).first()
    if wl is None:
        return []
    not_removed: ColumnElement[bool] = WatchlistItem.removed_by_owner_at.is_(None)
    if ctx.replay_as_of is not None:
        not_removed = or_(not_removed, WatchlistItem.removed_by_owner_at > ctx.replay_as_of)
    return list(
        session.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == wl.id, not_removed)
            .order_by(WatchlistItem.rank)
        )
    )


def _pick(item: WatchlistItem) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "asset_class": item.asset_class,
        "bias": item.bias,
        "conviction": _s(item.conviction),
        "thesis": item.thesis,
        "catalysts": item.catalysts,
        "key_levels": item.key_levels,
        "invalidation": item.invalidation,
        "pinned_by_owner": item.pinned_by_owner,
    }


def _latest(session: Session, ctx: ToolContext, author: str, card_type: str, inst_id: str) -> Item | None:
    found = props.latest_card(
        session, ctx, author=author, card_type=card_type, inst_id=inst_id, inst_ids_field=card_type == NEWS
    )
    if found is None:
        return None
    age = int((ctx.now() - found.posted_at).total_seconds() // 60)
    return Item(
        card=found.card, message_id=found.message_id, posted_at=found.posted_at, age_minutes=max(0, age)
    )


def atr_percentile(
    session: Session, ctx: ToolContext, inst_id: str, cfg: Mapping[str, Any]
) -> Decimal | None:
    """Percentile (0-100) of the newest ATR% among the last ``atr_percentile_lookback`` values, on
    ``atr_bar`` closed candles known at ``ctx.now()``. None with too little history."""
    s = cfg["sizing"]
    bar = str(s["atr_bar"])
    window = int(s["atr_window"])
    lookback = int(s["atr_percentile_lookback"])
    as_of = ctx.now()
    start = as_of - bar_delta(bar) * (lookback + window * 3)
    frame = load_candles(session, inst_id, bar, start, as_of, as_of=as_of, env=ctx.env).df
    if len(frame) < window + lookback:
        return None
    atr_pct = (wilder_atr(frame, window) / frame["c"] * 100.0).dropna().to_numpy(dtype="float64")
    if len(atr_pct) < lookback:
        return None
    recent = atr_pct[-lookback:]
    share = float(np.mean(recent <= recent[-1]) * 100.0)
    return Decimal(str(round(share, 2)))


def _settings_number(data: Mapping[str, Any], values: Mapping[str, Any], key: str) -> Decimal | None:
    found = _dec(values.get(key))
    if found is not None:
        return found
    profiles: Mapping[str, Any] = DEFAULTS["risk_profiles"]
    profile = data.get("risk_profile")
    if not isinstance(profile, str) or profile not in profiles or profile == "default":
        profile = str(profiles["default"])
    return _dec(profiles[profile].get(key))


def _available_usd(
    session: Session, ctx: ToolContext, settle: str | None, taken_at: datetime | None
) -> Decimal | None:
    if settle is None or taken_at is None:
        return None
    bal = session.execute(
        select(BalanceSnapshot.avail_bal, BalanceSnapshot.eq, BalanceSnapshot.eq_usd).where(
            BalanceSnapshot.env == ctx.env,
            BalanceSnapshot.account == "trading",
            BalanceSnapshot.ccy == settle,
            BalanceSnapshot.taken_at == taken_at,
        )
    ).first()
    if bal is None or bal.avail_bal is None:
        return None
    rate = Decimal(1)
    if bal.eq_usd is not None and bal.eq:
        rate = bal.eq_usd / bal.eq
    available: Decimal = bal.avail_bal * rate
    return available


# -- assembly ------------------------------------------------------------------------------------------


def gather(
    session: Session,
    ctx: ToolContext,
    inst_id: str,
    *,
    cfg: Mapping[str, Any] | None = None,
) -> StrategistInputs:
    c = cfg or sizing.config()
    now = ctx.now()
    notes: list[str] = []

    pick_row = next((i for i in watchlist_items(session, ctx) if i.inst_id == inst_id), None)
    inst = session.scalars(
        select(Instrument).where(Instrument.env == ctx.env, Instrument.inst_id == inst_id)
    ).first()
    asset_class = inst.asset_class if inst is not None else (pick_row.asset_class if pick_row else None)

    authors = {**AUTHORS, POSITIONING: str(c["positioning_author"])}
    items: dict[str, Item | None] = {
        name: _latest(session, ctx, authors[name], name, inst_id) for name in INPUT_NAMES
    }

    # Settings: the numbers-only snapshot; base_pct and max leverage fall back to the row's profile.
    settings_version: int | None = None
    values: dict[str, Any] = {}
    try:
        settings_version, values, _ = load_settings(session, ctx)
    except ToolError:
        notes.append("No settings are saved for this environment: no size can be suggested.")
    data = props.settings_data(session, ctx).data
    base_pct = _settings_number(data, values, "max_position_pct") if settings_version is not None else None
    max_lev_raw = _settings_number(data, values, "max_leverage") if settings_version is not None else None
    max_leverage = int(max_lev_raw) if max_lev_raw is not None else None

    equity_row = _latest_equity(session, ctx)
    equity = equity_row.total_eq_usd if equity_row is not None else None
    cap = _dec(data.get("bot_capital_cap_usdt"))
    sizing_equity = equity
    if equity is not None and cap is not None and cap > 0:
        sizing_equity = min(equity, cap)
    taken_at = _sync_instant(session, ctx)
    settle = None
    if inst is not None:
        settle = inst.settle_ccy if inst.inst_type != "SPOT" else inst.quote_ccy
    available = _available_usd(session, ctx, settle, taken_at)
    positions = [
        {
            "inst_id": p.inst_id,
            "direction": props.position_direction(p),
            "pos": _s(p.pos),
            "avg_px": _s(p.avg_px),
            "upl": _s(p.upl),
        }
        for p in _positions_at(session, ctx, taken_at)
        if p.pos != 0
    ]
    owner_zone = ZoneInfo(str(DEFAULTS["timezone"]["display"]))
    day_start = datetime.combine(owner_day(now), time.min, tzinfo=owner_zone).astimezone(UTC)
    today = [
        {"proposal_id": str(p.id), "side": p.side, "status": p.status, "version": p.version, "kind": p.kind}
        for p in session.scalars(
            select(Proposal)
            .where(
                Proposal.env == ctx.env,
                Proposal.inst_id == inst_id,
                Proposal.created_at >= day_start,
                ctx.bound(Proposal.created_at),
            )
            .order_by(Proposal.created_at)
        )
    ]

    conviction = _dec(pick_row.conviction) if pick_row is not None else None
    if pick_row is None:
        notes.append(f"{inst_id} is not on today's watchlist.")
    percentile = atr_percentile(session, ctx, inst_id, c)
    if percentile is None:
        notes.append(
            "Not enough candles for the ATR% percentile: the volatility multiplier is the cautious one."
        )
    size = sizing.size_suggestion(base_pct, conviction, percentile, c) if base_pct is not None else None
    leverage = sizing.lowest_leverage(
        size.size_pct if size else Decimal(0),
        sizing_equity,
        available,
        max_leverage,
        inst.max_lever if inst is not None else None,
        c,
    )

    verdict = items[HISTORY]
    hit_rates: dict[str, dict[str, Any] | None] = {}
    for side in ("buy", "sell"):
        found = sizing.analog_hit_rate(verdict.card if verdict else None, side, c)
        hit_rates[side] = None if found is None else {"hit_rate": _s(found[0]), "horizon": found[1]}

    return StrategistInputs(
        inst_id=inst_id,
        as_of=now,
        asset_class=asset_class,
        watchlist_pick=_pick(pick_row) if pick_row is not None else None,
        items=items,
        portfolio={
            "equity_usd": _s(equity),
            "sizing_equity_usd": _s(sizing_equity),
            "bot_capital_cap_usdt": _s(cap),
            "available_usd": _s(available),
            "settle_ccy": settle,
            "account_synced_at": taken_at.isoformat() if taken_at else None,
            "open_positions": positions,
            "proposals_today_on_instrument": today,
        },
        settings={"version": settings_version, "values": {k: _s(_dec(v)) for k, v in values.items()}},
        sizing=size,
        leverage=leverage,
        hit_rates=hit_rates,
        time_stop_minutes=sizing.default_time_stop_minutes(asset_class, now, c),
        take_profit_split=sizing.split_take_profits(3, c),
        notes=notes,
    )
