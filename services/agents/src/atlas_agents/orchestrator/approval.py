"""What happens to a proposal after its risk check (brief §7.9 / §10 check 9; ATL-160, ATL-169).

The single owner of the approval policy. Both an analysis cycle's entry (``CycleRunner._risk``) and a
Portfolio Manager amend/close (``pm.route.route``) call :func:`apply_risk_result` with the body of
the signed ``POST /internal/risk/evaluate/{id}``:

* ``result == "rejected"`` → ``rejected`` with ``status_reason`` ``risk_rejected``;
* autonomy 1, or the engine's ``decision == "advise_only"`` → ``advise_only``;
* otherwise → ``awaiting_owner`` with ``approval_expires_at`` = now + the approval timeout (also for
  ``send_now``, while autonomy 3-4 are not enabled: the owner approves through ATL-162).

The row only moves from ``risk_checked``. It posts the ``risk_check`` War Room card and, for
``awaiting_owner``/``advise_only``, one ``proposal.{status}`` event on ``atlas.proposals``.
Nothing here calls the Executor.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from atlas_shared.db import Proposal
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from atlas_agents import proposals as proposal_reads
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.warroom import message_envelope

WARROOM_STREAM: Final = "atlas.warroom"
PROPOSALS_STREAM: Final = "atlas.proposals"


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def autonomy_level(session: Session, ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> int:
    """The owner's autonomy level: settings.data, else the typed column, else the default."""
    data = proposal_reads.settings_data(session, ctx).data
    row = session.execute(text("SELECT autonomy_level FROM settings WHERE env = :e"), {"e": ctx.env}).first()
    raw = data.get("autonomy_level", row[0] if row is not None else None)
    try:
        return int(raw) if raw is not None else int(defaults["autonomy"]["default_level"])
    except (TypeError, ValueError):
        return int(defaults["autonomy"]["default_level"])


def approval_timeout(session: Session, ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> timedelta:
    """settings.data.approval_timeout_minutes (at least 1 minute), else the default."""
    data = proposal_reads.settings_data(session, ctx).data
    raw = data.get("approval_timeout_minutes", defaults["autonomy"]["approval_timeout_minutes"])
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = int(defaults["autonomy"]["approval_timeout_minutes"])
    return timedelta(minutes=max(1, minutes))


def apply_risk_result(
    ctx: ToolContext,
    proposal_id: uuid.UUID,
    body: Mapping[str, Any],
    *,
    cycle_id: uuid.UUID | str | None,
    source: str,
    now: datetime | None = None,
    autonomy: int | None = None,
    inst_id: str | None = None,
    defaults: Mapping[str, Any] = DEFAULTS,
) -> tuple[str, datetime | None, Any]:
    """Set the proposal's status from the risk result and publish the card and event (sync).

    Returns ``(status, approval_expires_at, row)``; ``row`` (id, version, inst_id, side, kind) is
    None when the proposal was no longer ``risk_checked`` (nothing is then published on
    atlas.proposals). ``autonomy`` is read from the settings when not given; ``now`` defaults to
    ``ctx.now()``.
    """
    at = now or ctx.now()
    with ctx.session_factory() as session:
        level = autonomy if autonomy is not None else autonomy_level(session, ctx, defaults)
        if body.get("result") == "rejected":
            status, expires = "rejected", None
        elif level <= 1 or body.get("decision") == "advise_only":
            status, expires = "advise_only", None
        else:
            status, expires = "awaiting_owner", at + approval_timeout(session, ctx, defaults)
        values: dict[str, Any] = {"status": status, "updated_at": at}
        if expires is not None:
            values["approval_expires_at"] = expires
        if status == "rejected":
            values["status_reason"] = "risk_rejected"
        row = session.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id, Proposal.status == "risk_checked")
            .values(**values)
            .returning(Proposal.id, Proposal.version, Proposal.inst_id, Proposal.side, Proposal.kind)
        ).first()
        session.commit()

    inst = row.inst_id if row is not None else (inst_id or "?")
    card = {"type": "risk_check", **{k: v for k, v in body.items() if k != "ok"}}
    content = f"**Risk {body.get('result')}** on {inst}: {body.get('reason') or ''}".strip()
    ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="risk_check", content_md=content, card=card))
    if row is not None and status in ("awaiting_owner", "advise_only"):
        ctx.publish(
            PROPOSALS_STREAM,
            EventEnvelope(
                type=f"proposal.{status}",
                ts=at,
                env=ctx.env,
                source=source,
                correlation_id=ctx.correlation_id,
                payload={
                    "proposal_id": str(row.id),
                    "version": row.version,
                    "cycle_id": str(cycle_id) if cycle_id is not None else None,
                    "kind": row.kind,
                    "inst_id": row.inst_id,
                    "side": row.side,
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
    return status, expires, row


__all__ = ["PROPOSALS_STREAM", "apply_risk_result", "approval_timeout", "autonomy_level"]
