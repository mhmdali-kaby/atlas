"""The Portfolio Manager's post-mortems (brief §7.8 POST-MORTEM, decision #253 K, ATL-194).

Every ``agents.portfolio_manager.postmortem.every_seconds`` the ``pm_postmortem`` hook looks for full
closes of Atlas positions (``positions_closed`` rows with a ``proposal_id`` and close_type close,
liquidation or ADL) in the last ``lookback_hours`` that have no journal entry yet. A position is
identified by ``position_ref`` = ``"<pos_id>:<closed_at ISO>"``; the entry is unique on
(env, proposal_id, data.position_ref) (a partial unique index backs the check).

For each, code builds the facts (:func:`facts`), never the model:

* expected, from the opening proposal: entry, stop, take-profits, expected R, probability,
  invalidation, evidence ids;
* actual, from the fills: average entry and exit (size-weighted), size, gross PnL (Σ OKX fillPnl of
  the closing fills, or the price move x size x ctVal when OKX gave none), fees (Σ fill fees, OKX
  sign), funding (the positions-history funding fee), realised PnL USD = gross + fees + funding,
  initial risk USD = |avg entry - stop| x size x ctVal, realised R = realised PnL / initial risk,
  holding minutes, how it closed (:func:`close_reason`), and MFE/MAE in R over the holding window
  from the first ``candle_bars`` bar that has candles (MFE >= 0, MAE <= 0);
* the votes on the proposal.

The PM model then writes the prose only: what the team got right and wrong (naming agents), one
lesson phrased as a reusable rule, and the setup type / regime / catalyst tags from fixed
vocabularies. Outcome (win|loss|scratch by ``scratch_r``) and asset class are code's tags.
:class:`PostMortemReviewer` takes nothing else from the model; the number guard runs on its prose.

Code writes the ``journal_entries`` row (title, expected_md and actual_md are code text, lessons_md
is the lesson, tags ``setup:..``, ``asset:..``, ``regime:..``, ``catalyst:..``, ``outcome:..``) and a
War Room ``post_mortem`` card replying to the proposal's card where it is in the War Room.

When the model run fails (error, rate_limited, unavailable, invalid answer) the entry and the card
are still written, from the facts, with ``data.lesson_pending = true``. A later tick retries the
prose (up to ``lesson_attempts`` runs) and, on success, updates that same row and posts a second
``post_mortem`` card (``lesson_update: true``) with the lesson. Never a second row.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from atlas_shared.db import (
    AlgoOrder,
    Candle,
    Fill,
    Instrument,
    JournalEntry,
    Order,
    PositionClosed,
    Proposal,
    Vote,
)
from atlas_shared.defaults import DEFAULTS
from claude_agent_sdk.types import Message
from sqlalchemy import JSON, String, Uuid, func, select, table
from sqlalchemy import column as sa_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas_agents import numguard
from atlas_agents.historian import split_card
from atlas_agents.pm import limits
from atlas_agents.pm.manage import run_context
from atlas_agents.registry import AgentHost
from atlas_agents.session import Review, RunResult
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.warroom import MAIN_THREAD, message_envelope

log = logging.getLogger(__name__)

SLUG: Final = "portfolio_manager"
JOB: Final = "pm_postmortem"
CARD_TYPE: Final = "post_mortem"
MESSAGE_KIND: Final = "post_mortem"
WARROOM_STREAM: Final = "atlas.warroom"
SOURCE: Final = "atlas.postmortem_code"

FULL_CLOSE_TYPES: Final = {"2": "close", "3": "liquidation", "5": "adl"}
"""OKX positions-history ``type`` values that end a position (1 and 4 are partial)."""
EXECUTED: Final = ("sent", "partially_filled", "filled", "closed")
USD_LIKE: Final = limits.USD_LIKE
CENT: Final = Decimal("0.01")
ZERO: Final = Decimal(0)
ONE: Final = Decimal(1)
BAR_MINUTES: Final = {"1m": 1, "5m": 5, "15m": 15, "1H": 60, "4H": 240, "1D": 1440}
MODEL_FIELDS: Final = ("right_md", "wrong_md", "lesson", "setup_type", "regime", "catalyst_type")

_messages = table(
    "warroom_messages",
    sa_column("id", Uuid),
    sa_column("env", String),
    sa_column("thread_id", Uuid),
    sa_column("event_id", Uuid),
    sa_column("kind", String),
    sa_column("card", JSONB().with_variant(JSON(), "sqlite")),
    sa_column("created_at"),
)


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["portfolio_manager"]["postmortem"]
    return cfg


EVERY_SECONDS: Final = int(config()["every_seconds"])


def _iso(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _q(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def position_ref(row: PositionClosed) -> str:
    return f"{row.pos_id}:{_iso(row.closed_at)}"


# -- finding work ---------------------------------------------------------------------------------


def _existing(session: Session, ctx: ToolContext, proposal_id: uuid.UUID, ref: str) -> JournalEntry | None:
    return session.scalars(
        select(JournalEntry)
        .where(
            JournalEntry.env == ctx.env,
            JournalEntry.proposal_id == proposal_id,
            JournalEntry.data["position_ref"].astext == ref,
        )
        .limit(1)
    ).first()


def pending_closes(session: Session, ctx: ToolContext) -> list[PositionClosed]:
    """Full closes of Atlas positions in the lookback window with no post-mortem yet, oldest first."""
    since = ctx.now() - timedelta(hours=int(config()["lookback_hours"]))
    rows = session.scalars(
        select(PositionClosed)
        .where(
            PositionClosed.env == ctx.env,
            PositionClosed.proposal_id.is_not(None),
            PositionClosed.close_type.in_(tuple(FULL_CLOSE_TYPES)),
            PositionClosed.closed_at >= since,
            ctx.bound(PositionClosed.closed_at),
        )
        .order_by(PositionClosed.closed_at)
    ).all()
    out: list[PositionClosed] = []
    for row in rows:
        assert row.proposal_id is not None
        if _existing(session, ctx, row.proposal_id, position_ref(row)) is None:
            out.append(row)
    return out


def pending_lessons(session: Session, ctx: ToolContext) -> list[JournalEntry]:
    """Post-mortems written without a lesson that may still be retried."""
    attempts = int(config()["lesson_attempts"])
    rows = session.scalars(
        select(JournalEntry)
        .where(
            JournalEntry.env == ctx.env,
            JournalEntry.agent == SLUG,
            JournalEntry.data["lesson_pending"].as_boolean().is_(True),
        )
        .order_by(JournalEntry.created_at)
    ).all()
    return [r for r in rows if int((r.data or {}).get("lesson_attempts", 0)) < attempts]


# -- the facts ------------------------------------------------------------------------------------


@dataclass
class Facts:
    row_id: uuid.UUID
    proposal_id: uuid.UUID
    inst_id: str
    side: str
    position_ref: str
    asset_class: str
    setup_tag: str | None
    outcome: str
    card: dict[str, Any]
    """expected, actual, votes, deadline: the code half of the post-mortem card."""


def _wavg(fills: Sequence[Fill]) -> tuple[Decimal | None, Decimal]:
    size = sum((f.fill_sz for f in fills), ZERO)
    if size == 0:
        return None, ZERO
    return sum((f.fill_px * f.fill_sz for f in fills), ZERO) / size, size


def _fills(
    session: Session, ctx: ToolContext, row: PositionClosed, opening_side: str
) -> tuple[list[Fill], list[Fill]]:
    """(opening fills, closing fills). Opening = fills of the proposal's entry orders, else
    opening-side fills on the instrument around the open; closing = the other side, from the first
    opening fill to the close (each with ``fill_grace_minutes``)."""
    grace = timedelta(minutes=int(config()["fill_grace_minutes"]))
    opening = list(
        session.scalars(
            select(Fill)
            .join(Order, Fill.order_id == Order.id)
            .where(
                Fill.env == ctx.env,
                Order.proposal_id == row.proposal_id,
                Order.purpose == "entry",
                Fill.side == opening_side,
                Fill.ts <= row.closed_at + grace,
            )
            .order_by(Fill.ts)
        )
    )
    if not opening and row.opened_at is not None:
        opening = list(
            session.scalars(
                select(Fill)
                .where(
                    Fill.env == ctx.env,
                    Fill.inst_id == row.inst_id,
                    Fill.side == opening_side,
                    Fill.ts >= row.opened_at - grace,
                    Fill.ts <= row.closed_at,
                )
                .order_by(Fill.ts)
            )
        )
    start = opening[0].ts if opening else (row.opened_at or row.closed_at - timedelta(days=30))
    closing = list(
        session.scalars(
            select(Fill)
            .where(
                Fill.env == ctx.env,
                Fill.inst_id == row.inst_id,
                Fill.side != opening_side,
                Fill.ts >= start,
                Fill.ts <= row.closed_at + grace,
            )
            .order_by(Fill.ts)
        )
    )
    return opening, closing


def close_reason(session: Session, ctx: ToolContext, row: PositionClosed, opened_at: datetime) -> str:
    """How the position closed: ``liquidation``/``adl`` (OKX type); ``kill`` (a kill-switch order);
    the PM rule that closed it (an executed PM close, e.g. ``time_stop``); ``stop_loss`` or
    ``take_profit`` (a triggered server-side algo); ``manual`` (the owner's Close in Atlas);
    ``external`` (closed outside Atlas)."""
    kind = FULL_CLOSE_TYPES.get(row.close_type)
    if kind in ("liquidation", "adl"):
        return str(kind)
    grace = timedelta(minutes=int(config()["fill_grace_minutes"]))
    orders = session.scalars(
        select(Order).where(
            Order.env == ctx.env,
            Order.inst_id == row.inst_id,
            Order.purpose.in_(("close", "kill")),
            Order.filled_sz > 0,
            Order.updated_at >= opened_at,
            Order.updated_at <= row.closed_at + grace,
        )
    ).all()
    if any(o.purpose == "kill" for o in orders):
        return "kill"
    pm = session.scalars(
        select(Proposal)
        .where(
            Proposal.env == ctx.env,
            Proposal.author == SLUG,
            Proposal.inst_id == row.inst_id,
            Proposal.kind == "close",
            Proposal.status.in_(EXECUTED),
            Proposal.updated_at >= opened_at,
            Proposal.updated_at <= row.closed_at + grace,
        )
        .order_by(Proposal.updated_at.desc())
        .limit(1)
    ).first()
    if pm is not None:
        # A PM close proposal has no rule column: proposals.submit stores the PM's structured
        # rule label (guard.rule_label, e.g. "time_stop") in `invalidation` (proposals.py).
        return pm.invalidation or "pm_close"
    algo = session.scalars(
        select(AlgoOrder)
        .where(
            AlgoOrder.env == ctx.env,
            AlgoOrder.inst_id == row.inst_id,
            AlgoOrder.state == "effective",
            AlgoOrder.updated_at >= opened_at,
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
    if any(o.origin == "atlas" for o in orders):
        return "manual"
    return "external"


def excursions(
    session: Session,
    ctx: ToolContext,
    inst_id: str,
    side: str,
    entry: Decimal,
    risk_per_unit: Decimal,
    opened_at: datetime,
    closed_at: datetime,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """(MFE R >= 0, MAE R <= 0, bar used): the best and worst price over the candles that overlap
    [opened_at, closed_at), from the first configured bar that has any."""
    if risk_per_unit <= 0:
        return None, None, None
    for bar in config()["candle_bars"]:
        span = timedelta(minutes=BAR_MINUTES[str(bar)])
        hi, lo = session.execute(
            select(func.max(Candle.h), func.min(Candle.l)).where(
                Candle.env == ctx.env,
                Candle.inst_id == inst_id,
                Candle.bar == str(bar),
                Candle.ts > opened_at - span,
                Candle.ts < closed_at,
            )
        ).one()
        if hi is None or lo is None:
            continue
        if side == "long":
            mfe, mae = (hi - entry) / risk_per_unit, (lo - entry) / risk_per_unit
        else:
            mfe, mae = (entry - lo) / risk_per_unit, (entry - hi) / risk_per_unit
        return max(mfe, ZERO), min(mae, ZERO), str(bar)
    return None, None, None


def _votes(session: Session, proposal_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        {
            "agent": v.agent,
            "vote": v.vote,
            "confidence": _plain(v.confidence),
            "reason": v.reason,
        }
        for v in session.scalars(select(Vote).where(Vote.proposal_id == proposal_id).order_by(Vote.agent))
    ]


def agents_of(author: str | None, votes: list[dict[str, Any]]) -> list[str]:
    """Every agent slug that authored or voted on the proposal, sorted and de-duplicated
    (the Journal's agent filter reads ``data.agents``)."""
    slugs = {str(v["agent"]) for v in votes if v.get("agent")}
    if author:
        slugs.add(author)
    return sorted(slugs)


def outcome_of(r: Decimal | None, pnl: Decimal) -> str:
    scratch = Decimal(str(config()["scratch_r"]))
    if r is not None:
        return "win" if r >= scratch else "loss" if r <= -scratch else "scratch"
    return "win" if pnl > 0 else "loss" if pnl < 0 else "scratch"


def facts(session: Session, ctx: ToolContext, row: PositionClosed) -> Facts:
    """Every number of the post-mortem of ``row``, from the proposal, fills, candles and votes."""
    assert row.proposal_id is not None
    proposal = session.get(Proposal, row.proposal_id)
    inst = session.scalars(
        select(Instrument).where(Instrument.env == ctx.env, Instrument.inst_id == row.inst_id)
    ).first()
    ct_val = inst.ct_val if inst is not None and inst.ct_val is not None and inst.ct_val > 0 else ONE
    asset_class = inst.asset_class if inst is not None else "other"
    side = row.side
    direction = ONE if side == "long" else -ONE
    opening, closing = _fills(session, ctx, row, "buy" if side == "long" else "sell")

    avg_entry, open_size = _wavg(opening)
    avg_exit, close_size = _wavg(closing)
    notes: list[str] = []
    if avg_entry is None:
        avg_entry, open_size = row.entry_px, row.size
        notes.append("no opening fills recorded: entry and size are OKX positions-history figures")
    if avg_exit is None:
        avg_exit, close_size = row.exit_px, row.size
        notes.append("no closing fills recorded: exit is the OKX positions-history figure")
    size = open_size if open_size > 0 else row.size

    if closing and all(f.fill_pnl is not None for f in closing):
        gross = sum((f.fill_pnl or ZERO for f in closing), ZERO)
    elif avg_entry is not None and avg_exit is not None:
        gross = (avg_exit - avg_entry) * close_size * ct_val * direction
    else:
        gross = row.pnl or ZERO
    fees = ZERO
    for f in (*opening, *closing):
        if f.fee is None:
            continue
        if f.fee_ccy is not None and f.fee_ccy.upper() not in USD_LIKE:
            notes.append(f"a fee in {f.fee_ccy} is not in USD and is not summed")
            continue
        fees += f.fee
    funding = row.funding_fee or ZERO
    realised = gross + fees + funding

    stop = proposal.stop if proposal is not None else None
    risk_per_unit = abs(avg_entry - stop) if avg_entry is not None and stop is not None else None
    initial_risk = risk_per_unit * size * ct_val if risk_per_unit is not None else None
    realised_r = realised / initial_risk if initial_risk else None

    opened_at = row.opened_at or (opening[0].ts if opening else None) or row.closed_at
    held_minutes = int((row.closed_at - opened_at).total_seconds() // 60)
    mfe = mae = None
    bar = None
    if risk_per_unit and avg_entry is not None:
        mfe, mae, bar = excursions(
            session, ctx, row.inst_id, side, avg_entry, risk_per_unit, opened_at, row.closed_at
        )
    reason = close_reason(session, ctx, row, opened_at)
    outcome = outcome_of(realised_r, realised)
    deadline = row.closed_at + timedelta(minutes=int(config()["deadline_minutes"]))
    votes = _votes(session, row.proposal_id)

    expected = {
        "entry": _plain(proposal.entry) if proposal is not None else None,
        "entry_zone": proposal.entry_zone if proposal is not None else None,
        "stop": _plain(stop),
        "take_profits": list(proposal.take_profits or []) if proposal is not None else [],
        "expected_r": _plain(proposal.expected_r) if proposal is not None else None,
        "probability": _plain(proposal.probability) if proposal is not None else None,
        "invalidation": proposal.invalidation if proposal is not None else None,
        "evidence": list(proposal.evidence or []) if proposal is not None else [],
        "time_stop_minutes": proposal.time_stop_minutes if proposal is not None else None,
        "author": proposal.author if proposal is not None else None,
    }
    actual = {
        "avg_entry": _plain(avg_entry),
        "avg_exit": _plain(avg_exit),
        "size": _plain(size),
        "ct_val": _plain(ct_val),
        "gross_pnl_usd": _q(gross),
        "fees_usd": _q(fees),
        "funding_usd": _q(funding),
        "realised_pnl_usd": _q(realised),
        "okx_realised_pnl_usd": _q(row.realised_pnl),
        "initial_risk_usd": _q(initial_risk),
        "realised_r": _q(realised_r),
        "mfe_r": _q(mfe),
        "mae_r": _q(mae),
        "excursion_bar": bar,
        "opened_at": _iso(opened_at),
        "closed_at": _iso(row.closed_at),
        "holding_minutes": held_minutes,
        "close_reason": reason,
        "close_type": row.close_type,
        "fill_count": len(opening) + len(closing),
    }
    card: dict[str, Any] = {
        "type": CARD_TYPE,
        "env": ctx.env,
        "as_of": _iso(ctx.now()),
        "inst_id": row.inst_id,
        "side": side,
        "asset_class": asset_class,
        "setup_tag": row.setup_tag,
        "proposal_id": str(row.proposal_id),
        "position_ref": position_ref(row),
        "outcome": outcome,
        "expected": expected,
        "actual": actual,
        "votes": votes,
        "agents": agents_of(proposal.author if proposal is not None else None, votes),
        "deadline_at": _iso(deadline),
        "notes": notes,
    }
    return Facts(
        row_id=row.id,
        proposal_id=row.proposal_id,
        inst_id=row.inst_id,
        side=side,
        position_ref=position_ref(row),
        asset_class=asset_class,
        setup_tag=row.setup_tag,
        outcome=outcome,
        card=card,
    )


# -- text -----------------------------------------------------------------------------------------


def _money(value: str | None) -> str:
    if value is None:
        return "n/a"
    d = Decimal(value)
    return f"{'+' if d > 0 else '-' if d < 0 else ''}${abs(d):,.2f}"


def _r(value: str | None) -> str:
    return "n/a" if value is None else f"{'+' if Decimal(value) > 0 else ''}{value}R"


def title(card: Mapping[str, Any]) -> str:
    a = card["actual"]
    text = f"{card['inst_id']} {card['side']}: {_r(a['realised_r'])} {_money(a['realised_pnl_usd'])}"
    return f"{text} ({card['outcome']})"[:255]


def expected_md(card: Mapping[str, Any]) -> str:
    e = card["expected"]
    tps = ", ".join(
        f"{tp.get('price')} ({tp.get('pct', tp.get('close_pct', '?'))}%)"
        for tp in e["take_profits"]
        if isinstance(tp, Mapping)
    )
    lines = [
        f"- Entry: {e['entry'] or e['entry_zone'] or 'n/a'}; stop: {e['stop'] or 'n/a'}",
        f"- Take-profits: {tps or 'none'}",
        f"- Expected R: {e['expected_r'] or 'n/a'}; probability: {e['probability'] or 'n/a'}",
        f"- Invalidation: {e['invalidation'] or 'n/a'}",
    ]
    return "\n".join(lines)


def actual_md(card: Mapping[str, Any]) -> str:
    a = card["actual"]
    lines = [
        f"- Avg entry {a['avg_entry'] or 'n/a'} → avg exit {a['avg_exit'] or 'n/a'}, size {a['size']}",
        f"- Realised {_money(a['realised_pnl_usd'])} (gross {_money(a['gross_pnl_usd'])}, "
        f"fees {_money(a['fees_usd'])}, funding {_money(a['funding_usd'])}) = {_r(a['realised_r'])}",
        f"- MFE {_r(a['mfe_r'])}, MAE {_r(a['mae_r'])}",
        f"- Held {a['holding_minutes']} min; closed by {a['close_reason']}",
    ]
    return "\n".join(lines)


def code_summary(card: Mapping[str, Any]) -> str:
    a = card["actual"]
    return (
        f"**Post-mortem {card['inst_id']} {card['side']}** ({card['outcome']}): "
        f"{_r(a['realised_r'])}, {_money(a['realised_pnl_usd'])}, held {a['holding_minutes']} min, "
        f"closed by {a['close_reason']}. Lesson pending."
    )


def prompt(card: Mapping[str, Any]) -> str:
    cfg = config()
    return (
        f"Write the post-mortem of the closed {card['side']} on {card['inst_id']} (env {card['env']}).\n\n"
        "Every figure below was computed by code from the proposal, OKX fills and candles. Do not "
        "recompute or change any of them: code writes every number in the journal entry. Do not call "
        "memory.write_journal for this post-mortem: code saves it from your answer. You may read "
        "earlier entries with memory.read_journal and the War Room research with your other tools.\n\n"
        f"```json\n{json.dumps(card, indent=1)}\n```\n\n"
        "Answer with:\n"
        "1. A summary of at most 120 words: expected vs actual, and which agents' inputs mattered. "
        "Use only numbers from the figures above.\n"
        "2. Then exactly one fenced ```json block with these keys:\n"
        '{"right_md": "<what the team got right, naming the agents>", '
        '"wrong_md": "<what the team got wrong, naming the agents>", '
        '"lesson": "<ONE reusable rule, one sentence, starting with e.g. When / If / Never / Always>", '
        f'"setup_type": one of {json.dumps(list(cfg["setup_types"]))}, '
        f'"regime": one of {json.dumps(list(cfg["regimes"]))}, '
        f'"catalyst_type": one of {json.dumps(list(cfg["catalyst_types"]))}}}'
    )


# -- the model's half -----------------------------------------------------------------------------


def _clean(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit] if text else None


def validate_answer(model_card: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """(the accepted prose and tags, None) or (None, why it was refused)."""
    cfg = config()
    prose_max = int(cfg["prose_max_chars"])
    lesson_raw = model_card.get("lesson")
    if not isinstance(lesson_raw, str) or not lesson_raw.strip():
        return None, "lesson is missing"
    lines = [ln for ln in lesson_raw.strip().splitlines() if ln.strip()]
    if len(lines) != 1:
        return None, "lesson must be one rule on one line"
    lesson = " ".join(lines[0].split()).lstrip("-*• ").strip()
    if len(lesson) > int(cfg["lesson_max_chars"]):
        return None, f"lesson is longer than {cfg['lesson_max_chars']} characters"
    first = lesson.split(" ", 1)[0].lower().strip(",:;")
    prefixes = {str(p).lower() for p in cfg["lesson_prefixes"]}
    if first not in prefixes:
        return None, (
            "lesson is not phrased as a reusable rule: start it with one of " + ", ".join(sorted(prefixes))
        )
    out: dict[str, Any] = {"lesson": lesson}
    for key in ("right_md", "wrong_md"):
        text = _clean(model_card.get(key), prose_max)
        if text is None:
            return None, f"{key} is missing"
        out[key] = text
    for key, vocab in (
        ("setup_type", "setup_types"),
        ("regime", "regimes"),
        ("catalyst_type", "catalyst_types"),
    ):
        value = model_card.get(key)
        if not isinstance(value, str) or value not in {str(v) for v in cfg[vocab]}:
            return None, f"{key} must be one of {', '.join(str(v) for v in cfg[vocab])}"
        out[key] = value
    return out, None


def tags_of(facts_card: Mapping[str, Any], answer: Mapping[str, Any] | None) -> dict[str, str]:
    """The five tags. Outcome and asset class are always code's; setup type is the position's own
    setup tag when it has one, else the model's; regime and catalyst are the model's ("unknown"
    while the lesson is pending)."""
    answer = answer or {}
    setup = facts_card.get("setup_tag") or answer.get("setup_type") or "unknown"
    return {
        "setup": str(setup),
        "asset": str(facts_card["asset_class"]),
        "regime": str(answer.get("regime") or "unknown"),
        "catalyst": str(answer.get("catalyst_type") or "unknown"),
        "outcome": str(facts_card["outcome"]),
    }


def tag_list(tags: Mapping[str, str]) -> list[str]:
    """The prefixed tags plus the bare outcome (``win``/``loss``/``scratch``), which the Journal's
    ``?outcome=`` filter matches."""
    out = {f"{k}:{v}" for k, v in tags.items()}
    if tags.get("outcome"):
        out.add(tags["outcome"])
    return sorted(out)


def build_card(
    facts_card: Mapping[str, Any],
    answer: Mapping[str, Any] | None,
    journal_id: uuid.UUID,
    *,
    lesson_update: bool = False,
) -> dict[str, Any]:
    card: dict[str, Any] = copy.deepcopy(dict(facts_card))
    tags = tags_of(facts_card, answer)
    card.update(
        {
            "journal_id": str(journal_id),
            "review": {
                "right_md": (answer or {}).get("right_md"),
                "wrong_md": (answer or {}).get("wrong_md"),
            },
            "lesson": (answer or {}).get("lesson"),
            "lesson_pending": answer is None,
            "tags": tags,
            "tag_list": tag_list(tags),
            "lesson_update": lesson_update,
        }
    )
    return card


class PostMortemReviewer:
    """Accepts the model's prose and tags only; every number is code's. One follow-up when the
    answer block is missing or invalid."""

    def __init__(
        self,
        facts_card: Mapping[str, Any],
        journal_id: uuid.UUID,
        target: Mapping[str, Any],
        *,
        lesson_update: bool = False,
    ) -> None:
        self.facts_card = facts_card
        self.journal_id = journal_id
        self.target = target
        self.lesson_update = lesson_update
        self.answer: dict[str, Any] | None = None
        self.card: dict[str, Any] | None = None
        self.content: str | None = None

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, model_card, error = split_card(text)
        answer, why = (None, error or "no JSON block") if model_card is None else validate_answer(model_card)
        if answer is None:
            return Review(
                follow_up=(
                    f"Your answer was not accepted: {why}. Send the summary again, then exactly one "
                    "fenced ```json block {right_md, wrong_md, lesson, setup_type, regime, "
                    "catalyst_type}. This is your only retry."
                ),
                failure=f"invalid post-mortem after retry: {why}",
            )
        card = build_card(self.facts_card, answer, self.journal_id, lesson_update=self.lesson_update)
        content = content or code_summary(card).replace("Lesson pending.", "")
        content = f"{content}\n\n**Lesson:** {answer['lesson']}"
        prose = " ".join([content, answer["right_md"], answer["wrong_md"]])
        outputs = [{"as_of": card["as_of"], "source": SOURCE, "data": self.facts_card}]
        card["meta"] = numguard.check(prose, None, outputs).to_meta()
        self.answer, self.card, self.content = answer, card, content
        return Review(
            message={
                "kind": MESSAGE_KIND,
                "content_md": content,
                "card": card,
                "mentions": [],
                **self.target,
            }
        )


# -- storage and the War Room ---------------------------------------------------------------------


def proposal_target(session: Session, ctx: ToolContext, proposal_id: uuid.UUID) -> dict[str, Any]:
    """Where the card goes: a reply to the proposal's War Room card (in its thread) when it is
    there, else the main thread."""
    found = session.execute(
        select(_messages.c.id, _messages.c.thread_id)
        .where(
            _messages.c.env == ctx.env,
            _messages.c.kind == "proposal",
            _messages.c.card["proposal_id"].astext == str(proposal_id),
        )
        .order_by(_messages.c.created_at.desc())
        .limit(1)
    ).first()
    if found is None:
        return {"thread": dict(MAIN_THREAD), "reply_to": None}
    return {"thread_id": str(found.thread_id), "reply_to": str(found.id)}


def _message_row(session: Session, ctx: ToolContext, event_id: str | None) -> dict[str, Any] | None:
    if not event_id:
        return None
    found = session.execute(
        select(_messages.c.id, _messages.c.thread_id).where(
            _messages.c.env == ctx.env, _messages.c.event_id == event_id
        )
    ).first()
    return None if found is None else {"thread_id": str(found.thread_id), "reply_to": str(found.id)}


def entry_data(card: Mapping[str, Any], *, attempts: int, on_time: bool) -> dict[str, Any]:
    data = {k: v for k, v in card.items() if k not in ("type", "meta", "journal_id", "lesson_update")}
    data["lesson_attempts"] = attempts
    data["on_time"] = on_time
    if "meta" in card:
        data["numguard"] = card["meta"]
    return data


def write_entry(
    session: Session, ctx: ToolContext, journal_id: uuid.UUID, card: Mapping[str, Any], *, attempts: int
) -> JournalEntry | None:
    """Insert the post-mortem row; None when one already exists for this close (the unique index)."""
    now = ctx.now()
    deadline = datetime.fromisoformat(str(card["deadline_at"]).replace("Z", "+00:00"))
    row = JournalEntry(
        id=journal_id,
        env=ctx.env,
        agent=SLUG,
        inst_id=card["inst_id"],
        proposal_id=uuid.UUID(str(card["proposal_id"])),
        title=title(card),
        expected_md=expected_md(card),
        actual_md=actual_md(card),
        lessons_md=card.get("lesson"),
        tags=list(card["tag_list"]),
        data=entry_data(card, attempts=attempts, on_time=now <= deadline),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        log.info("portfolio_manager: a post-mortem of %s already exists", card["position_ref"])
        return None
    return row


async def _narrate(
    host: AgentHost,
    facts_card: Mapping[str, Any],
    journal_id: uuid.UUID,
    target: Mapping[str, Any],
    correlation_id: str,
    *,
    lesson_update: bool,
) -> tuple[PostMortemReviewer, RunResult | None]:
    reviewer = PostMortemReviewer(facts_card, journal_id, target, lesson_update=lesson_update)
    result: RunResult | None = None
    try:
        result = await host.run(
            SLUG,
            prompt(facts_card),
            trigger="schedule",
            task=f"post-mortem {facts_card['inst_id']}",
            correlation_id=correlation_id,
            reviewer=reviewer,
            max_follow_ups=int(config()["output_retries"]),
        )
    except LookupError:  # AgentUnavailableError: disabled or over budget
        log.info("portfolio_manager: not available; the post-mortem lesson stays pending")
    return reviewer, result


def _accepted(reviewer: PostMortemReviewer, result: RunResult | None) -> bool:
    return (
        result is not None
        and result.status == "ok"
        and result.message is not None
        and reviewer.answer is not None
        and reviewer.card is not None
    )


async def write_post_mortem(host: AgentHost, row: PositionClosed) -> JournalEntry | None:
    """The post-mortem of one close: facts, the model's prose, the entry and the card."""
    ctx = run_context(host)

    def load() -> tuple[Facts, dict[str, Any]] | None:
        with ctx.session_factory() as session:
            assert row.proposal_id is not None
            if _existing(session, ctx, row.proposal_id, position_ref(row)) is not None:
                return None
            fresh = session.get(PositionClosed, row.id)
            if fresh is None:
                return None
            f = facts(session, ctx, fresh)
            return f, proposal_target(session, ctx, f.proposal_id)

    loaded = await asyncio.to_thread(load)
    if loaded is None:
        return None
    f, target = loaded
    journal_id = uuid.uuid4()
    corr = f"pm:postmortem:{f.position_ref}"
    reviewer, result = await _narrate(host, f.card, journal_id, target, corr, lesson_update=False)
    accepted = _accepted(reviewer, result)
    card = reviewer.card if accepted and reviewer.card is not None else build_card(f.card, None, journal_id)
    event_id: str | None = None
    if not accepted:
        envelope = message_envelope(
            ctx,
            kind=MESSAGE_KIND,
            content_md=code_summary(card),
            card=card,
            thread=target.get("thread"),
            thread_id=target.get("thread_id"),
            reply_to=target.get("reply_to"),
        )
        ctx.publish(WARROOM_STREAM, envelope)
        event_id = str(envelope.id)

    def store() -> JournalEntry | None:
        with ctx.session_factory() as session:
            ran = result is not None and result.status not in ("rate_limited", "over_budget")
            entry = write_entry(session, ctx, journal_id, card, attempts=1 if ran else 0)
            if entry is not None and event_id is not None:
                entry.data = {**entry.data, "card_event_id": event_id}
                session.commit()
            if entry is not None:
                session.refresh(entry)
                session.expunge(entry)
            return entry

    return await asyncio.to_thread(store)


async def retry_lesson(host: AgentHost, entry: JournalEntry) -> bool:
    """One more model run for a pending lesson; on success the same row gets the prose and tags and
    a ``lesson_update`` card is posted. True when the lesson was filled."""
    ctx = run_context(host)
    data = dict(entry.data or {})
    facts_card = {
        k: data[k]
        for k in (
            "env",
            "as_of",
            "inst_id",
            "side",
            "asset_class",
            "setup_tag",
            "proposal_id",
            "position_ref",
            "outcome",
            "expected",
            "actual",
            "votes",
            "agents",
            "deadline_at",
            "notes",
        )
        if k in data
    }
    facts_card["type"] = CARD_TYPE
    facts_card["as_of"] = _iso(ctx.now())

    def target() -> dict[str, Any]:
        with ctx.session_factory() as session:
            first = _message_row(session, ctx, data.get("card_event_id"))
            if first is not None:
                return first
            assert entry.proposal_id is not None
            return proposal_target(session, ctx, entry.proposal_id)

    where = await asyncio.to_thread(target)
    corr = f"pm:postmortem:{data.get('position_ref')}:lesson:{int(data.get('lesson_attempts', 1)) + 1}"
    reviewer, result = await _narrate(host, facts_card, entry.id, where, corr, lesson_update=True)
    accepted = _accepted(reviewer, result)
    # A run that never reached the model (rate limited, over budget, disabled) is not an attempt.
    ran = result is not None and result.status not in ("rate_limited", "over_budget")

    def store() -> None:
        with ctx.session_factory() as session:
            row = session.get(JournalEntry, entry.id)
            if row is None:
                return
            current = dict(row.data or {})
            attempts = int(current.get("lesson_attempts", 1)) + (1 if ran else 0)
            if accepted and reviewer.card is not None:
                card = reviewer.card
                row.lessons_md = card["lesson"]
                row.tags = list(card["tag_list"])
                current.update(
                    {
                        "review": card["review"],
                        "lesson": card["lesson"],
                        "lesson_pending": False,
                        "tags": card["tags"],
                        "tag_list": card["tag_list"],
                        "numguard": card.get("meta"),
                    }
                )
            current["lesson_attempts"] = attempts
            row.data = current
            row.updated_at = ctx.now()
            session.commit()

    await asyncio.to_thread(store)
    return accepted


async def tick(host: AgentHost) -> list[JournalEntry]:
    """One hook run: post-mortem every new close, then retry pending lessons. Nothing in replay."""
    if host.replaying:
        return []
    ctx = run_context(host)

    def load() -> tuple[list[PositionClosed], list[JournalEntry]]:
        with ctx.session_factory() as session:
            closes = pending_closes(session, ctx)
            lessons = pending_lessons(session, ctx)
            for obj in (*closes, *lessons):
                session.expunge(obj)
            return closes, lessons

    closes, lessons = await asyncio.to_thread(load)
    written: list[JournalEntry] = []
    for close in closes:
        try:
            entry = await write_post_mortem(host, close)
        except Exception:
            log.exception("portfolio_manager: post-mortem of %s failed", close.inst_id)
            continue
        if entry is not None:
            written.append(entry)
    for pending in lessons:
        try:
            await retry_lesson(host, pending)
        except Exception:
            log.exception("portfolio_manager: post-mortem lesson retry for %s failed", pending.inst_id)
    return written


__all__ = [
    "CARD_TYPE",
    "EVERY_SECONDS",
    "JOB",
    "Facts",
    "PostMortemReviewer",
    "build_card",
    "close_reason",
    "excursions",
    "facts",
    "outcome_of",
    "pending_closes",
    "pending_lessons",
    "position_ref",
    "retry_lesson",
    "tags_of",
    "tick",
    "validate_answer",
    "write_post_mortem",
]
