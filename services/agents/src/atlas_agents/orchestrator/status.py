"""The "Where we are" card (brief §7.9 "KEEPING THE ROOM READABLE"; ATL-161).

Every ``where_we_are.every_minutes`` (120) from ``first`` (08:10) to ``last`` (23:00) Baghdad, the
Orchestrator posts one Main-thread card built entirely in code: watchlist status, open positions,
PnL today, budget left and the next scheduled step. A slot is posted once (in memory, and checked
against ``warroom_messages`` so a restart does not repeat it); after ``last`` nothing is posted.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.db import Cycle, EquityPoint, owner_day
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select, text

from atlas_agents.registry import AgentHost
from atlas_agents.tools import ToolContext
from atlas_agents.tools.account import get_positions, get_today_budget_left
from atlas_agents.tools.framework import NoInput
from atlas_agents.tools.warroom import WARROOM_STREAM, message_envelope
from atlas_agents.watchlists import current_watchlist, listed_ids

SLUG: Final = "orchestrator"
CARD_TYPE: Final = "where_we_are"

_posted: set[tuple[str, str]] = set()
"""(env, slot ISO) already posted by this process."""


def _zone(defaults: Mapping[str, Any] = DEFAULTS) -> ZoneInfo:
    return ZoneInfo(str(defaults["timezone"]["display"]))


def _hhmm(value: str) -> time:
    hours, minutes = str(value).split(":")
    return time(int(hours), int(minutes))


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def card_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["orchestrator"]["where_we_are"]
    return cfg


def due_slot(now: datetime, defaults: Mapping[str, Any] = DEFAULTS) -> datetime | None:
    """The card slot (UTC) due at ``now``: the latest ``first + k x every_minutes`` at or before
    ``now`` (Baghdad), while ``now`` is inside [first, last]. None outside that window."""
    cfg = card_config(defaults)
    zone = _zone(defaults)
    local = now.astimezone(zone)
    first = datetime.combine(local.date(), _hhmm(cfg["first"]), tzinfo=zone)
    last = datetime.combine(local.date(), _hhmm(cfg["last"]), tzinfo=zone)
    if local < first or local > last:
        return None
    every = timedelta(minutes=int(cfg["every_minutes"]))
    return (first + every * math.floor((local - first) / every)).astimezone(UTC)


def _posted_in_db(ctx: ToolContext, slot: datetime) -> bool:
    with ctx.session_factory() as session:
        row = session.execute(
            text(
                "SELECT 1 FROM warroom_messages WHERE env = :env AND author = :author "
                "AND card->>'type' = :type AND card->>'slot_at' = :slot LIMIT 1"
            ),
            {"env": ctx.env, "author": SLUG, "type": CARD_TYPE, "slot": _iso(slot)},
        ).first()
    return row is not None


def _money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else "\u2212" if value < 0 else ""  # U+2212 minus, as the UI shows it
    return f"{sign}${abs(value):,.2f}"


def _pnl_today(ctx: ToolContext) -> tuple[Decimal | None, Decimal | None]:
    """(USD change, % change) of equity since the first point of today (Baghdad)."""
    zone = _zone()
    day = owner_day(ctx.now())
    start = datetime.combine(day, time(0, 0), tzinfo=zone).astimezone(UTC)
    with ctx.session_factory() as session:
        first = session.scalars(
            select(EquityPoint)
            .where(EquityPoint.env == ctx.env, EquityPoint.ts >= start, ctx.bound(EquityPoint.ts))
            .order_by(EquityPoint.ts)
            .limit(1)
        ).first()
        last = session.scalars(
            select(EquityPoint)
            .where(EquityPoint.env == ctx.env, EquityPoint.ts >= start, ctx.bound(EquityPoint.ts))
            .order_by(EquityPoint.ts.desc())
            .limit(1)
        ).first()
    if first is None or last is None:
        return None, None
    change = last.total_eq_usd - first.total_eq_usd
    pct = (change / first.total_eq_usd * 100).quantize(Decimal("0.01")) if first.total_eq_usd else None
    return change, pct


def _cycle_state(ctx: ToolContext) -> str | None:
    with ctx.session_factory() as session:
        row = session.scalars(
            select(Cycle)
            .where(Cycle.env == ctx.env, Cycle.state != "cycle_closed", ctx.bound(Cycle.started_at))
            .order_by(Cycle.started_at.desc())
            .limit(1)
        ).first()
    return row.state if row is not None else None


def next_step(now: datetime, cycle_state: str | None, defaults: Mapping[str, Any] = DEFAULTS) -> str:
    """The next scheduled step as one phrase."""
    from atlas_agents.orchestrator.cycle import due_slot as cycle_slot

    if cycle_state is not None:
        return f"a cycle is running ({cycle_state.replace('_', ' ')})"
    sched = defaults["agents"]["schedule"]
    interval = timedelta(minutes=int(sched["analysis_interval_minutes"]))
    zone = _zone(defaults)
    current = cycle_slot(now, defaults)
    candidate = current + interval if current is not None else None
    local = now.astimezone(zone)
    first = datetime.combine(local.date(), _hhmm(sched["analysis_first_slot"]), tzinfo=zone)
    if candidate is None and local < first:
        candidate = first.astimezone(UTC)
    if candidate is not None and cycle_slot(candidate, defaults) == candidate:
        return f"analysis cycle at {candidate.astimezone(zone):%H:%M}"
    return f"analysis cycle tomorrow at {first:%H:%M}"


async def build(ctx: ToolContext, slot: datetime) -> tuple[str, dict[str, Any]]:
    """The card's text and JSON, from code only."""

    def reads() -> tuple[list[str], str | None, Decimal | None, Decimal | None, str | None]:
        with ctx.session_factory() as session:
            wl = current_watchlist(session, ctx, owner_day(ctx.now()))
            ids = listed_ids(session, wl)
        change, pct = _pnl_today(ctx)
        return ids, (wl.status if wl is not None else None), change, pct, _cycle_state(ctx)

    ids, wl_status, change, pct, state = await asyncio.to_thread(reads)
    positions = (await get_positions(ctx, NoInput())).data
    budget = (await get_today_budget_left(ctx, NoInput())).data
    open_rows = [
        {"inst_id": p.inst_id, "pos_side": p.pos_side, "upl": str(p.upl) if p.upl is not None else None}
        for p in positions.positions
    ]
    step = next_step(ctx.now(), state)
    local = slot.astimezone(_zone())
    watch = (
        f"watchlist {len(ids)} ({', '.join(ids[:5])}{'…' if len(ids) > 5 else ''})"
        if ids
        else "no watchlist yet"
    )
    pos = f"{len(open_rows)} open position{'s' if len(open_rows) != 1 else ''}" + (
        f" ({', '.join(r['inst_id'] for r in open_rows[:4])})" if open_rows else ""
    )
    pnl = f"PnL today {_money(change)}" + (
        f" ({'+' if pct and pct > 0 else ''}{pct}%)" if pct is not None else ""
    )
    left = f"daily loss budget left {_money(budget.daily_loss_left_usd).lstrip('+')}"
    slots = f", {budget.positions_left} position slot(s) left" if budget.positions_left is not None else ""
    content = f"Where we are ({local:%H:%M} Baghdad): {watch}; {pos}; {pnl}; {left}{slots}; next: {step}."
    card = {
        "type": CARD_TYPE,
        "slot_at": _iso(slot),
        "watchlist": {"status": wl_status, "inst_ids": ids},
        "open_positions": open_rows,
        "pnl_today_usd": str(change) if change is not None else None,
        "pnl_today_pct": str(pct) if pct is not None else None,
        "budget": {
            "daily_loss_left_usd": str(budget.daily_loss_left_usd)
            if budget.daily_loss_left_usd is not None
            else None,
            "positions_left": budget.positions_left,
        },
        "cycle_state": state,
        "next_step": step,
        "as_of": _iso(ctx.now()),
    }
    return content, card


async def post_if_due(host: AgentHost, now: datetime) -> datetime | None:
    """Post the card for the slot due at ``now`` unless it was posted; returns the slot posted."""
    slot = due_slot(now)
    if slot is None or host.replaying:
        return None
    key = (host.env, _iso(slot))
    if key in _posted:
        return None
    ctx = host.tool_context(SLUG)
    if await asyncio.to_thread(_posted_in_db, ctx, slot):
        _posted.add(key)
        return None
    _posted.add(key)
    content, card = await build(ctx, slot)
    host.publish(WARROOM_STREAM, message_envelope(ctx, kind="text", content_md=content, card=card))
    return slot


__all__ = ["CARD_TYPE", "build", "due_slot", "next_step", "post_if_due"]
