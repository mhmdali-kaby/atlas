"""The weighted vote on a proposal, computed in code (ATL-154, brief §7.9 Voting).

Voters are ``agents.votes.voters`` (Technical Analyst, Derivatives & Sentiment, News Intelligence,
Historian, Devil's Advocate). For each voter:

* points: take = 1, change = 0.5 when its fix was applied in a later version (the vote was cast on
  an earlier version and the proposal was revised after it), else 0; skip = 0;
* weight = stated confidence x the voter's reliability weight (the newest
  ``agent_scores.data.reliability_weight`` in the last 30 Baghdad days, clamped to 0.5-1.5; 1.0 when
  there is none);
* a voter with no vote on any version counts as skip with confidence 0.5, in the score and in the
  total weight.

The vote passes when sum(points x weight) / sum(weight) >= the threshold (settings key
``agents.vote_threshold``, default 0.6) **and** the Devil's Advocate's newest risk_rating on any
version of the proposal is <= 4. A missing rating fails. A total weight of zero gives ratio 0.

:func:`tally` is the pure arithmetic (Decimal only). :func:`close_vote` reads the votes, ratings,
reliability and threshold, stores passed|rejected on the proposal, posts one ``vote_summary`` War
Room message and publishes ``proposal.vote_closed`` on ``atlas.proposals``. Nothing here calls the
Risk Engine: that hand-off belongs to the Orchestrator's cycle.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from atlas_shared.db import AgentScore, Proposal, Vote, owner_day
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Integer, String, column, select, table
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Session

from atlas_agents.tools.framework import ToolContext, ToolError

VoteKind = Literal["take", "change", "skip"]
Result = Literal["passed", "rejected"]

PROPOSALS_STREAM: Final = "atlas.proposals"
WARROOM_STREAM: Final = "atlas.warroom"
_ZERO: Final = Decimal(0)
_ONE: Final = Decimal(1)
_SHOWN: Final = Decimal("0.0001")

_messages = table(
    "warroom_messages",
    column("env", String),
    column("author", String),
    column("card", JSONB),
    column("created_at", TIMESTAMP(timezone=True)),
)
_settings = table("settings", column("env", String), column("version", Integer), column("data", JSONB))


def votes_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["votes"]
    return config


# -- pure arithmetic -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CastVote:
    """One voter's vote as it counts at close. ``fix_applied`` only matters for ``change``."""

    vote: VoteKind
    confidence: Decimal
    fix_applied: bool = False
    version: int | None = None
    fix: str | None = None


@dataclass(frozen=True)
class VoterLine:
    agent: str
    vote: VoteKind
    confidence: Decimal
    reliability: Decimal
    weight: Decimal
    points: Decimal
    score: Decimal
    missing: bool
    fix_applied: bool
    voted_version: int | None
    fix: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "vote": self.vote,
            "missing": self.missing,
            "confidence": _s(self.confidence),
            "reliability": _s(self.reliability),
            "weight": _s(self.weight),
            "points": _s(self.points),
            "score": _s(self.score),
            "voted_version": self.voted_version,
            "fix": self.fix,
            "fix_applied": self.fix_applied,
        }


@dataclass(frozen=True)
class Tally:
    lines: tuple[VoterLine, ...]
    weighted_score: Decimal
    total_weight: Decimal
    ratio: Decimal
    threshold: Decimal
    da_rating: int | None
    da_max_rating: int
    passed: bool
    reasons: tuple[str, ...] = field(default=())

    @property
    def result(self) -> Result:
        return "passed" if self.passed else "rejected"


def _s(value: Decimal) -> str:
    """A Decimal for display: at most 4 places, no exponent, no trailing zeros."""
    shown = value.quantize(_SHOWN) if value != value.to_integral_value() else value
    text = format(shown.normalize(), "f")
    return text


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def points_for(vote: CastVote, scores: Mapping[str, Any]) -> Decimal:
    if vote.vote == "take":
        return Decimal(str(scores["take"]))
    if vote.vote == "change":
        return Decimal(str(scores["change_applied" if vote.fix_applied else "change"]))
    return Decimal(str(scores["skip"]))


def tally(
    votes: Mapping[str, CastVote | None],
    *,
    reliability: Mapping[str, Decimal] | None = None,
    threshold: Decimal,
    da_rating: int | None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> Tally:
    """The §7.9 weighted vote over the configured voters. ``votes`` maps a voter to its vote (absent
    or None = missing); votes from agents that are not voters are ignored."""
    cfg = votes_config(defaults)
    scores: Mapping[str, Any] = cfg["scores"]
    missing_conf = Decimal(str(cfg["missing_confidence"]))
    low, high = Decimal(str(cfg["reliability_min"])), Decimal(str(cfg["reliability_max"]))
    default_rel = Decimal(str(cfg["reliability_default"]))
    max_rating = int(cfg["da_max_rating"])
    lines: list[VoterLine] = []
    for agent in cfg["voters"]:
        cast = votes.get(agent)
        missing = cast is None
        counted = cast if cast is not None else CastVote(vote="skip", confidence=missing_conf)
        confidence = clamp(counted.confidence, _ZERO, _ONE)
        rel = clamp((reliability or {}).get(agent, default_rel), low, high)
        weight = confidence * rel
        pts = points_for(counted, scores)
        lines.append(
            VoterLine(
                agent=agent,
                vote=counted.vote,
                confidence=confidence,
                reliability=rel,
                weight=weight,
                points=pts,
                score=pts * weight,
                missing=missing,
                fix_applied=counted.vote == "change" and counted.fix_applied,
                voted_version=counted.version,
                fix=counted.fix,
            )
        )
    total = sum((line.weight for line in lines), _ZERO)
    score = sum((line.score for line in lines), _ZERO)
    ratio = score / total if total > 0 else _ZERO
    reasons: list[str] = []
    if ratio < threshold:
        reasons.append("ratio_below_threshold")
    if da_rating is None:
        reasons.append("da_rating_missing")
    elif da_rating > max_rating:
        reasons.append("da_rating_too_high")
    return Tally(
        lines=tuple(lines),
        weighted_score=score,
        total_weight=total,
        ratio=ratio,
        threshold=threshold,
        da_rating=da_rating,
        da_max_rating=max_rating,
        passed=not reasons,
        reasons=tuple(reasons),
    )


# -- reads ---------------------------------------------------------------------------------------------


def version_chain(session: Session, proposal: Proposal) -> list[Proposal]:
    """``proposal`` and every version it revises, newest first."""
    chain = [proposal]
    seen = {proposal.id}
    while chain[-1].parent_id is not None and chain[-1].parent_id not in seen:
        parent = session.get(Proposal, chain[-1].parent_id)
        if parent is None:
            break
        chain.append(parent)
        seen.add(parent.id)
    return chain


def collect_votes(session: Session, chain: Sequence[Proposal]) -> dict[str, CastVote]:
    """Each agent's vote on its newest voted version of the chain. A ``change`` cast on an earlier
    version than the one being closed counts as having its fix applied (the proposal was revised)."""
    current = chain[0].version
    versions = {p.id: p.version for p in chain}
    rows = session.scalars(select(Vote).where(Vote.proposal_id.in_(list(versions)))).all()
    out: dict[str, CastVote] = {}
    for row in sorted(rows, key=lambda r: versions[r.proposal_id]):
        version = versions[row.proposal_id]
        if row.vote not in ("take", "change", "skip"):
            continue
        out[row.agent] = CastVote(
            vote=row.vote,  # type: ignore[arg-type]
            confidence=Decimal(row.confidence),
            fix_applied=row.vote == "change" and version < current,
            version=version,
            fix=row.fix,
        )
    return out


def reliability_weights(
    session: Session, ctx: ToolContext, voters: Sequence[str], cfg: Mapping[str, Any]
) -> dict[str, Decimal]:
    """The newest valid ``data.reliability_weight`` per voter in the last ``reliability_days`` days."""
    today = owner_day(ctx.now())
    since = today - timedelta(days=int(cfg["reliability_days"]))
    rows = session.execute(
        select(AgentScore.agent, AgentScore.data)
        .where(
            AgentScore.env == ctx.env,
            AgentScore.agent.in_(list(voters)),
            AgentScore.day > since,
            AgentScore.day <= today,
        )
        .order_by(AgentScore.day.desc())
    ).all()
    out: dict[str, Decimal] = {}
    for agent, data in rows:
        if agent in out or not isinstance(data, dict):
            continue
        weight = _decimal(data.get("reliability_weight"))
        if weight is not None:
            out[agent] = weight
    return out


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def da_rating(
    session: Session, ctx: ToolContext, chain: Sequence[Proposal], cfg: Mapping[str, Any]
) -> int | None:
    """The Devil's Advocate's newest risk_rating (1-5) on any version of the proposal; None when
    there is no card or its rating is not an integer 1-5."""
    row = session.execute(
        select(_messages.c.card)
        .where(
            _messages.c.env == ctx.env,
            _messages.c.author == str(cfg["da_agent"]),
            _messages.c.card["type"].astext == str(cfg["da_card_type"]),
            _messages.c.card["proposal_id"].astext.in_([str(p.id) for p in chain]),
            ctx.bound(_messages.c.created_at),
        )
        .order_by(_messages.c.created_at.desc())
        .limit(1)
    ).first()
    if row is None or not isinstance(row[0], dict):
        return None
    rating = row[0].get("risk_rating")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        return None
    return rating


def vote_threshold(session: Session, ctx: ToolContext, cfg: Mapping[str, Any]) -> Decimal:
    """The owner's ``agents.vote_threshold`` (0..1) from the settings row, else the default."""
    default = Decimal(str(cfg["threshold"]))
    data = session.scalar(select(_settings.c.data).where(_settings.c.env == ctx.env))
    if not isinstance(data, dict):
        return default
    value = _decimal(data.get(str(cfg["threshold_setting"])))
    if value is None or not _ZERO <= value <= _ONE:
        return default
    return value


# -- close ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VoteClosed:
    proposal_id: str
    version: int
    result: Result
    tally: Tally
    message_id: str
    card: dict[str, Any]


def summary_card(proposal: Proposal, result: Tally) -> dict[str, Any]:
    return {
        "type": "vote_summary",
        "proposal_id": str(proposal.id),
        "version": proposal.version,
        "cycle_id": str(proposal.cycle_id) if proposal.cycle_id else None,
        "kind": proposal.kind,
        "inst_id": proposal.inst_id,
        "side": proposal.side,
        "result": result.result,
        "ratio": _s(result.ratio),
        "threshold": _s(result.threshold),
        "weighted_score": _s(result.weighted_score),
        "total_weight": _s(result.total_weight),
        "da_rating": result.da_rating,
        "da_max_rating": result.da_max_rating,
        "reasons": list(result.reasons),
        "voters": [line.as_json() for line in result.lines],
    }


def summary_md(proposal: Proposal, result: Tally) -> str:
    what = f"{(proposal.side or proposal.kind).upper()} {proposal.inst_id} v{proposal.version}"
    sign = ">=" if result.ratio >= result.threshold else "<"
    da = f"{result.da_rating}/5" if result.da_rating is not None else "missing"
    voters = "; ".join(
        f"{line.agent} {'missing (skip)' if line.missing else line.vote} conf {_s(line.confidence)} "
        f"x rel {_s(line.reliability)} = weight {_s(line.weight)}, score {_s(line.score)}"
        for line in result.lines
    )
    return (
        f"**Vote {result.result}** on {what}: weighted {_s(result.ratio)} {sign} threshold "
        f"{_s(result.threshold)}; Devil's Advocate risk {da} (max {result.da_max_rating}). {voters}."
    )


def close_vote(ctx: ToolContext, proposal_id: str, *, defaults: Mapping[str, Any] = DEFAULTS) -> VoteClosed:
    """Close the vote on one proposal version: store passed|rejected, post the ``vote_summary``
    message (author ``ctx.agent``) and publish ``proposal.vote_closed``. Raises ToolError
    ``unknown_proposal`` or ``proposal_not_open``; the DB is committed before publishing."""
    from atlas_agents.tools.warroom import _publishable, message_envelope

    _publishable(ctx)
    cfg = votes_config(defaults)
    try:
        wanted = uuid.UUID(str(proposal_id))
    except ValueError as exc:
        raise ToolError("unknown_proposal", f"{proposal_id!r} is not a proposal id") from exc
    with ctx.session_factory() as session:
        proposal = session.scalars(
            select(Proposal).where(Proposal.id == wanted, Proposal.env == ctx.env).with_for_update()
        ).first()
        if proposal is None:
            raise ToolError("unknown_proposal", f"no proposal {proposal_id} in {ctx.env}")
        allowed = list(cfg["closable_statuses"])
        if proposal.status not in allowed:
            raise ToolError(
                "proposal_not_open",
                f"proposal {proposal.id} is {proposal.status}; "
                f"closing a vote needs one of {', '.join(allowed)}",
            )
        chain = version_chain(session, proposal)
        voters = [str(v) for v in cfg["voters"]]
        result = tally(
            collect_votes(session, chain),
            reliability=reliability_weights(session, ctx, voters, cfg),
            threshold=vote_threshold(session, ctx, cfg),
            da_rating=da_rating(session, ctx, chain, cfg),
            defaults=defaults,
        )
        proposal.status = result.result
        proposal.updated_at = ctx.now()
        card = summary_card(proposal, result)
        content = summary_md(proposal, result)
        session.commit()
    message = message_envelope(ctx, kind="vote_summary", content_md=content, card=card)
    ctx.publish(WARROOM_STREAM, message)
    ctx.publish(
        PROPOSALS_STREAM,
        EventEnvelope(
            type="proposal.vote_closed",
            ts=ctx.now(),
            env=ctx.env,
            source=f"agent:{ctx.agent}",
            correlation_id=ctx.run.correlation_id or ctx.correlation_id,
            payload={
                "proposal_id": card["proposal_id"],
                "version": card["version"],
                "cycle_id": card["cycle_id"],
                "inst_id": card["inst_id"],
                "result": card["result"],
                "ratio": card["ratio"],
                "threshold": card["threshold"],
                "da_rating": card["da_rating"],
                "reasons": card["reasons"],
                "message_id": str(message.id),
            },
        ),
    )
    return VoteClosed(
        proposal_id=card["proposal_id"],
        version=card["version"],
        result=result.result,
        tally=result,
        message_id=str(message.id),
        card=card,
    )


__all__ = [
    "CastVote",
    "Tally",
    "VoteClosed",
    "VoterLine",
    "close_vote",
    "collect_votes",
    "da_rating",
    "reliability_weights",
    "summary_card",
    "tally",
    "version_chain",
    "vote_threshold",
    "votes_config",
]
