"""Vote runs: the analysts review a proposal and cast their own vote (ATL-182, brief §7.9 Voting).

When the cycle opens a vote on a proposal version (and again for each revised version), it starts one
run per ``agents.votes.run_voters`` agent (Technical Analyst, Derivatives & Sentiment, News
Intelligence, Historian) that is enabled, not degraded / rate-limited / paused, within its daily
budget and has not yet voted on that version. The Devil's Advocate votes from its own review
(ATL-159) and gets no run here.

Each run is on the agent's routine (cheaper) model and counts against its turn/token budget like any
run. Its prompt holds the proposal card (that version), the agent's own newest research card on the
instrument (``agents.votes.voter_cards``: TechnicalSetup, PositioningReport, news brief,
HistoryVerdict) and the deadline. The agent must answer with ``warroom.cast_vote``.

Code rules: nothing here casts a vote for the agent. A run that ends without a valid cast_vote
stores nothing. ``warroom.cast_vote`` refuses a vote after the run's deadline (min of the cycle's
vote deadline and run start + ``voter_run_timeout_seconds``) or after the cycle's vote deadline
(:func:`atlas_agents.proposals.require_vote_window`), so a late answer stores nothing either. The
tally then counts the voter as missing: skip at ``missing_confidence``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from atlas_shared.db import Proposal
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents import proposals as props
from atlas_agents.registry import AgentHost
from atlas_agents.session import RunResult
from atlas_agents.tools.framework import ToolContext

log = logging.getLogger(__name__)

NEWS_CARD: Final = "news_brief"
"""The one voter card that names its instruments in an ``inst_ids`` list."""

VoteRunFn = Callable[..., Awaitable[RunResult | None]]
"""``(host, agent, proposal_id, *, deadline, correlation_id) -> RunResult | None``."""


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["votes"]
    return cfg


def run_voters(defaults: Mapping[str, Any] = DEFAULTS) -> list[str]:
    return [str(v) for v in config(defaults)["run_voters"]]


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def own_card(
    session: Session, ctx: ToolContext, agent: str, inst_id: str, defaults: Mapping[str, Any] = DEFAULTS
) -> dict[str, Any] | None:
    """``agent``'s newest research card on ``inst_id`` (``voter_cards``), with its message id, when it
    was posted and its age in minutes. None when it has none."""
    card_type = config(defaults)["voter_cards"].get(agent)
    if card_type is None:
        return None
    found = props.latest_card(
        session,
        ctx,
        author=agent,
        card_type=str(card_type),
        inst_id=inst_id,
        inst_ids_field=card_type == NEWS_CARD,
    )
    if found is None:
        return None
    age = int((ctx.now() - found.posted_at).total_seconds() // 60)
    return {
        "message_id": found.message_id,
        "posted_at": _iso(found.posted_at),
        "age_minutes": max(0, age),
        "card": found.card,
    }


def load_version(ctx: ToolContext, proposal_id: uuid.UUID | str) -> Proposal | None:
    with ctx.session_factory() as session:
        row = session.scalars(
            select(Proposal).where(Proposal.id == uuid.UUID(str(proposal_id)), Proposal.env == ctx.env)
        ).first()
        if row is not None:
            session.expunge(row)
        return row


def vote_prompt(
    agent: str,
    row: Proposal,
    mine: Mapping[str, Any] | None,
    *,
    deadline: datetime,
    now: datetime,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> str:
    card_type = config(defaults)["voter_cards"].get(agent, "research card")
    proposal = {
        "as_of": _iso(now),
        "source": "postgres:proposals",
        "data": props.proposal_card(row, [], props.default_summary(row)),
    }
    seconds = max(0, int((deadline - now).total_seconds()))
    parts = [
        f"The vote is open on {row.inst_id} proposal {row.id} (version {row.version}, by {row.author}).",
        f"Vote deadline: {_iso(deadline)} ({seconds} seconds from now). A vote after it is refused and "
        "counts as skip at 0.5.",
        f"The proposal:\n```json\n{json.dumps(proposal, default=str)}\n```",
    ]
    if mine is None:
        parts.append(
            f"You have no {card_type} card on {row.inst_id}. Vote on what your own tools show, or skip "
            "with low confidence and say so."
        )
    else:
        own = {"as_of": mine["posted_at"], "source": "postgres:warroom_messages", "data": mine}
        parts.append(
            f"Your newest {card_type} card on {row.inst_id} ({mine['age_minutes']} min old):\n"
            f"```json\n{json.dumps(own, default=str)}\n```"
        )
    parts.append(
        f'Answer with exactly one warroom.cast_vote call: proposal_id "{row.id}", vote "take", "change" '
        '(with the concrete fix in "fix") or "skip", confidence 0-1, and a one-line reason with the '
        "number behind it. Do not post a new report for this vote."
    )
    return "\n\n".join(parts)


async def run_vote(
    host: AgentHost,
    agent: str,
    proposal_id: uuid.UUID | str,
    *,
    deadline: datetime,
    correlation_id: str | None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> RunResult | None:
    """One vote run of ``agent`` on that proposal version. None when nothing ran (the version is
    gone or closed, or the agent is not enabled). Whatever the run does, nothing is voted here."""
    from atlas_agents.supervisor import AgentUnavailableError

    ctx = host.tool_context(agent)
    row = await asyncio.to_thread(load_version, ctx, proposal_id)
    if row is None or row.status not in list(props.checklist_config()["open_statuses"]):
        log.info("vote run: proposal %s is unknown or not open; %s not asked", proposal_id, agent)
        return None

    def read_card() -> dict[str, Any] | None:
        with ctx.session_factory() as session:
            return own_card(session, ctx, agent, row.inst_id, defaults)

    mine = await asyncio.to_thread(read_card)
    prompt = vote_prompt(agent, row, mine, deadline=deadline, now=ctx.now(), defaults=defaults)
    props.set_run_deadline(host.env, agent, str(row.id), deadline)
    try:
        return await host.run(
            agent,
            prompt,
            trigger="schedule",
            task=f"vote {row.inst_id} v{row.version}",
            correlation_id=correlation_id,
            routine=True,
        )
    except AgentUnavailableError:
        log.info("vote run: %s is not enabled; no vote on %s", agent, row.id)
        return None
    finally:
        props.clear_run_deadline(host.env, agent, str(row.id))


__all__ = [
    "VoteRunFn",
    "config",
    "load_version",
    "own_card",
    "run_vote",
    "run_voters",
    "vote_prompt",
]
