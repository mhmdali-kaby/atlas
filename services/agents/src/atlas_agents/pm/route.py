"""A Portfolio Manager proposal straight to the Risk Engine (brief §7.8 / §7.9, ATL-169).

Amend and close proposals skip the team vote: :func:`route` moves the saved proposal from
``under_review`` to ``passed`` and makes ATL-160's one signed ``POST /internal/risk/evaluate/{id}``.
The result then goes through the same :func:`atlas_agents.orchestrator.approval.apply_risk_result`
as a cycle's entry: rejected → ``rejected``; autonomy 1 → ``advise_only``; otherwise
``awaiting_owner`` with ``approval_expires_at`` (the owner approves through ATL-162).
Nothing here calls the Executor.

When trading_core refuses or cannot be reached, the proposal is ``rejected`` with
``status_reason`` ``risk_<error code>`` (e.g. ``risk_risk_engine_error``) and the War Room gets one
line with the code and detail.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Final

from atlas_shared.db import Proposal
from sqlalchemy import update

from atlas_agents.orchestrator import approval
from atlas_agents.orchestrator.trading_core import TradingCoreClient, TradingCoreError
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.warroom import message_envelope

log = logging.getLogger(__name__)

WARROOM_STREAM: Final = "atlas.warroom"


def _skip_vote(ctx: ToolContext, proposal_id: uuid.UUID) -> bool:
    with ctx.session_factory() as session:
        row = session.execute(
            update(Proposal)
            .where(
                Proposal.id == proposal_id,
                Proposal.env == ctx.env,
                Proposal.status == "under_review",
                Proposal.kind != "entry",
            )
            .values(status="passed", updated_at=ctx.now())
            .returning(Proposal.id)
        ).first()
        session.commit()
    return row is not None


def _refused(ctx: ToolContext, proposal_id: uuid.UUID, code: str) -> None:
    """trading_core refused or could not be reached: the proposal is rejected with the reason."""
    with ctx.session_factory() as session:
        session.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id, Proposal.status == "passed")
            .values(status="rejected", status_reason=f"risk_{code}"[:64], updated_at=ctx.now())
        )
        session.commit()


def _line(ctx: ToolContext, content: str) -> None:
    ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="text", content_md=content))


async def route(ctx: ToolContext, proposal_id: str, client: TradingCoreClient) -> str:
    """Vote skipped, one risk check, autonomy. Returns the proposal's status afterwards, or
    ``error`` when it was not under_review."""
    pid = uuid.UUID(proposal_id)
    if not await asyncio.to_thread(_skip_vote, ctx, pid):
        log.warning("portfolio_manager: proposal %s was not under_review; not routed", proposal_id)
        return "error"
    try:
        body = await client.risk_evaluate(proposal_id)
    except TradingCoreError as exc:
        log.warning("portfolio_manager: risk check on %s failed: %s", proposal_id, exc.code)
        await asyncio.to_thread(_refused, ctx, pid, exc.code)
        _line(
            ctx,
            f"Risk check on Portfolio Manager proposal {proposal_id} failed ({exc.code}): {exc.detail}. "
            "The proposal is rejected.",
        )
        return "rejected"
    status, _expires, row = await asyncio.to_thread(
        approval.apply_risk_result,
        ctx,
        pid,
        body,
        cycle_id=None,
        source=f"agent:{ctx.agent}",
    )
    if row is None:
        log.warning("portfolio_manager: proposal %s was not risk_checked after the check", proposal_id)
        return "error"
    return status


__all__ = ["route"]
