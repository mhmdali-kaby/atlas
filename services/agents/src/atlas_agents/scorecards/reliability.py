"""Each voter's 30-day reliability weight (ATL-198, brief §7.9 Voting), read by :mod:`atlas_agents.votes`.

For every voter in ``agents.votes.voters`` the sample is its newest vote (on any version, as
:func:`atlas_agents.votes.collect_votes` counts it) on each Strategist entry chain whose latest
version was created in the ``agents.scoring.reliability.days`` Baghdad days ending on ``day``. Each
chain's outcome is the realised R of the taken trade (the PM post-mortems' ``data.actual.realised_r``,
else the full closes in ``positions_closed``), else of its filled paper trade (ATL-196). A not-filled
paper trade, a trade still open, a chain with neither, and a scratch (``|R| < scratch_r``) give no
outcome and the vote is not counted.

A vote is **correct** when take, or change with its fix applied, preceded a win (R >= scratch_r), or
when skip, or change without the fix (both score 0 in the vote), preceded a loss (R <= -scratch_r).

``accuracy = correct / n`` and ``reliability_weight = clamp(base + accuracy, reliability_min,
reliability_max)``; with ``n < min_n`` the weight is ``reliability_default`` (1.0). The weight, with
``reliability_accuracy``, ``reliability_n`` and ``reliability_correct`` beside it, is merged into the
voter's ``agent_scores.data`` for ``day`` (a row with n 0 is created when the agent has none);
:func:`atlas_agents.scoring.upsert_score` keeps these keys when the day is re-scored.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from atlas_shared.db import AgentScore, PaperTrade, Proposal
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Session

from atlas_agents import votes
from atlas_agents.scorecards import outcomes, paper
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

JOB: Final = "reliability"
HOST_AGENT: Final = "orchestrator"
"""The slug the daily hook is registered under: the Orchestrator runs the votes these weights feed."""


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    return defaults["agents"]["scoring"]["reliability"]  # type: ignore[no-any-return]


@dataclass(frozen=True)
class Reliability:
    agent: str
    n: int
    correct: int
    accuracy: Decimal | None
    weight: Decimal

    def as_data(self) -> dict[str, Any]:
        return {
            "reliability_weight": str(self.weight),
            "reliability_accuracy": None if self.accuracy is None else float(round(self.accuracy, 4)),
            "reliability_n": self.n,
            "reliability_correct": self.correct,
        }


def weight_for(
    correct: int, n: int, defaults: Mapping[str, Any] = DEFAULTS
) -> tuple[Decimal | None, Decimal]:
    """(accuracy, weight) for ``correct`` of ``n`` scored votes."""
    cfg = config(defaults)
    vcfg = votes.votes_config(defaults)
    if n <= 0:
        return None, Decimal(str(vcfg["reliability_default"]))
    accuracy = Decimal(correct) / Decimal(n)
    if n < int(cfg["min_n"]):
        return accuracy, Decimal(str(vcfg["reliability_default"]))
    weight = votes.clamp(
        Decimal(str(cfg["base"])) + accuracy,
        Decimal(str(vcfg["reliability_min"])),
        Decimal(str(vcfg["reliability_max"])),
    )
    return accuracy, weight


def is_correct(vote: votes.CastVote, result: str) -> bool | None:
    """True/False for a win or loss; None for a scratch (not counted)."""
    if result == "scratch":
        return None
    backs = vote.vote == "take" or (vote.vote == "change" and vote.fix_applied)
    return backs if result == "win" else not backs


def chain_r(session: Session, env: str, head: Proposal, ids: Sequence[uuid.UUID]) -> Decimal | None:
    """The chain's realised R (taken trade, else filled paper trade), or None when it has none."""
    if paper.entry_filled(session, ids):
        r = outcomes._journal_r(session, env, ids)
        if r is None and head.stop is not None:
            r = outcomes._closed_r(session, env, head.inst_id, ids, Decimal(head.stop))
        return r
    trade = session.scalars(select(PaperTrade).where(PaperTrade.proposal_id == head.id)).first()
    if trade is None or trade.status != "filled" or trade.realised_r is None:
        return None
    return Decimal(trade.realised_r)


def classify(r: Decimal, scratch: Decimal) -> str:
    if r >= scratch:
        return "win"
    if r <= -scratch:
        return "loss"
    return "scratch"


def compute(
    session: Session, env: str, day: date, defaults: Mapping[str, Any] = DEFAULTS
) -> dict[str, Reliability]:
    """Each voter's reliability over the window ending on Baghdad ``day`` (inclusive)."""
    cfg = config(defaults)
    voters = [str(v) for v in votes.votes_config(defaults)["voters"]]
    scratch = Decimal(str(cfg["scratch_r"]))
    first = day - timedelta(days=int(cfg["days"]) - 1)
    start, _ = outcomes.day_bounds(first)
    _, end = outcomes.day_bounds(day)
    tallies: dict[str, list[int]] = {agent: [0, 0] for agent in voters}  # [n, correct]
    for head in paper.heads(session, env, start, end):
        chain = votes.version_chain(session, head)
        cast = votes.collect_votes(session, chain)
        if not any(agent in cast for agent in voters):
            continue
        r = chain_r(session, env, head, [p.id for p in chain])
        if r is None:
            continue
        result = classify(r, scratch)
        for agent in voters:
            vote = cast.get(agent)
            if vote is None:
                continue
            ok = is_correct(vote, result)
            if ok is None:
                continue
            tallies[agent][0] += 1
            tallies[agent][1] += int(ok)
    out: dict[str, Reliability] = {}
    for agent, (n, correct) in tallies.items():
        accuracy, weight = weight_for(correct, n, defaults)
        out[agent] = Reliability(agent=agent, n=n, correct=correct, accuracy=accuracy, weight=weight)
    return out


def write(session: Session, env: str, day: date, rel: Reliability, now: datetime) -> None:
    """Merge the reliability keys into the voter's row for ``day``; create it (n 0) when missing."""
    data = rel.as_data()
    stmt = insert(AgentScore).values(
        id=uuid.uuid4(),
        agent=rel.agent,
        env=env,
        day=day,
        n=0,
        hit_rate=None,
        brier_score=None,
        data=data,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["agent", "env", "day"],
        set_={
            "data": AgentScore.__table__.c.data.op("||", return_type=JSONB)(stmt.excluded.data),
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)


def run(ctx: ToolContext, today: date, defaults: Mapping[str, Any] = DEFAULTS) -> dict[str, Reliability]:
    """The daily job: compute over the window ending yesterday (the last day the scorers closed) and
    write each voter's weight into its row for that day."""
    day = today - timedelta(days=1)
    with ctx.session_factory() as session:
        result = compute(session, ctx.env, day, defaults)
        now = ctx.now()
        for rel in result.values():
            write(session, ctx.env, day, rel, now)
        session.commit()
    log.info(
        "reliability: %s %s",
        day.isoformat(),
        {a: (r.n, r.correct, str(r.weight)) for a, r in result.items()},
    )
    return result


__all__ = [
    "HOST_AGENT",
    "JOB",
    "Reliability",
    "classify",
    "compute",
    "is_correct",
    "run",
    "weight_for",
    "write",
]
