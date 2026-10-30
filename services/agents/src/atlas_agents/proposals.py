"""Proposals in code: the Strategist's §7.6 checklist, saving, revising, withdrawing and votes (ATL-153).

``warroom.submit_proposal`` and ``warroom.cast_vote`` (atlas_agents.tools.warroom) call into this
module; it validates and writes Postgres, and the tool publishes the War Room card and the
``atlas.proposals`` event. Nothing here can place an order: a saved proposal goes to the vote and
then the Risk Engine (rule 1).

The six checks for an entry (brief §7.6), every one evaluated so the agent sees every failure:

1. ``on_watchlist`` — the instrument is on today's (Asia/Baghdad) current watchlist, owner pins
   included, and the owner has not removed it.
2. ``technical_setup`` — the newest TechnicalSetup card for the instrument exists, its setup_type is
   not ``none`` and it was posted less than ``setup_max_age_minutes`` ago.
3. ``news_and_events`` — no CONFIRMED story (status from code, decision #247 (4)) published in the
   last ``news_lookback_hours`` whose mean sentiment opposes the side by ``contradict_sentiment``, and
   no economic event of importance >= ``blackout_importance`` in the next ``event_blackout_minutes``.
4. ``historian_verdict`` — the newest Historian history_verdict card for the instrument is not
   ``contradicts`` (no verdict passes: §7.6 only forbids a contradiction).
5. ``expectancy`` — expected R >= the owner's ``min_expected_r`` and p x R - (1 - p) >
   ``min_expectancy``.
6. ``no_duplicate`` — no open position and no pending proposal on the same instrument and direction.

Thresholds are in config/defaults.yaml ``agents.strategist.checklist``; min R comes only from the
settings table (rule 2).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

from atlas_shared.db import (
    Cycle,
    EconomicEvent,
    NewsItem,
    NewsSource,
    PositionSnapshot,
    Proposal,
    Vote,
    Watchlist,
    WatchlistItem,
    owner_day,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.proposals import (
    POSITION_ACTION_KINDS,
    PositionAction,
    TradeProposal,
    validate_position_action,
)
from pydantic import BaseModel, ValidationError
from sqlalchemy import ColumnElement, String, column, or_, select, table
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, insert
from sqlalchemy.orm import Session

from atlas_agents.tools.account import _positions_at, _settings, _settings_history, _sync_instant
from atlas_agents.tools.framework import ToolContext, ToolError
from atlas_agents.tools.news_status import decide_status

STRATEGIST: Final = "strategist"
PORTFOLIO_MANAGER: Final = "portfolio_manager"

ItemName = Literal[
    "on_watchlist", "technical_setup", "news_and_events", "historian_verdict", "expectancy", "no_duplicate"
]

_messages = table(
    "warroom_messages",
    column("id"),
    column("env", String),
    column("author", String),
    column("kind", String),
    column("card", JSONB),
    column("created_at", TIMESTAMP(timezone=True)),
)


def checklist_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["strategist"]["checklist"]
    return config


class ChecklistItem(BaseModel):
    item: int
    name: ItemName
    passed: bool
    detail: str


@dataclass(frozen=True)
class SettingsData:
    version: int | None
    data: Mapping[str, Any]


# -- reads ---------------------------------------------------------------------------------------------


def settings_data(session: Session, ctx: ToolContext) -> SettingsData:
    """The settings row in force at ``ctx.now()`` (settings_history in replay); empty when none."""
    if ctx.replaying:
        row = session.execute(
            select(_settings_history.c.version, _settings_history.c.data)
            .join(_settings, _settings.c.id == _settings_history.c.settings_id)
            .where(_settings.c.env == ctx.env, ctx.bound(_settings_history.c.created_at))
            .order_by(_settings_history.c.version.desc())
            .limit(1)
        ).first()
    else:
        row = session.execute(
            select(_settings.c.version, _settings.c.data).where(_settings.c.env == ctx.env)
        ).first()
    if row is None:
        return SettingsData(version=None, data={})
    version, data = row
    return SettingsData(version=int(version), data=data if isinstance(data, dict) else {})


@dataclass(frozen=True)
class LatestCard:
    """The newest research_report card found by :func:`latest_card`."""

    card: dict[str, Any]
    posted_at: datetime
    message_id: str


def latest_card(
    session: Session,
    ctx: ToolContext,
    *,
    author: str,
    card_type: str,
    inst_id: str,
    inst_ids_field: bool = False,
) -> LatestCard | None:
    """``author``'s newest research_report card of ``card_type`` on ``inst_id``.

    With ``inst_ids_field`` the card names its instruments in an ``inst_ids`` list (news_brief)
    rather than one ``inst_id``."""
    names_inst = (
        _messages.c.card["inst_ids"].op("?")(inst_id)
        if inst_ids_field
        else _messages.c.card["inst_id"].astext == inst_id
    )
    row = session.execute(
        select(_messages.c.id, _messages.c.card, _messages.c.created_at)
        .where(
            _messages.c.env == ctx.env,
            _messages.c.author == author,
            _messages.c.kind == "research_report",
            _messages.c.card["type"].astext == card_type,
            names_inst,
            ctx.bound(_messages.c.created_at),
        )
        .order_by(_messages.c.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    msg_id, card, posted = row
    return LatestCard(card=dict(card), posted_at=posted, message_id=str(msg_id))


def open_positions(session: Session, ctx: ToolContext, inst_id: str) -> list[PositionSnapshot]:
    """Non-zero positions on ``inst_id`` at the last account sync."""
    taken_at = _sync_instant(session, ctx)
    return [p for p in _positions_at(session, ctx, taken_at) if p.inst_id == inst_id and p.pos != 0]


def position_direction(position: PositionSnapshot) -> Literal["long", "short"]:
    if position.pos_side in ("long", "short"):
        return "long" if position.pos_side == "long" else "short"
    return "long" if position.pos > 0 else "short"


# -- the six checks ------------------------------------------------------------------------------------


def _check_watchlist(session: Session, ctx: ToolContext, inst_id: str) -> ChecklistItem:
    day = owner_day(ctx.now())
    wl = session.scalars(
        select(Watchlist)
        .where(Watchlist.env == ctx.env, Watchlist.date == day, ctx.bound(Watchlist.created_at))
        .order_by(Watchlist.version.desc())
        .limit(1)
    ).first()
    if wl is None:
        return ChecklistItem(
            item=1, name="on_watchlist", passed=False, detail=f"there is no watchlist for {day.isoformat()}"
        )
    not_removed: ColumnElement[bool] = WatchlistItem.removed_by_owner_at.is_(None)
    if ctx.replay_as_of is not None:
        not_removed = or_(not_removed, WatchlistItem.removed_by_owner_at > ctx.replay_as_of)
    item = session.scalars(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == wl.id, WatchlistItem.inst_id == inst_id, not_removed
        )
    ).first()
    if item is None:
        return ChecklistItem(
            item=1,
            name="on_watchlist",
            passed=False,
            detail=f"{inst_id} is not on the {day.isoformat()} watchlist (v{wl.version}) and not pinned "
            "by the owner",
        )
    how = "pinned by the owner" if item.pinned_by_owner else f"rank {item.rank}"
    return ChecklistItem(
        item=1, name="on_watchlist", passed=True, detail=f"on the {day.isoformat()} watchlist ({how})"
    )


def _check_setup(session: Session, ctx: ToolContext, inst_id: str, cfg: Mapping[str, Any]) -> ChecklistItem:
    max_age = timedelta(minutes=int(cfg["setup_max_age_minutes"]))
    found = latest_card(
        session, ctx, author="technical_analyst", card_type="technical_setup", inst_id=inst_id
    )
    if found is None:
        return ChecklistItem(
            item=2, name="technical_setup", passed=False, detail=f"no TechnicalSetup card for {inst_id}"
        )
    card, posted = found.card, found.posted_at
    age = ctx.now() - posted
    minutes = int(age.total_seconds() // 60)
    if card.get("setup_type") == "none":
        return ChecklistItem(
            item=2,
            name="technical_setup",
            passed=False,
            detail=f"the newest TechnicalSetup ({minutes} min old) says no setup",
        )
    if age >= max_age:
        return ChecklistItem(
            item=2,
            name="technical_setup",
            passed=False,
            detail=f"the newest TechnicalSetup is {minutes} min old; it must be under "
            f"{int(cfg['setup_max_age_minutes'])} min",
        )
    return ChecklistItem(
        item=2,
        name="technical_setup",
        passed=True,
        detail=f"{card.get('setup_type')} {card.get('direction')} setup, {minutes} min old",
    )


def _blackout_minutes(settings: SettingsData, cfg: Mapping[str, Any]) -> int:
    value = settings.data.get("event_blackout_minutes")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return int(cfg["event_blackout_minutes"])


def _check_news_and_events(
    session: Session,
    ctx: ToolContext,
    proposal: TradeProposal,
    settings: SettingsData,
    cfg: Mapping[str, Any],
) -> ChecklistItem:
    now = ctx.now()
    problems: list[str] = []
    since = now - timedelta(hours=int(cfg["news_lookback_hours"]))
    threshold = Decimal(str(cfg["contradict_sentiment"]))
    items = session.execute(
        select(NewsItem.id, NewsItem.cluster_id, NewsItem.source, NewsItem.sentiment, NewsItem.title).where(
            NewsItem.instruments.contains([proposal.inst_id]),
            NewsItem.published_at >= since,
            ctx.bound(NewsItem.published_at),
            ctx.bound(NewsItem.fetched_at),
        )
    ).all()
    stories: dict[str, list[Any]] = {}
    for row in items:
        key = str(row.cluster_id) if row.cluster_id is not None else f"item:{row.id}"
        stories.setdefault(key, []).append(row)
    cluster_ids = [uuid.UUID(k) for k in stories if not k.startswith("item:")]
    sources: dict[str, set[str]] = {k: {r.source for r in rows} for k, rows in stories.items()}
    if cluster_ids:
        for cid, source in session.execute(
            select(NewsItem.cluster_id, NewsItem.source).where(
                NewsItem.cluster_id.in_(cluster_ids),
                ctx.bound(NewsItem.published_at),
                ctx.bound(NewsItem.fetched_at),
            )
        ).all():
            sources[str(cid)].add(source)
    keys = sorted({s for group in sources.values() for s in group})
    trust = {
        key: (bool(reputable), bool(official))
        for key, reputable, official in session.execute(
            select(NewsSource.key, NewsSource.reputable, NewsSource.official).where(NewsSource.key.in_(keys))
        ).all()
    }
    for key, rows in stories.items():
        scores = [Decimal(r.sentiment) for r in rows if r.sentiment is not None]
        if not scores:
            continue
        mean = sum(scores, Decimal(0)) / len(scores)
        opposes = mean <= -threshold if proposal.side == "buy" else mean >= threshold
        if not opposes:
            continue
        if decide_status(sources[key], trust).status != "CONFIRMED":
            continue
        story = key.removeprefix("item:")
        problems.append(
            f"CONFIRMED story {story} ({rows[0].title[:80]!r}) has sentiment "
            f"{mean.quantize(Decimal('0.01'))}, against a {proposal.side}"
        )
    minutes = _blackout_minutes(settings, cfg)
    importance = int(cfg["blackout_importance"])
    if minutes > 0:
        events = session.execute(
            select(EconomicEvent.name, EconomicEvent.country, EconomicEvent.scheduled_at)
            .where(
                EconomicEvent.importance >= importance,
                EconomicEvent.scheduled_at >= now,
                EconomicEvent.scheduled_at <= now + timedelta(minutes=minutes),
                ctx.bound(EconomicEvent.created_at),
            )
            .order_by(EconomicEvent.scheduled_at)
        ).all()
        for name, country, at in events:
            problems.append(
                f"importance-{importance} event {country} {name} at {at.isoformat()} is inside the first "
                f"{minutes} min of the trade"
            )
    if problems:
        return ChecklistItem(item=3, name="news_and_events", passed=False, detail="; ".join(problems))
    return ChecklistItem(
        item=3,
        name="news_and_events",
        passed=True,
        detail=f"no contradicting CONFIRMED story in {int(cfg['news_lookback_hours'])}h and no "
        f"importance-{importance} event in the next {minutes} min",
    )


def _check_historian(session: Session, ctx: ToolContext, inst_id: str) -> ChecklistItem:
    found = latest_card(session, ctx, author="historian", card_type="history_verdict", inst_id=inst_id)
    if found is None:
        return ChecklistItem(
            item=4, name="historian_verdict", passed=True, detail=f"no Historian verdict on {inst_id}"
        )
    verdict = found.card.get("verdict")
    if verdict == "contradicts":
        return ChecklistItem(
            item=4,
            name="historian_verdict",
            passed=False,
            detail=f"the newest Historian verdict on {inst_id} contradicts the idea",
        )
    return ChecklistItem(
        item=4, name="historian_verdict", passed=True, detail=f"Historian verdict: {verdict}"
    )


def min_expected_r(settings: SettingsData, defaults: Mapping[str, Any] = DEFAULTS) -> Decimal | None:
    """The owner's min_expected_r; the settings row's risk profile default when the row predates the
    key; None when there is no settings row (rule 2: limits come only from settings)."""
    if settings.version is None:
        return None
    raw = settings.data.get("min_expected_r")
    if raw is None:
        profiles: Mapping[str, Any] = defaults["risk_profiles"]
        profile = settings.data.get("risk_profile")
        if not isinstance(profile, str) or profile not in profiles or profile == "default":
            profile = str(profiles["default"])
        raw = profiles[profile]["min_expected_r"]
    return Decimal(str(raw))


def expectancy(probability: Decimal, r: Decimal) -> Decimal:
    """p x R - (1 - p)."""
    return probability * r - (1 - probability)


def _check_expectancy(
    proposal: TradeProposal, settings: SettingsData, cfg: Mapping[str, Any]
) -> ChecklistItem:
    min_r = min_expected_r(settings)
    if min_r is None:
        return ChecklistItem(
            item=5, name="expectancy", passed=False, detail="no settings saved for this environment"
        )
    r, p = proposal.expected_r_multiple, proposal.probability_estimate
    edge = expectancy(p, r)
    floor = Decimal(str(cfg["min_expectancy"]))
    problems = []
    if r < min_r:
        problems.append(f"expected R {r} is below the owner's min R {min_r}")
    if edge <= floor:
        problems.append(f"p x R - (1 - p) = {edge.normalize()} must be greater than {floor}")
    if problems:
        return ChecklistItem(item=5, name="expectancy", passed=False, detail="; ".join(problems))
    return ChecklistItem(
        item=5,
        name="expectancy",
        passed=True,
        detail=f"R {r} >= {min_r}; p x R - (1 - p) = {edge.normalize()} > {floor}",
    )


def _check_duplicate(
    session: Session,
    ctx: ToolContext,
    proposal: TradeProposal,
    cfg: Mapping[str, Any],
    exclude_id: uuid.UUID | None,
) -> ChecklistItem:
    problems = []
    for position in open_positions(session, ctx, proposal.inst_id):
        if position_direction(position) == proposal.direction:
            problems.append(f"there is an open {proposal.direction} position on {proposal.inst_id}")
            break
    query = select(Proposal.id, Proposal.status).where(
        Proposal.env == ctx.env,
        Proposal.inst_id == proposal.inst_id,
        Proposal.kind == "entry",
        Proposal.side == proposal.side,
        Proposal.status.in_(list(cfg["pending_statuses"])),
    )
    if exclude_id is not None:
        query = query.where(Proposal.id != exclude_id)
    pending = session.execute(query.limit(1)).first()
    if pending is not None:
        problems.append(f"proposal {pending.id} ({pending.status}) is already pending on the same side")
    if problems:
        return ChecklistItem(item=6, name="no_duplicate", passed=False, detail="; ".join(problems))
    return ChecklistItem(
        item=6, name="no_duplicate", passed=True, detail="no open position or pending proposal on this side"
    )


def run_checklist(
    session: Session,
    ctx: ToolContext,
    proposal: TradeProposal,
    *,
    exclude_id: uuid.UUID | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> list[ChecklistItem]:
    """All six §7.6 checks, in order. ``exclude_id``: a proposal being revised (not a duplicate)."""
    cfg = checklist_config(defaults)
    settings = settings_data(session, ctx)
    return [
        _check_watchlist(session, ctx, proposal.inst_id),
        _check_setup(session, ctx, proposal.inst_id, cfg),
        _check_news_and_events(session, ctx, proposal, settings, cfg),
        _check_historian(session, ctx, proposal.inst_id),
        _check_expectancy(proposal, settings, cfg),
        _check_duplicate(session, ctx, proposal, cfg, exclude_id),
    ]


# -- validation ----------------------------------------------------------------------------------------


def _errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())


def parse(kind: str, raw: Mapping[str, Any] | None) -> TradeProposal | PositionAction:
    """The validated proposal, or ToolError ``invalid_proposal`` with every schema error."""
    if raw is None:
        raise ToolError("invalid_proposal", "proposal is required")
    try:
        if kind == "entry":
            return TradeProposal.model_validate(dict(raw))
        action = PositionAction.model_validate(dict(raw))
    except ValidationError as exc:
        model = "TradeProposal" if kind == "entry" else f"{kind} action"
        raise ToolError("invalid_proposal", f"{model} failed validation: {_errors(exc)}") from exc
    problems = validate_position_action(kind, action)
    if problems:
        raise ToolError("invalid_proposal", f"{kind} action failed validation: {'; '.join(problems)}")
    return action


def check_author(agent: str, kind: str) -> None:
    """Entries come only from the Strategist; position actions only from the Portfolio Manager."""
    if kind == "entry" and agent != STRATEGIST:
        raise ToolError("kind_not_allowed", "only the Strategist submits entry proposals")
    if kind in POSITION_ACTION_KINDS and agent != PORTFOLIO_MANAGER:
        raise ToolError("kind_not_allowed", f"only the Portfolio Manager submits {kind} proposals")


def checklist_error(items: Sequence[ChecklistItem]) -> ToolError | None:
    failed = [i for i in items if not i.passed]
    if not failed:
        return None
    lines = "; ".join(f"({i.item}) {i.name}: {i.detail}" for i in failed)
    return ToolError(
        "checklist_failed",
        f"{len(failed)} of 6 Strategist checklist items failed, nothing was saved: {lines}",
    )


# -- writes --------------------------------------------------------------------------------------------


def resolve_cycle(session: Session, ctx: ToolContext) -> uuid.UUID | None:
    """The cycle named by the run's correlation id (``<uuid>``, ``cycle-<uuid>`` or ``cycle:<uuid>``)
    when that cycle exists in this env; else None (an owner-requested or out-of-cycle proposal)."""
    raw = ctx.run.correlation_id or ctx.correlation_id
    if not raw:
        return None
    for prefix in ("cycle-", "cycle:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    try:
        cycle_id = uuid.UUID(raw)
    except ValueError:
        return None
    found = session.scalar(select(Cycle.id).where(Cycle.id == cycle_id, Cycle.env == ctx.env))
    return found


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def new_row(
    ctx: ToolContext,
    kind: str,
    model: TradeProposal | PositionAction,
    *,
    cycle_id: uuid.UUID | None,
    version: int = 1,
    parent_id: uuid.UUID | None = None,
) -> Proposal:
    now = ctx.now()
    common: dict[str, Any] = {
        "id": uuid.uuid4(),
        "env": ctx.env,
        "cycle_id": cycle_id,
        "version": version,
        "parent_id": parent_id,
        "kind": kind,
        "author": ctx.agent,
        "inst_id": model.inst_id,
        "stop": model.stop,
        "take_profits": [
            {"price": _decimal_str(t.price), "pct": _decimal_str(t.pct)} for t in model.take_profits
        ],
        "rationale_md": model.rationale_md,
        "evidence": [e.model_dump() for e in model.evidence],
        "status": "under_review",
        "created_at": now,
        "updated_at": now,
    }
    if isinstance(model, TradeProposal):
        common.update(
            side=model.side,
            order_type=model.order_type,
            entry=model.entry,
            entry_zone=[_decimal_str(v) for v in model.entry_zone] if model.entry_zone else None,
            time_stop_minutes=model.time_stop_minutes,
            size_suggestion_pct=model.size_suggestion_pct_equity,
            leverage_suggestion=model.leverage_suggestion,
            expected_r=model.expected_r_multiple,
            probability=model.probability_estimate,
            invalidation=model.invalidation,
        )
    else:
        common.update(close_pct=model.close_pct, invalidation=model.rule)
    return Proposal(**common)


def load_proposal(session: Session, ctx: ToolContext, proposal_id: str) -> Proposal:
    try:
        wanted = uuid.UUID(str(proposal_id))
    except ValueError as exc:
        raise ToolError("unknown_proposal", f"{proposal_id!r} is not a proposal id") from exc
    row = session.scalars(
        select(Proposal).where(Proposal.id == wanted, Proposal.env == ctx.env).with_for_update()
    ).first()
    if row is None:
        raise ToolError("unknown_proposal", f"no proposal {proposal_id} in {ctx.env}")
    return row


def require_open(row: Proposal, cfg: Mapping[str, Any], action: str) -> None:
    allowed = list(cfg["open_statuses"])
    if row.status not in allowed:
        raise ToolError(
            "proposal_not_open",
            f"proposal {row.id} is {row.status}; {action} needs one of {', '.join(allowed)}",
        )


# -- vote deadlines (ATL-182) ---------------------------------------------------------------------

_run_deadlines: dict[tuple[str, str, str], datetime] = {}
"""(env, agent, proposal_id) -> the deadline of the vote run the cycle started for that agent on that
proposal version: min(the cycle's vote deadline, run start + voter_run_timeout_seconds)."""


def set_run_deadline(env: str, agent: str, proposal_id: str, deadline: datetime) -> None:
    _run_deadlines[(env, agent, str(proposal_id))] = deadline


def clear_run_deadline(env: str, agent: str, proposal_id: str) -> None:
    _run_deadlines.pop((env, agent, str(proposal_id)), None)


def require_vote_window(session: Session, ctx: ToolContext, row: Proposal) -> None:
    """Refuse a vote after the deadline: the cycle's ``deadlines.vote`` (once the vote has opened)
    and the agent's own vote-run deadline (when the cycle started one). A refused vote stores
    nothing, so the tally counts the voter as missing (skip at missing_confidence)."""
    now = ctx.now()
    run_deadline = _run_deadlines.get((ctx.env, ctx.agent, str(row.id)))
    if run_deadline is not None and now > run_deadline:
        raise ToolError(
            "vote_deadline_passed",
            f"your vote run on proposal {row.id} ended at {run_deadline.isoformat()}; no vote is stored",
        )
    if row.cycle_id is None:
        return
    deadlines = session.scalars(select(Cycle.deadlines).where(Cycle.id == row.cycle_id)).first()
    raw = (deadlines or {}).get("vote") if isinstance(deadlines, Mapping) else None
    if not raw:
        return
    try:
        closes = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return
    if now > closes:
        raise ToolError(
            "vote_deadline_passed",
            f"the vote on proposal {row.id} closed at {raw}; no vote is stored",
        )


def proposal_card(row: Proposal, checklist: Sequence[ChecklistItem], summary_md: str) -> dict[str, Any]:
    """The War Room ``proposal`` card (JSON-safe; decimals as strings)."""
    edge = (
        expectancy(row.probability, row.expected_r)
        if row.probability is not None and row.expected_r is not None
        else None
    )
    return {
        "type": "proposal",
        "proposal_id": str(row.id),
        "version": row.version,
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "cycle_id": str(row.cycle_id) if row.cycle_id else None,
        "kind": row.kind,
        "author": row.author,
        "status": row.status,
        "inst_id": row.inst_id,
        "side": row.side,
        "order_type": row.order_type,
        "entry": _decimal_str(row.entry),
        "entry_zone": row.entry_zone,
        "stop": _decimal_str(row.stop),
        "take_profits": row.take_profits,
        "time_stop_minutes": row.time_stop_minutes,
        "size_suggestion_pct_equity": _decimal_str(row.size_suggestion_pct),
        "close_pct": _decimal_str(row.close_pct),
        "leverage_suggestion": row.leverage_suggestion,
        "expected_r_multiple": _decimal_str(row.expected_r),
        "probability_estimate": _decimal_str(row.probability),
        "expectancy_r": _decimal_str(edge.normalize()) if edge is not None else None,
        "rationale_md": row.rationale_md,
        "invalidation": row.invalidation,
        "evidence": row.evidence,
        "checklist": [i.model_dump() for i in checklist],
        "summary_md": summary_md,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def default_summary(row: Proposal) -> str:
    if row.kind == "entry":
        targets = ", ".join(str(t["price"]) for t in row.take_profits)
        where = _decimal_str(row.entry) or (
            f"{row.entry_zone[0]}-{row.entry_zone[1]}" if row.entry_zone else "market"
        )
        return (
            f"**{(row.side or '').upper()} {row.inst_id}** v{row.version} ({row.order_type}) at {where}, "
            f"stop {_decimal_str(row.stop)}, targets {targets}; R {_decimal_str(row.expected_r)}, "
            f"p {_decimal_str(row.probability)}, size {_decimal_str(row.size_suggestion_pct)}% of equity."
        )
    return f"**{row.kind} {row.inst_id}** (Portfolio Manager): {row.invalidation or row.rationale_md[:200]}"


def upsert_vote(
    session: Session,
    ctx: ToolContext,
    proposal: Proposal,
    *,
    vote: str,
    confidence: Decimal,
    reason: str,
    fix: str | None,
) -> uuid.UUID:
    """One vote per agent per proposal version (votes UNIQUE (proposal_id, agent)): a second vote
    replaces the first."""
    now = ctx.now()
    stmt = insert(Vote).values(
        id=uuid.uuid4(),
        env=ctx.env,
        proposal_id=proposal.id,
        agent=ctx.agent,
        vote=vote,
        confidence=confidence,
        reason=reason,
        fix=fix,
        created_at=now,
        updated_at=now,
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=[Vote.proposal_id, Vote.agent],
        set_={
            "vote": stmt.excluded.vote,
            "confidence": stmt.excluded.confidence,
            "reason": stmt.excluded.reason,
            "fix": stmt.excluded.fix,
            "updated_at": stmt.excluded.updated_at,
        },
    ).returning(Vote.id)
    vote_id: uuid.UUID = session.execute(upsert).scalar_one()
    return vote_id


__all__ = [
    "ChecklistItem",
    "SettingsData",
    "check_author",
    "checklist_config",
    "checklist_error",
    "clear_run_deadline",
    "default_summary",
    "expectancy",
    "load_proposal",
    "min_expected_r",
    "new_row",
    "open_positions",
    "parse",
    "proposal_card",
    "require_open",
    "require_vote_window",
    "resolve_cycle",
    "run_checklist",
    "set_run_deadline",
    "settings_data",
    "upsert_vote",
]
