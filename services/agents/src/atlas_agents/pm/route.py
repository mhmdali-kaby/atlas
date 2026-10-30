"""A Portfolio Manager proposal straight to the Risk Engine (brief §7.8 / §7.9, ATL-169).

Amend and close proposals skip the team vote: :func:`route` moves the saved proposal from
``under_review`` to ``passed`` and makes ATL-160's one signed ``POST /internal/risk/evaluate/{id}``.
The result then follows autonomy exactly as a cycle's entry does: rejected → ``rejected``; autonomy
1 → ``advise_only``; otherwise ``awaiting_owner`` with ``approval_expires_at`` (the owner approves
through ATL-162). Nothing here calls the Executor.

When trading_core refuses or cannot be reached, the proposal stays ``passed`` (so the next check does
not propose the same kind again) and the War Room gets one line with the error code.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from atlas_shared.db import Proposal
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from atlas_agents import proposals as props
from atlas_agents.orchestrator.trading_core import TradingCoreClient, TradingCoreError
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.warroom import message_envelope

log = logging.getLogger(__name__)

WARROOM_STREAM: Final = "atlas.warroom"
PROPOSALS_STREAM: Final = "atlas.proposals"


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def autonomy_level(session: Session, ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> int:
    """settings.data.autonomy_level, else the typed column, else the default (as the cycle reads it)."""
    data = props.settings_data(session, ctx).data
    row = session.execute(text("SELECT autonomy_level FROM settings WHERE env = :e"), {"e": ctx.env}).first()
    raw = data.get("autonomy_level", row[0] if row is not None else None)
    try:
        return int(raw) if raw is not None else int(defaults["autonomy"]["default_level"])
    except (TypeError, ValueError):
        return int(defaults["autonomy"]["default_level"])


def approval_timeout(session: Session, ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> timedelta:
    data = props.settings_data(session, ctx).data
    raw = data.get("approval_timeout_minutes", defaults["autonomy"]["approval_timeout_minutes"])
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = int(defaults["autonomy"]["approval_timeout_minutes"])
    return timedelta(minutes=max(1, minutes))


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


def _line(ctx: ToolContext, content: str) -> None:
    ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="text", content_md=content))


async def route(ctx: ToolContext, proposal_id: str, client: TradingCoreClient) -> str:
    """Vote skipped, one risk check, autonomy. Returns the proposal's status afterwards, or
    ``error`` (left ``passed``)."""
    pid = uuid.UUID(proposal_id)
    if not await asyncio.to_thread(_skip_vote, ctx, pid):
        log.warning("portfolio_manager: proposal %s was not under_review; not routed", proposal_id)
        return "error"
    try:
        body = await client.risk_evaluate(proposal_id)
    except TradingCoreError as exc:
        log.warning("portfolio_manager: risk check on %s failed: %s", proposal_id, exc.code)
        _line(ctx, f"Risk check on Portfolio Manager proposal {proposal_id} failed: {exc.code}.")
        return "error"
    card = {"type": "risk_check", **{k: v for k, v in body.items() if k != "ok"}}

    def apply() -> tuple[str, datetime | None, Any]:
        with ctx.session_factory() as session:
            level = autonomy_level(session, ctx)
            at = ctx.now()
            if body.get("result") == "rejected":
                status, expires = "rejected", None
            elif level <= 1 or body.get("decision") == "advise_only":
                status, expires = "advise_only", None
            else:  # awaiting_owner, and send_now while autonomy 3-4 are not enabled
                status, expires = "awaiting_owner", at + approval_timeout(session, ctx)
            values: dict[str, Any] = {"status": status, "updated_at": at}
            if expires is not None:
                values["approval_expires_at"] = expires
            if status == "rejected":
                values["status_reason"] = "risk_rejected"
            row = session.execute(
                update(Proposal)
                .where(Proposal.id == pid, Proposal.status == "risk_checked")
                .values(**values)
                .returning(Proposal.id, Proposal.version, Proposal.inst_id, Proposal.kind)
            ).first()
            session.commit()
        return status, expires, row

    status, expires, row = await asyncio.to_thread(apply)
    inst = row.inst_id if row is not None else "?"
    content = f"**Risk {body.get('result')}** on {inst}: {body.get('reason') or ''}".strip()
    ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="risk_check", content_md=content, card=card))
    if row is None:
        log.warning("portfolio_manager: proposal %s was not risk_checked after the check", proposal_id)
        return "error"
    if status in ("awaiting_owner", "advise_only"):
        ctx.publish(
            PROPOSALS_STREAM,
            EventEnvelope(
                type=f"proposal.{status}",
                ts=ctx.now(),
                env=ctx.env,
                source=f"agent:{ctx.agent}",
                correlation_id=ctx.correlation_id,
                payload={
                    "proposal_id": str(row.id),
                    "version": row.version,
                    "cycle_id": None,
                    "kind": row.kind,
                    "inst_id": row.inst_id,
                    "side": None,
                    "risk_check_id": body.get("risk_check_id"),
                    "result": body.get("result"),
                    "decision": body.get("decision"),
                    "final_contracts": body.get("final_contracts"),
                    "final_notional_usd": body.get("final_notional_usd"),
                    "leverage": body.get("leverage"),
                    "approval_expires_at": _iso(expires) if expires else None,
                },
            ),
        )
    return status


__all__ = ["approval_timeout", "autonomy_level", "route"]
