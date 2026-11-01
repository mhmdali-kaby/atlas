"""What happened to each entry proposal of a day, taken or paper (ATL-196): shared by the Strategist
and Devil's Advocate scorers.

* **Taken** = an Atlas entry order on any version of the chain has a filled size. Realised R is the
  sum of ``data.actual.realised_r`` of the Portfolio Manager's post-mortems for the chain (ATL-194);
  without one, the full closes (``close_type`` 2/3/5) in ``positions_closed`` give
  sum(realised_pnl) / sum(|entry_px - stop| x size x ctVal). Neither = still open (pending).
  TP1-before-stop is walked on candles from the first entry fill at the order's average price, with
  the same rules as a paper trade.
* **Paper** = :mod:`atlas_agents.scorecards.paper` (filled: its realised_r and tp1_hit; not filled:
  no outcome).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.db import Fill, Instrument, JournalEntry, Order, PositionClosed, Proposal
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents.scorecards import paper

FULL_CLOSE_TYPES: Final = ("2", "3", "5")


@dataclass(frozen=True)
class Outcome:
    proposal_id: uuid.UUID
    """The head (latest version)."""
    kind: str
    """taken | paper | not_filled | pending | none (still open or never reached execution)"""
    realised_r: Decimal | None = None
    tp1_hit: bool | None = None
    source: str | None = None
    """journal | positions_closed | paper"""


def day_bounds(day: date) -> tuple[datetime, datetime]:
    zone = ZoneInfo(str(DEFAULTS["timezone"]["display"]))
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    return start, start + timedelta(days=1)


def _journal_r(session: Session, env: str, ids: Sequence[uuid.UUID]) -> Decimal | None:
    rows = session.scalars(
        select(JournalEntry.data).where(
            JournalEntry.env == env,
            JournalEntry.agent == "portfolio_manager",
            JournalEntry.proposal_id.in_(ids),
        )
    ).all()
    values: list[Decimal] = []
    for data in rows:
        actual = data.get("actual") if isinstance(data, dict) else None
        r = paper._dec(actual.get("realised_r")) if isinstance(actual, dict) else None
        if r is not None:
            values.append(r)
    return sum(values, Decimal(0)) if values else None


def _closed_r(
    session: Session, env: str, inst_id: str, ids: Sequence[uuid.UUID], stop: Decimal
) -> Decimal | None:
    rows = session.scalars(
        select(PositionClosed).where(
            PositionClosed.env == env,
            PositionClosed.proposal_id.in_(ids),
            PositionClosed.close_type.in_(FULL_CLOSE_TYPES),
        )
    ).all()
    if not rows:
        return None
    ct_val = session.scalar(
        select(Instrument.ct_val).where(Instrument.env == env, Instrument.inst_id == inst_id)
    )
    mult = Decimal(ct_val) if ct_val is not None else Decimal(1)
    pnl = Decimal(0)
    risk = Decimal(0)
    for row in rows:
        if row.entry_px is None or row.realised_pnl is None:
            return None
        pnl += Decimal(row.realised_pnl)
        risk += abs(Decimal(row.entry_px) - stop) * Decimal(row.size) * mult
    return paper._q(pnl / risk) if risk > 0 else None


def _taken_tp1(session: Session, p: Proposal, ids: Sequence[uuid.UUID], now: datetime) -> bool | None:
    order = session.scalars(
        select(Order)
        .where(
            Order.proposal_id.in_(ids), Order.purpose == "entry", Order.origin == "atlas", Order.filled_sz > 0
        )
        .order_by(Order.created_at)
        .limit(1)
    ).first()
    if order is None or order.avg_px is None or p.stop is None or p.side not in ("buy", "sell"):
        return None
    first_fill = session.scalar(select(Fill.ts).where(Fill.order_id == order.id).order_by(Fill.ts).limit(1))
    started = first_fill or order.created_at
    if started is None:
        return None
    tps = paper.take_profits(p)
    if not tps:
        return None
    max_hold = timedelta(hours=int(paper.paper_config()["max_hold_hours"]))
    hold = timedelta(minutes=p.time_stop_minutes) if p.time_stop_minutes else max_hold
    bars, _bar = paper.load_bars(session, p.env, p.inst_id, started, started + hold + timedelta(days=1), now)
    start_idx = next((k for k, b in enumerate(bars) if b.end > started), None)
    if start_idx is None:
        return None
    entry = Decimal(order.avg_px)
    if entry == Decimal(p.stop):
        return None
    walk = paper.walk_exits(
        bars,
        start_idx,
        side=p.side,
        entry=entry,
        stop=Decimal(p.stop),
        take_profits=tps,
        end=bars[start_idx].ts + hold,
        end_reason="horizon",
        now=now,
        first_bar_stop_only=True,
    )
    return None if walk is None else walk.tp1_hit


def day_outcomes(
    session: Session, env: str, day: date, now: datetime
) -> tuple[list[Proposal], dict[uuid.UUID, Outcome]]:
    """The Strategist's entry proposals of Baghdad ``day`` (latest versions) and each one's outcome.
    Paper trades of the day are (re)simulated and upserted first."""
    start, end = day_bounds(day)
    papers = paper.ensure_paper_trades(session, env, start, end, now)
    statuses = {str(s) for s in paper.paper_config()["statuses"]}
    proposals = paper.heads(session, env, start, end)
    out: dict[uuid.UUID, Outcome] = {}
    for p in proposals:
        ids = paper.chain_ids(session, p)
        if paper.entry_filled(session, ids):
            stop = Decimal(p.stop) if p.stop is not None else None
            r = _journal_r(session, env, ids)
            source = "journal"
            if r is None and stop is not None:
                r, source = _closed_r(session, env, p.inst_id, ids, stop), "positions_closed"
            tp1 = _taken_tp1(session, p, ids, now)
            out[p.id] = Outcome(
                p.id, "taken" if r is not None else "pending", r, tp1, source if r is not None else None
            )
        elif p.id in papers:
            res = papers[p.id]
            if res.status == "filled":
                out[p.id] = Outcome(p.id, "paper", res.realised_r, res.tp1_hit, "paper")
            else:
                out[p.id] = Outcome(p.id, "not_filled", source="paper")
        elif p.status in statuses:
            out[p.id] = Outcome(p.id, "pending")
        else:
            out[p.id] = Outcome(p.id, "none")
    return proposals, out


def ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summary_ids(outcomes: dict[uuid.UUID, Outcome], kind: str) -> list[str]:
    return [str(k) for k, o in outcomes.items() if o.kind == kind]


def as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def lookback_days(cfg: dict[str, Any] | Any, today: date) -> list[date]:
    days = int(cfg["lookback_days"])
    return [today - timedelta(days=back) for back in range(days, 0, -1)]


__all__ = ["Outcome", "day_bounds", "day_outcomes", "lookback_days", "mean", "ratio"]
