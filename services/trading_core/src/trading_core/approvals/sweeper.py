"""The approval sweeper (ATL-162, brief §14.6 "approval timeout and what happens on timeout").

Every ``approvals.sweep_seconds`` for this trading_core's env:

- **Reminder.** An ``awaiting_owner`` proposal whose window is ``approvals.reminder_fraction`` gone
  (default halfway: ``approval_expires_at - approval_timeout_minutes * (1 - fraction)``) gets one War
  Room line from the Orchestrator and a ``proposal.approval_reminder`` event; ``approval_reminded_at``
  makes it once only.
- **Expiry.** At ``approval_expires_at`` the proposal gets ``autonomy.on_approval_timeout``. The only
  action is ``reject`` (any other value is treated as reject and logged): status ``rejected``,
  ``status_reason = approval_timeout``, ``decided_by = system``, ``proposal.rejected`` on
  atlas.proposals and a War Room line. Never "approve on timeout": silence is not approval (§7.9).

Both are conditional UPDATEs on ``status = 'awaiting_owner'``, so an approval that already moved the
row to ``risk_checked`` wins and is never expired underneath the Executor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import redis
from atlas_shared import bus
from atlas_shared.db import Proposal
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session

from trading_core.approvals.common import (
    PROPOSALS_STREAM,
    SOURCE,
    iso,
    proposal_payload,
    publish,
    warroom_line,
)

log = logging.getLogger("trading_core.approvals")

JOB = "approvals"


@dataclass
class SweepResult:
    reminded: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)


def _settings(engine: Engine, env: str) -> dict[str, Any]:
    with engine.connect() as c:
        row = c.execute(text("SELECT data FROM settings WHERE env = :e"), {"e": env}).first()
    data = row[0] if row is not None else None
    return data if isinstance(data, dict) else {}


def _timeout(data: dict[str, Any]) -> timedelta:
    default = int(DEFAULTS["autonomy"]["approval_timeout_minutes"])
    try:
        minutes = int(data.get("approval_timeout_minutes", default))
    except (TypeError, ValueError):
        minutes = default
    return timedelta(minutes=max(1, minutes))


def _fraction() -> Decimal:
    try:
        value = Decimal(str(DEFAULTS["approvals"]["reminder_fraction"]))
    except (ArithmeticError, KeyError, TypeError, ValueError):
        value = Decimal("0.5")
    return min(max(value, Decimal(0)), Decimal(1))


def _baghdad(at: datetime) -> str:
    tz = ZoneInfo(str(DEFAULTS["timezone"]["display"]))
    return at.astimezone(tz).strftime("%H:%M")


def _side(row: Proposal) -> str:
    return f"{row.side} " if row.side else ""


def sweep(
    env: str,
    *,
    engine: Engine,
    redis_client: redis.Redis,
    now: Callable[[], datetime] | None = None,
) -> SweepResult:
    """One pass: post due reminders, then expire due proposals. ``now`` is the simulated clock."""
    at = (now or (lambda: datetime.now(UTC)))()
    data = _settings(engine, env)
    remaining = _timeout(data) * float(Decimal(1) - _fraction())
    action = str(data.get("on_approval_timeout", DEFAULTS["autonomy"]["on_approval_timeout"]))
    if action != "reject":
        log.warning("unsupported on_approval_timeout; rejecting", extra={"value": action})
    out = SweepResult()

    with Session(engine, expire_on_commit=False) as s, s.begin():
        due = list(
            s.scalars(
                update(Proposal)
                .where(
                    Proposal.env == env,
                    Proposal.status == "awaiting_owner",
                    Proposal.approval_expires_at.is_not(None),
                    Proposal.approval_expires_at > at,
                    Proposal.approval_expires_at - remaining <= at,
                    Proposal.approval_reminded_at.is_(None),
                )
                .values(approval_reminded_at=at)
                .returning(Proposal)
            )
        )
    for row in due:
        expires = row.approval_expires_at
        assert expires is not None  # filtered above
        out.reminded.append(str(row.id))
        minutes = max(1, int((expires - at).total_seconds() // 60))
        publish(
            redis_client,
            PROPOSALS_STREAM,
            "proposal.approval_reminder",
            row,
            proposal_payload(row, minutes_left=minutes),
            SOURCE,
        )
        warroom_line(
            redis_client,
            row,
            f"Reminder: the {_side(row)}{row.inst_id} proposal is waiting for your approval. "
            f"It expires at {_baghdad(expires)} Baghdad (about {minutes} min).",
            {"type": "approval_reminder", "proposal_id": str(row.id), "approval_expires_at": iso(expires)},
        )

    with Session(engine, expire_on_commit=False) as s, s.begin():
        gone = list(
            s.scalars(
                update(Proposal)
                .where(
                    Proposal.env == env,
                    Proposal.status == "awaiting_owner",
                    Proposal.approval_expires_at.is_not(None),
                    Proposal.approval_expires_at <= at,
                )
                .values(
                    status="rejected",
                    status_reason="approval_timeout",
                    decided_by="system",
                    decided_at=at,
                    updated_at=at,
                )
                .returning(Proposal)
            )
        )
    for row in gone:
        out.expired.append(str(row.id))
        publish(
            redis_client,
            PROPOSALS_STREAM,
            "proposal.rejected",
            row,
            proposal_payload(row, decided_by="system", on_approval_timeout="reject"),
            SOURCE,
        )
        warroom_line(
            redis_client,
            row,
            f"The {_side(row)}{row.inst_id} proposal was rejected: no approval before it expired.",
            {"type": "approval_timeout", "proposal_id": str(row.id), "status": "rejected"},
        )
    if out.reminded or out.expired:
        log.info("approval sweep", extra={"reminded": len(out.reminded), "expired": len(out.expired)})
    return out


def approvals_job(env: str, engine: Engine) -> Callable[[], Awaitable[SweepResult]]:
    """The scheduler job: one sweep in a worker thread with a fresh Redis client."""

    def once() -> SweepResult:
        rds = bus.make_client()
        try:
            return sweep(env, engine=engine, redis_client=rds)
        finally:
            rds.close()

    async def job() -> SweepResult:
        return await asyncio.to_thread(once)

    return job


__all__ = ["JOB", "SweepResult", "approvals_job", "sweep"]
