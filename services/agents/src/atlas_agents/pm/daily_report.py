"""The Portfolio Manager's daily report (brief §7.8 "DAILY REPORT", ATL-170).

At ``agents.schedule.pm_daily_report`` (23:30 Baghdad) code computes the Baghdad day's figures
(:func:`compute`) from the synced OKX data, never from the model:

* equity start (the last equity point at or before 00:00 Baghdad, else the first one of the day) and
  end (the newest point), the change in USD and % (net deposits/withdrawals of the day taken out);
* realised PnL, fees and funding: sums over the day's ``bills`` (``trading_core.sync.bill_types``:
  realised = Σ ``pnl``, fees = Σ ``fee`` signed as OKX reports it, funding = Σ ``bal_chg`` of type 8);
  unrealised = the newest equity point's ``upl_usd``;
* closed trades: the day's ``positions_closed`` rows with instrument, side, R (the History API's
  formula: favourable move ÷ |entry - proposal stop|), USD, holding time and the closing rule;
* best and worst trade by USD;
* limit usage: the peak daily loss % over the day's equity points, the peak exposure (Σ |pos| x mark x
  ctVal per account sync) and the peak open-position count;
* rule breaches: a peak above the owner's limit is a breach row and raises a system alert
  (``system.alert`` on atlas.system plus a War Room ``system_alert``);
* tomorrow: the positions carried and the economic events of importance >= ``event_min_importance``
  in the next ``event_lookahead_hours``.

The PM model is then given the figures and writes the summary and one line each on the best and the
worst trade. :class:`ReportReviewer` takes only those notes from its card: every figure is code's,
whatever the model wrote, like the news-status fields. The message is a ``research_report`` whose card
``type`` is ``daily_report``; the report is stored as a ``research_reports`` row of kind
``daily_report``. A day with no trades posts with zeros. When the model is unavailable or its answer
fails twice, code posts the card with its own summary.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.db import (
    AlgoOrder,
    Bill,
    DepositWithdrawal,
    EconomicEvent,
    EquityPoint,
    Instrument,
    PositionClosed,
    PositionSnapshot,
    Proposal,
    ResearchReport,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from claude_agent_sdk.types import Message
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents import numguard
from atlas_agents import proposals as props
from atlas_agents.historian import split_card
from atlas_agents.pm import inputs as inp
from atlas_agents.pm import limits
from atlas_agents.pm.manage import run_context
from atlas_agents.registry import AgentHost, ScheduleFire
from atlas_agents.session import Review, RunResult
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.warroom import MAIN_THREAD, message_envelope

log = logging.getLogger(__name__)

SLUG: Final = "portfolio_manager"
JOB: Final = "pm_daily_report"
KIND: Final = "daily_report"
CARD_TYPE: Final = "daily_report"
MESSAGE_KIND: Final = "research_report"
WARROOM_STREAM: Final = "atlas.warroom"
SYSTEM_STREAM: Final = "atlas.system"
BREACH_CODE: Final = "limit_breach"

FUNDING_FEE_TYPE: Final = "8"
"""OKX bill type "Funding fee" (trading_core.sync.bill_types.FUNDING_FEE)."""
USD_LIKE: Final = limits.USD_LIKE
CENT: Final = Decimal("0.01")
HUNDRED: Final = Decimal(100)
ZERO: Final = Decimal(0)
EXECUTED: Final = ("sent", "partially_filled", "filled", "closed")
"""A PM close/partial_close in one of these statuses was sent to OKX (ATL-185)."""
CLOSE_TYPES: Final = {"3": "liquidation", "4": "liquidation", "5": "adl"}
"""OKX positions-history ``type``: 1 partial close, 2 close, 3 liquidation, 4 partial liquidation, 5 ADL."""
NOTE_FIELDS: Final = ("best_note", "worst_note")
"""The only card fields the model writes."""


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["portfolio_manager"]["daily_report"]
    return cfg


def _zone() -> ZoneInfo:
    return ZoneInfo(str(DEFAULTS["timezone"]["display"]))


def _usd(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def _pct(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def _plain(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _iso(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_window(day: date) -> tuple[datetime, datetime]:
    """[00:00, 24:00) of ``day`` in Asia/Baghdad, as UTC instants."""
    start = datetime(day.year, day.month, day.day, tzinfo=_zone())
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


# -- the figures ----------------------------------------------------------------------------------


@dataclass
class Report:
    day: date
    env: str
    card: dict[str, Any]
    breaches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def trades(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = self.card["trades"]
        return out


def _equity_start(session: Session, ctx: ToolContext, start: datetime, end: datetime) -> Decimal | None:
    before = session.scalar(
        select(EquityPoint.total_eq_usd)
        .where(EquityPoint.env == ctx.env, EquityPoint.ts <= start, ctx.bound(EquityPoint.ts))
        .order_by(EquityPoint.ts.desc())
        .limit(1)
    )
    if before is not None:
        return before
    return session.scalar(
        select(EquityPoint.total_eq_usd)
        .where(
            EquityPoint.env == ctx.env,
            EquityPoint.ts >= start,
            EquityPoint.ts < end,
            ctx.bound(EquityPoint.ts),
        )
        .order_by(EquityPoint.ts)
        .limit(1)
    )


def _equity_points(
    session: Session, ctx: ToolContext, start: datetime, end: datetime
) -> list[tuple[datetime, Decimal, Decimal | None]]:
    return [
        (ts, eq, upl)
        for ts, eq, upl in session.execute(
            select(EquityPoint.ts, EquityPoint.total_eq_usd, EquityPoint.upl_usd)
            .where(
                EquityPoint.env == ctx.env,
                EquityPoint.ts >= start,
                EquityPoint.ts < end,
                ctx.bound(EquityPoint.ts),
            )
            .order_by(EquityPoint.ts)
        ).all()
    ]


def _flows(
    session: Session, ctx: ToolContext, start: datetime, end: datetime
) -> list[tuple[datetime, Decimal]]:
    out: list[tuple[datetime, Decimal]] = []
    for kind, ccy, amount, ts in session.execute(
        select(DepositWithdrawal.kind, DepositWithdrawal.ccy, DepositWithdrawal.amount, DepositWithdrawal.ts)
        .where(
            DepositWithdrawal.env == ctx.env,
            DepositWithdrawal.ts >= start,
            DepositWithdrawal.ts < end,
            ctx.bound(DepositWithdrawal.ts),
        )
        .order_by(DepositWithdrawal.ts)
    ).all():
        if str(ccy).upper() in USD_LIKE:
            out.append((ts, amount if kind == "deposit" else -amount))
    return out


@dataclass
class BillSums:
    realised: Decimal = ZERO
    fees: Decimal = ZERO
    funding: Decimal = ZERO
    count: int = 0
    skipped_ccys: set[str] = field(default_factory=set)


def bill_sums(session: Session, ctx: ToolContext, start: datetime, end: datetime) -> BillSums:
    """Σ pnl, Σ fee and Σ funding bal_chg over the window's USD-like bills (OKX signs: a fee paid is
    negative, funding received is positive)."""
    sums = BillSums()
    for bill in session.scalars(
        select(Bill).where(Bill.env == ctx.env, Bill.ts >= start, Bill.ts < end, ctx.bound(Bill.ts))
    ):
        if str(bill.ccy).upper() not in USD_LIKE:
            sums.skipped_ccys.add(str(bill.ccy))
            continue
        sums.count += 1
        sums.realised += bill.pnl or ZERO
        sums.fees += bill.fee or ZERO
        if bill.type == FUNDING_FEE_TYPE:
            sums.funding += bill.bal_chg
    return sums


def r_multiple(
    side: str, entry: Decimal | None, exit_px: Decimal | None, stop: Decimal | None
) -> Decimal | None:
    """The History API's R (HistoryQuery::rMultiple): move in the trade's favour ÷ |entry - stop|."""
    if stop is None or entry is None or exit_px is None or side not in ("long", "short"):
        return None
    risk = abs(entry - stop)
    if risk == 0:
        return None
    move = exit_px - entry if side == "long" else entry - exit_px
    return (move / risk).quantize(CENT, rounding=ROUND_HALF_UP)


def closing_rule(session: Session, ctx: ToolContext, row: PositionClosed) -> str:
    """Why the position closed: an executed PM close/partial_close names its rule; else OKX's
    liquidation/ADL type; else the server-side stop or target that triggered; else ``manual``
    (the owner, or outside Atlas)."""
    grace = timedelta(minutes=int(config()["closing_rule_grace_minutes"]))
    since = row.opened_at or row.closed_at - timedelta(days=30)
    pm = session.scalars(
        select(Proposal)
        .where(
            Proposal.env == ctx.env,
            Proposal.author == SLUG,
            Proposal.inst_id == row.inst_id,
            Proposal.kind.in_(("close", "partial_close")),
            Proposal.status.in_(EXECUTED),
            Proposal.updated_at >= since,
            Proposal.updated_at <= row.closed_at + grace,
        )
        .order_by(Proposal.updated_at.desc())
        .limit(1)
    ).first()
    if pm is not None:
        return pm.invalidation or pm.kind
    if row.close_type in CLOSE_TYPES:
        return CLOSE_TYPES[row.close_type]
    algo = session.scalars(
        select(AlgoOrder)
        .where(
            AlgoOrder.env == ctx.env,
            AlgoOrder.inst_id == row.inst_id,
            AlgoOrder.state == "effective",
            AlgoOrder.updated_at >= since,
            AlgoOrder.updated_at <= row.closed_at + grace,
        )
        .order_by(AlgoOrder.updated_at.desc())
        .limit(1)
    ).first()
    if algo is not None:
        if algo.kind == "sl":
            return "stop_loss"
        if algo.kind == "tp":
            return "take_profit"
        if algo.kind == "oco" and row.exit_px is not None:
            sl, tp = algo.sl_trigger_px, algo.tp_trigger_px
            if sl is not None and tp is not None:
                return "stop_loss" if abs(row.exit_px - sl) <= abs(row.exit_px - tp) else "take_profit"
            return "stop_loss" if sl is not None else "take_profit"
    return "manual"


def _trade(session: Session, ctx: ToolContext, row: PositionClosed) -> dict[str, Any]:
    stop: Decimal | None = None
    entry = row.entry_px
    if row.proposal_id is not None:
        proposal = session.get(Proposal, row.proposal_id)
        if proposal is not None:
            stop = proposal.stop
            entry = entry if entry is not None else proposal.entry
    held = None
    if row.opened_at is not None:
        held = int((row.closed_at - row.opened_at).total_seconds() // 60)
    r = r_multiple(row.side, entry, row.exit_px, stop)
    pnl = row.realised_pnl if row.realised_pnl is not None else row.pnl
    return {
        "inst_id": row.inst_id,
        "side": row.side,
        "size": _plain(row.size),
        "entry": _plain(entry),
        "exit": _plain(row.exit_px),
        "r": _pct(r),
        "pnl_usd": _usd(pnl),
        "holding_minutes": held,
        "closing_rule": closing_rule(session, ctx, row),
        "opened_at": _iso(row.opened_at),
        "closed_at": _iso(row.closed_at),
        "proposal_id": str(row.proposal_id) if row.proposal_id else None,
        "origin": row.origin,
    }


def _exposure_peak(
    session: Session, ctx: ToolContext, start: datetime, end: datetime
) -> tuple[Decimal, int, datetime | None]:
    """(peak Σ |pos| x mark x ctVal, peak open-position count, when the exposure peaked) over the
    window's account syncs."""
    ct = {
        inst: (ct_val if ct_val is not None and ct_val > 0 else Decimal(1))
        for inst, ct_val in session.execute(
            select(Instrument.inst_id, Instrument.ct_val).where(Instrument.env == ctx.env)
        ).all()
    }
    by_sync: dict[datetime, tuple[Decimal, int]] = {}
    for snap in session.scalars(
        select(PositionSnapshot).where(
            PositionSnapshot.env == ctx.env,
            PositionSnapshot.taken_at >= start,
            PositionSnapshot.taken_at < end,
            ctx.bound(PositionSnapshot.taken_at),
        )
    ):
        notional, count = by_sync.get(snap.taken_at, (ZERO, 0))
        if snap.pos != 0:
            px = snap.mark_px if snap.mark_px is not None else (snap.avg_px or ZERO)
            notional += abs(snap.pos) * px * ct.get(snap.inst_id, Decimal(1))
            count += 1
        by_sync[snap.taken_at] = (notional, count)
    peak, peak_at, peak_count = ZERO, None, 0
    for at, (notional, count) in by_sync.items():
        if notional > peak:
            peak, peak_at = notional, at
        peak_count = max(peak_count, count)
    return peak, peak_count, peak_at


def _events(session: Session, ctx: ToolContext, now: datetime) -> list[dict[str, Any]]:
    cfg = config()
    until = now + timedelta(hours=int(cfg["event_lookahead_hours"]))
    rows = session.scalars(
        select(EconomicEvent)
        .where(
            EconomicEvent.scheduled_at > now,
            EconomicEvent.scheduled_at <= until,
            EconomicEvent.importance >= int(cfg["event_min_importance"]),
            ctx.bound(EconomicEvent.created_at),
        )
        .order_by(EconomicEvent.scheduled_at)
        .limit(int(cfg["max_events"]))
    ).all()
    return [
        {
            "name": e.name,
            "country": e.country,
            "importance": e.importance,
            "scheduled_at": _iso(e.scheduled_at),
            "forecast": e.forecast,
            "previous": e.previous,
        }
        for e in rows
    ]


def _carried(session: Session, ctx: ToolContext) -> list[dict[str, Any]]:
    return [
        {
            "inst_id": p.inst_id,
            "side": props.position_direction(p),
            "size": _plain(abs(p.pos)),
            "avg_px": _plain(p.avg_px),
            "mark_px": _plain(p.mark_px),
            "upl_usd": _usd(p.upl),
        }
        for p in sorted(inp.held_snapshots(session, ctx), key=lambda s: s.inst_id)
    ]


def compute(session: Session, ctx: ToolContext, day: date) -> Report:
    """Every figure of ``day``'s report (Baghdad day), up to ``ctx.now()``."""
    now = ctx.now()
    start, day_end = day_window(day)
    end = min(day_end, now + timedelta(microseconds=1))
    notes: list[str] = []

    eq_start = _equity_start(session, ctx, start, end)
    points = _equity_points(session, ctx, start, end)
    eq_end = points[-1][1] if points else eq_start
    upl = points[-1][2] if points else None
    flows = _flows(session, ctx, start, end)
    net_flows = sum((amount for _ts, amount in flows), ZERO)
    change = change_pct = None
    if eq_start is not None and eq_end is not None:
        change = eq_end - eq_start - net_flows
        change_pct = change / eq_start * HUNDRED if eq_start > 0 else None
    if eq_start is None:
        notes.append("no equity point synced for this day: the equity figures are empty")

    sums = bill_sums(session, ctx, start, end)
    if sums.skipped_ccys:
        notes.append(f"bills in {', '.join(sorted(sums.skipped_ccys))} are not in USD and are not summed")

    closed = session.scalars(
        select(PositionClosed)
        .where(
            PositionClosed.env == ctx.env,
            PositionClosed.closed_at >= start,
            PositionClosed.closed_at < end,
            ctx.bound(PositionClosed.closed_at),
        )
        .order_by(PositionClosed.closed_at)
    ).all()
    trades = [_trade(session, ctx, row) for row in closed]
    priced = [t for t in trades if t["pnl_usd"] is not None]
    best = max(priced, key=lambda t: Decimal(t["pnl_usd"]), default=None)
    worst = min(priced, key=lambda t: Decimal(t["pnl_usd"]), default=None)

    # Peak daily loss: the worst point of the day, deposits/withdrawals up to that point taken out.
    peak_loss: Decimal | None = None
    peak_loss_at: datetime | None = None
    if eq_start is not None and eq_start > 0:
        peak_loss = ZERO
        for ts, eq, _upl in points:
            flow = sum((a for at, a in flows if at <= ts), ZERO)
            loss = (eq_start + flow - eq) / eq_start * HUNDRED
            if loss > peak_loss:
                peak_loss, peak_loss_at = loss, ts
    exposure, peak_positions, exposure_at = _exposure_peak(session, ctx, start, end)
    exposure_pct = exposure / eq_start * HUNDRED if eq_start is not None and eq_start > 0 else None

    caps = limits.limit_values(session, ctx)
    breaches: list[dict[str, Any]] = []
    daily_cap = caps.get("max_daily_loss_pct")
    if peak_loss is not None and daily_cap is not None and peak_loss > daily_cap:
        breaches.append(
            {
                "limit": "max_daily_loss_pct",
                "used": _pct(peak_loss),
                "cap": _plain(daily_cap),
                "at": _iso(peak_loss_at),
            }
        )
    pos_cap = caps.get("max_open_positions")
    if pos_cap is not None and peak_positions > pos_cap:
        breaches.append(
            {"limit": "max_open_positions", "used": str(peak_positions), "cap": _plain(pos_cap), "at": None}
        )

    def pick(trade: dict[str, Any] | None) -> dict[str, Any] | None:
        if trade is None:
            return None
        keys = ("inst_id", "side", "r", "pnl_usd", "closing_rule")
        return {**{k: trade[k] for k in keys}, "note": None}

    card: dict[str, Any] = {
        "type": CARD_TYPE,
        "day": day.isoformat(),
        "env": ctx.env,
        "as_of": _iso(now),
        "window": {"start": _iso(start), "end": _iso(min(day_end, now))},
        "equity": {
            "start_usd": _usd(eq_start),
            "end_usd": _usd(eq_end),
            "net_flows_usd": _usd(net_flows),
            "change_usd": _usd(change),
            "change_pct": _pct(change_pct),
        },
        "pnl": {
            "realised_usd": _usd(sums.realised),
            "fees_usd": _usd(sums.fees),
            "funding_usd": _usd(sums.funding),
            "net_realised_usd": _usd(sums.realised + sums.fees + sums.funding),
            "unrealised_usd": _usd(upl),
            "bill_count": sums.count,
        },
        "trade_count": len(trades),
        "trades": trades,
        "best_trade": pick(best),
        "worst_trade": pick(worst) if worst is not best else None,
        "limits": {
            "peak_daily_loss_pct": _pct(peak_loss),
            "max_daily_loss_pct": _plain(daily_cap),
            "peak_exposure_usd": _usd(exposure),
            "peak_exposure_pct": _pct(exposure_pct),
            "peak_exposure_at": _iso(exposure_at),
            "peak_open_positions": peak_positions,
            "max_open_positions": _plain(pos_cap),
        },
        "breaches": breaches,
        "tomorrow": {"positions": _carried(session, ctx), "events": _events(session, ctx, now)},
        "notes": notes,
    }
    return Report(day=day, env=ctx.env, card=card, breaches=breaches)


# -- text -----------------------------------------------------------------------------------------


def _money(value: str | None) -> str:
    if value is None:
        return "n/a"
    d = Decimal(value)
    return f"{'+' if d > 0 else '-' if d < 0 else ''}${abs(d):,.2f}"


def code_summary(card: Mapping[str, Any]) -> str:
    """The report's text when the model does not write it: the headline figures only."""
    eq, pnl = card["equity"], card["pnl"]
    pct = f" ({eq['change_pct']}%)" if eq["change_pct"] is not None else ""
    lines = [
        f"**Daily report {card['day']}** ({card['env']}).",
        f"Equity {_money(eq['start_usd'])} → {_money(eq['end_usd'])}, "
        f"change {_money(eq['change_usd'])}{pct}.",
        f"Realised {_money(pnl['realised_usd'])}, unrealised {_money(pnl['unrealised_usd'])}, "
        f"fees {_money(pnl['fees_usd'])}, funding {_money(pnl['funding_usd'])}.",
        f"Trades closed: {card['trade_count']}." if card["trade_count"] else "No trades closed today.",
    ]
    if card["breaches"]:
        lines.append(f"**Rule breaches: {len(card['breaches'])}** (system alert raised).")
    lines.append(f"Positions carried: {len(card['tomorrow']['positions'])}.")
    return "\n".join(lines)


def prompt(card: Mapping[str, Any]) -> str:
    return (
        f"Write today's daily report ({card['day']}, Baghdad day, env {card['env']}).\n\n"
        "Every figure below was computed by code from OKX bills, fills and account syncs. Do not "
        "recompute, round differently or change any of them: code overwrites every figure in your card.\n\n"
        f"```json\n{json.dumps(card, indent=1)}\n```\n\n"
        "Answer with:\n"
        "1. A summary of at most 150 words: the equity change, realised vs unrealised, fees and funding, "
        "the closed trades, limit usage and any breach, and what to watch tomorrow (carried positions and "
        "events). Use only numbers from the figures above.\n"
        "2. Then exactly one fenced ```json block: "
        '{"best_note": "<one line on why the best trade worked, or null>", '
        '"worst_note": "<one line on why the worst trade lost, or null>"}. '
        "Both are null on a day with no closed trades."
    )


def _note(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    limit = int(config()["note_max_chars"])
    return text[:limit] if text else None


class ReportReviewer:
    """Accepts the model's summary and its two notes; the card is code's figures, whatever the model
    wrote (the model's other card fields are dropped). One follow-up when no JSON block came back."""

    def __init__(self, ctx: ToolContext, report: Report) -> None:
        self.ctx = ctx
        self.report = report
        self.card: dict[str, Any] | None = None
        self.content: str | None = None

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, model_card, error = split_card(text)
        if model_card is None:
            reason = error or "no JSON block"
            return Review(
                follow_up=(
                    f"Your answer was not accepted: {reason}. Send the summary again, then exactly one "
                    'fenced ```json block {"best_note": ..., "worst_note": ...}. This is your only retry.'
                ),
                failure=f"invalid daily report notes after retry: {reason}",
            )
        card = finalize(self.report.card, model_card)
        content = content or code_summary(card)
        plain = {k: v for k, v in card.items() if k != "meta"}
        outputs = [{"as_of": card["as_of"], "source": "atlas.daily_report_code", "data": self.report.card}]
        card["meta"] = numguard.check(content, plain, outputs).to_meta()
        self.card, self.content = card, content
        return Review(
            message={
                "kind": MESSAGE_KIND,
                "content_md": content,
                "card": card,
                "reply_to": None,
                "mentions": [],
                "thread": dict(MAIN_THREAD),
            }
        )


def finalize(code_card: Mapping[str, Any], model_card: Mapping[str, Any] | None) -> dict[str, Any]:
    """Code's card with the model's two notes put on the best and worst trade. Nothing else the
    model wrote survives: realised PnL, fees, equity, trades... are all code's."""
    card: dict[str, Any] = copy.deepcopy(dict(code_card))
    notes = {k: _note((model_card or {}).get(k)) for k in NOTE_FIELDS}
    if card.get("best_trade") is not None:
        card["best_trade"]["note"] = notes["best_note"]
    if card.get("worst_trade") is not None:
        card["worst_trade"]["note"] = notes["worst_note"]
    return card


# -- storage, alerts and the job ------------------------------------------------------------------


def already_reported(session: Session, ctx: ToolContext, day: date) -> bool:
    return (
        session.scalars(
            select(ResearchReport.id)
            .where(
                ResearchReport.env == ctx.env,
                ResearchReport.agent == SLUG,
                ResearchReport.kind == KIND,
                ResearchReport.data["day"].astext == day.isoformat(),
            )
            .limit(1)
        ).first()
        is not None
    )


def save(
    session: Session, ctx: ToolContext, card: Mapping[str, Any], content: str, run_id: uuid.UUID | None
) -> ResearchReport:
    stamp = ctx.clock()
    row = ResearchReport(
        env=ctx.env,
        agent=SLUG,
        agent_run_id=run_id,
        inst_id=None,
        kind=KIND,
        content_md=content,
        data=dict(card),
        confidence=None,
        valid_until=None,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    session.flush()
    return row


def breach_text(breach: Mapping[str, Any], day: date) -> str:
    label = limits.LABELS.get(str(breach["limit"]), str(breach["limit"]))
    if breach["limit"] == "max_open_positions":
        detail = f"{breach['used']} positions open against a limit of {breach['cap']}"
    else:
        detail = f"peak loss {breach['used']}% of equity against a {breach['cap']}% limit"
    return f"**Rule breach on {day.isoformat()}:** the {label} was exceeded ({detail})."


def publish_breaches(ctx: ToolContext, report: Report, correlation_id: str | None) -> None:
    """One ``system.alert`` on atlas.system and one War Room ``system_alert`` per breach."""
    for breach in report.breaches:
        detail = breach_text(breach, report.day)
        card = {"code": BREACH_CODE, "day": report.day.isoformat(), **breach}
        ctx.publish(
            SYSTEM_STREAM,
            EventEnvelope(
                type="system.alert",
                ts=ctx.now(),
                env=ctx.env,
                source=f"agent:{SLUG}",
                correlation_id=correlation_id,
                payload={"detail": detail, **card},
            ),
        )
        ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="system_alert", content_md=detail, card=card))


async def run(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    """The 23:30 job: compute, alert on breaches, let the model narrate, post, store."""
    ctx = run_context(host, fire.correlation_id)

    def load() -> Report | None:
        with ctx.session_factory() as session:
            if already_reported(session, ctx, fire.day):
                return None
            return compute(session, ctx, fire.day)

    report = await asyncio.to_thread(load)
    if report is None:
        return None
    if not host.replaying:
        publish_breaches(ctx, report, fire.correlation_id)
    reviewer = ReportReviewer(ctx, report)
    result: RunResult | None = None
    try:
        result = await host.run(
            SLUG,
            prompt(report.card),
            trigger="schedule",
            task="daily report",
            correlation_id=fire.correlation_id,
            reviewer=reviewer,
            max_follow_ups=int(config()["output_retries"]),
        )
    except LookupError:  # AgentUnavailableError: disabled or over budget
        log.info("portfolio_manager: not available; the daily report is posted by code")
    card, content = reviewer.card, reviewer.content
    if card is None or content is None or result is None or result.message is None:
        card = finalize(report.card, None)
        content = code_summary(card)
        ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind=MESSAGE_KIND, content_md=content, card=card))
    run_id = result.run_id if result is not None else None

    def store() -> None:
        with ctx.session_factory() as session:
            save(session, ctx, card, content, run_id)
            session.commit()

    await asyncio.to_thread(store)
    return result


__all__ = [
    "JOB",
    "KIND",
    "Report",
    "ReportReviewer",
    "bill_sums",
    "closing_rule",
    "code_summary",
    "compute",
    "day_window",
    "finalize",
    "prompt",
    "r_multiple",
    "run",
]
