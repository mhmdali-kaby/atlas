"""The Portfolio Manager's inputs from Postgres (brief §7.8, ATL-169): every open position with what
:func:`atlas_agents.pm.rules.evaluate` needs. Synchronous; run it with ``asyncio.to_thread``.

* position: the newest account sync's ``positions_snapshots`` (non-zero rows);
* current stop: the tightest live ``sl`` / ``oco`` algo order on the instrument (rule 3: the stop that
  rests on OKX), TP1 resting when a live ``oco`` / ``tp`` carries a take-profit;
* plan: the entry proposal behind the newest filled Atlas entry order on the instrument in the
  position's direction (None for an external position: rules 1-4 then do not run);
* ATR(14) and the last confirmed 1H swing on the protective side (ZigZag at the TA's threshold);
* events: economic events (they are not mapped to instruments, so all apply, as for the Devil's
  Advocate) and the earnings of a stock/ETF underlying, from now to ``event_lookahead_minutes``;
* funding: the newest ``funding_rates`` row;
* applied rules / pending: the Portfolio Manager's own proposals on the instrument since it opened.

``structure`` is None: no card says intact/weakening/broken yet (ATL-168's gap), so rule 5
(invalidation) cannot fire until the Technical Analyst reports it.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

import numpy as np
from atlas_quant import load_candles
from atlas_quant.grid import bar_delta
from atlas_quant.indicators import wilder_atr_values
from atlas_shared.db import (
    AlgoOrder,
    EarningsEvent,
    EconomicEvent,
    FundingRate,
    Instrument,
    Order,
    PositionSnapshot,
    Proposal,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.proposals import TakeProfit
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents import proposals as props
from atlas_agents.pm.rules import EventInfo, FundingInfo, PositionState, ProposalPlan, RuleSettings
from atlas_agents.ta.swings import zigzag
from atlas_agents.tools.account import _positions_at, _sync_instant
from atlas_agents.tools.framework import ToolContext

PORTFOLIO_MANAGER: Final = "portfolio_manager"
LIVE_ALGO_STATES: Final = ("live", "partially_effective")
PENDING_STATUSES: Final = (
    "draft",
    "under_review",
    "voting",
    "passed",
    "risk_checked",
    "awaiting_owner",
    "sent",
    "partially_filled",
)
"""A PM proposal in one of these is still on its way: the same kind is not proposed again."""
DEAD_STATUSES: Final = ("rejected", "cancelled", "expired", "advise_only")
EXECUTED_STATUSES: Final = ("sent", "partially_filled", "filled", "closed")


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["portfolio_manager"]["check"]
    return cfg


@dataclass(frozen=True)
class HeldPosition:
    """One open position and every rule input for it."""

    snapshot_id: uuid.UUID
    inst_id: str
    side: Literal["long", "short"]
    size: Decimal
    upl: Decimal | None
    state: PositionState
    plan: ProposalPlan | None
    atr_1h: Decimal | None
    last_swing: Decimal | None
    events: tuple[EventInfo, ...]
    funding: FundingInfo | None
    settings: RuleSettings
    pending_kinds: frozenset[str] = frozenset()
    taken_at: datetime | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def view(self) -> dict[str, Any]:
        """JSON-safe inputs for the model (decimals as strings)."""
        s = self.state

        def d(v: Decimal | None) -> str | None:
            return None if v is None else format(v, "f")

        return {
            "inst_id": self.inst_id,
            "side": self.side,
            "size_contracts": d(self.size),
            "unrealised_pnl_usd": d(self.upl),
            "entry_price": d(s.entry_price),
            "mark_price": d(s.mark_price),
            "current_stop": d(s.stop),
            "opened_at": s.opened_at.isoformat(),
            "tp1_resting": s.tp1_resting,
            "tp1_filled": s.tp1_filled,
            "original_stop": d(self.plan.stop) if self.plan else None,
            "take_profits": [
                {"price": d(t.price), "pct": d(t.pct)} for t in (self.plan.take_profits if self.plan else ())
            ],
            "time_stop_minutes": self.plan.time_stop_minutes if self.plan else None,
            "proposal_id": self.plan.proposal_id if self.plan else None,
            "atr_1h": d(self.atr_1h),
            "last_1h_swing": d(self.last_swing),
            "events": [
                {
                    "id": e.id,
                    "name": e.name,
                    "scheduled_at": e.scheduled_at.isoformat(),
                    "importance": e.importance,
                }
                for e in self.events
            ],
            "funding_rate": d(self.funding.rate) if self.funding else None,
            "as_of": self.taken_at.isoformat() if self.taken_at else None,
            "notes": list(self.notes),
        }


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        return None
    return out if out.is_finite() else None


def _current_stop(algos: Sequence[AlgoOrder], side: str) -> Decimal | None:
    stops: list[Decimal] = []
    for a in algos:
        px = a.sl_trigger_px if a.kind == "oco" else a.trigger_px if a.kind == "sl" else None
        if px is not None:
            stops.append(px)
    if not stops:
        return None
    return max(stops) if side == "long" else min(stops)


def _tp_resting(algos: Sequence[AlgoOrder]) -> bool:
    return any((a.kind == "oco" and a.tp_trigger_px is not None) or a.kind == "tp" for a in algos)


def _plan(session: Session, ctx: ToolContext, inst_id: str, side: str) -> ProposalPlan | None:
    order_side = "buy" if side == "long" else "sell"
    row = session.execute(
        select(Proposal)
        .join(Order, Order.proposal_id == Proposal.id)
        .where(
            Order.env == ctx.env,
            Order.inst_id == inst_id,
            Order.purpose == "entry",
            Order.origin == "atlas",
            Order.side == order_side,
            Order.filled_sz > 0,
            ctx.bound(Order.created_at),
        )
        .order_by(Order.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None or row.stop is None:
        return None
    tps: list[TakeProfit] = []
    for raw in row.take_profits or []:
        if isinstance(raw, Mapping) and _dec(raw.get("price")) and _dec(raw.get("pct")):
            tps.append(TakeProfit(price=Decimal(str(raw["price"])), pct=Decimal(str(raw["pct"]))))
    return ProposalPlan(
        stop=row.stop,
        take_profits=tuple(tps),
        time_stop_minutes=row.time_stop_minutes,
        proposal_id=str(row.id),
    )


def _opened_at(
    session: Session, ctx: ToolContext, snap: PositionSnapshot, plan: ProposalPlan | None
) -> datetime:
    if snap.c_time is not None:
        return snap.c_time
    if plan is not None and plan.proposal_id:
        created = session.scalar(
            select(Order.created_at)
            .where(Order.proposal_id == uuid.UUID(plan.proposal_id), Order.purpose == "entry")
            .order_by(Order.created_at)
            .limit(1)
        )
        if created is not None:
            return created
    return snap.taken_at


def _atr_and_swing(
    session: Session, ctx: ToolContext, inst_id: str, side: str, cfg: Mapping[str, Any]
) -> tuple[Decimal | None, Decimal | None]:
    bars, window = int(cfg["atr_lookback_bars"]), int(cfg["atr_window"])
    now = ctx.now()
    df = load_candles(session, inst_id, "1H", now - bar_delta("1H") * bars, now, as_of=now, env=ctx.env).df
    if df is None or len(df) < window:
        return None, None
    atr_arr = wilder_atr_values(df, window)
    last = float(atr_arr[-1])
    if not math.isfinite(last) or last <= 0:
        return None, None
    atr = Decimal(str(last))
    threshold = np.asarray(atr_arr * float(cfg["swing_atr_mult"]), dtype=np.float64)
    want = "low" if side == "long" else "high"
    swings = [s for s in zigzag(df, threshold) if s.kind == want]
    swing = Decimal(str(swings[-1].price)) if swings else None
    return atr, swing


def _events(
    session: Session, ctx: ToolContext, inst_id: str, cfg: Mapping[str, Any]
) -> tuple[EventInfo, ...]:
    now = ctx.now()
    end = now + timedelta(minutes=int(cfg["event_lookahead_minutes"]))
    out = [
        EventInfo(id=str(e.id), name=e.name, scheduled_at=e.scheduled_at, importance=int(e.importance))
        for e in session.scalars(
            select(EconomicEvent).where(
                EconomicEvent.scheduled_at >= now,
                EconomicEvent.scheduled_at <= end,
                ctx.bound(EconomicEvent.created_at),
            )
        ).all()
    ]
    inst = session.scalars(
        select(Instrument).where(Instrument.env == ctx.env, Instrument.inst_id == inst_id)
    ).first()
    if inst is not None and inst.asset_class in ("stock", "etf") and inst.base_ccy:
        importance = int(cfg["earnings_importance"])
        for e in session.scalars(
            select(EarningsEvent).where(
                EarningsEvent.symbol == inst.base_ccy,
                EarningsEvent.date >= now.date(),
                EarningsEvent.date <= end.date(),
                ctx.bound(EarningsEvent.created_at),
            )
        ).all():
            # Finnhub gives a session, not a time: bmo/amc/dmh map to fixed UTC hours in config.
            hour = int(
                dict(cfg["earnings_hours_utc"]).get(e.time or "", cfg["earnings_hours_utc"]["default"])
            )
            at = datetime.combine(e.date, time(hour, 0), tzinfo=now.tzinfo)
            if now <= at <= end:
                out.append(
                    EventInfo(
                        id=f"earnings:{e.id}",
                        name=f"{e.symbol} earnings",
                        scheduled_at=at,
                        importance=importance,
                    )
                )
    return tuple(sorted(out, key=lambda e: (e.scheduled_at, e.id)))


def _funding(session: Session, ctx: ToolContext, inst_id: str) -> FundingInfo | None:
    row = session.execute(
        select(FundingRate.funding_rate, FundingRate.ts)
        .where(FundingRate.env == ctx.env, FundingRate.inst_id == inst_id, ctx.bound(FundingRate.ts))
        .order_by(FundingRate.ts.desc())
        .limit(1)
    ).first()
    return None if row is None else FundingInfo(rate=row[0], as_of=row[1])


def _own_proposals(session: Session, ctx: ToolContext, inst_id: str, since: datetime) -> list[Proposal]:
    return list(
        session.scalars(
            select(Proposal).where(
                Proposal.env == ctx.env,
                Proposal.author == PORTFOLIO_MANAGER,
                Proposal.inst_id == inst_id,
                Proposal.created_at >= since,
                ctx.bound(Proposal.created_at),
            )
        ).all()
    )


def rule_settings(session: Session, ctx: ToolContext) -> RuleSettings:
    """agents.portfolio_manager.rules with the owner's settings-table override of
    close_before_high_impact_events (``data.agents.portfolio_manager``)."""
    data = props.settings_data(session, ctx).data
    agents = data.get("agents") if isinstance(data, Mapping) else None
    pm = agents.get(PORTFOLIO_MANAGER) if isinstance(agents, Mapping) else None
    overrides: dict[str, Any] = {}
    if isinstance(pm, Mapping) and isinstance(pm.get("close_before_high_impact_events"), bool):
        overrides["close_before_high_impact_events"] = pm["close_before_high_impact_events"]
    return RuleSettings.from_config(overrides)


def held_snapshots(session: Session, ctx: ToolContext) -> list[PositionSnapshot]:
    """Non-zero positions at the newest account sync."""
    taken_at = _sync_instant(session, ctx)
    return [p for p in _positions_at(session, ctx, taken_at) if p.pos != 0]


def held_inst_ids(session: Session, ctx: ToolContext) -> set[str]:
    return {p.inst_id for p in held_snapshots(session, ctx)}


def gather(
    session: Session, ctx: ToolContext, snap: PositionSnapshot, settings: RuleSettings
) -> HeldPosition:
    cfg = config()
    side = props.position_direction(snap)
    notes: list[str] = []
    algos = list(
        session.scalars(
            select(AlgoOrder).where(
                AlgoOrder.env == ctx.env,
                AlgoOrder.inst_id == snap.inst_id,
                AlgoOrder.state.in_(LIVE_ALGO_STATES),
            )
        ).all()
    )
    stop = _current_stop(algos, side)
    if stop is None:
        notes.append("no live stop on OKX for this position (reconciliation places one)")
    plan = _plan(session, ctx, snap.inst_id, side)
    if plan is None:
        notes.append("external position or no Atlas entry: R-based rules 1-4 do not run")
    opened = _opened_at(session, ctx, snap, plan)
    inst = session.scalars(
        select(Instrument).where(Instrument.env == ctx.env, Instrument.inst_id == snap.inst_id)
    ).first()
    own = _own_proposals(session, ctx, snap.inst_id, opened)
    applied = frozenset(p.invalidation for p in own if p.status not in DEAD_STATUSES and p.invalidation)
    tp1_filled = any(
        p.kind == "partial_close" and p.invalidation == "tp1_partial" and p.status in EXECUTED_STATUSES
        for p in own
    )
    pending = frozenset(p.kind for p in own if p.status in PENDING_STATUSES)
    atr, swing = _atr_and_swing(session, ctx, snap.inst_id, side, cfg)
    if atr is None:
        notes.append("no ATR(1H): trailing rules are skipped")
    mark = snap.mark_px if snap.mark_px is not None else snap.avg_px
    entry = snap.avg_px if snap.avg_px is not None else mark
    if mark is None or entry is None:
        raise ValueError(f"position {snap.inst_id} has no average or mark price")
    state = PositionState(
        inst_id=snap.inst_id,
        side=side,
        entry_price=entry,
        mark_price=mark,
        stop=stop,
        opened_at=opened,
        tick_sz=inst.tick_sz if inst is not None else None,
        tp1_filled=tp1_filled,
        tp1_resting=_tp_resting(algos),
        applied_rules=applied,
    )
    return HeldPosition(
        snapshot_id=snap.id,
        inst_id=snap.inst_id,
        side=side,
        size=abs(snap.pos),
        upl=snap.upl,
        state=state,
        plan=plan,
        atr_1h=atr,
        last_swing=swing,
        events=_events(session, ctx, snap.inst_id, cfg),
        funding=_funding(session, ctx, snap.inst_id),
        settings=settings,
        pending_kinds=pending,
        taken_at=snap.taken_at,
        notes=tuple(notes),
    )


def gather_all(session: Session, ctx: ToolContext) -> list[HeldPosition]:
    settings = rule_settings(session, ctx)
    out: list[HeldPosition] = []
    for snap in held_snapshots(session, ctx):
        if snap.avg_px is None and snap.mark_px is None:
            continue
        out.append(gather(session, ctx, snap, settings))
    return out


__all__ = [
    "PENDING_STATUSES",
    "HeldPosition",
    "config",
    "gather",
    "gather_all",
    "held_inst_ids",
    "held_snapshots",
    "rule_settings",
]
