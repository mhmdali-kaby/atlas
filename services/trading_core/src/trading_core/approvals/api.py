"""Signed ``POST /internal/proposals/{proposal_id}/execute`` (ATL-162, ATL-213): send an approved proposal.

Body: ``{"approved_by": "owner"}`` or ``{"approved_by": "autonomy:3"|"autonomy:4"}`` (required).

* ``owner``: Laravel already checked the status, the expiry and the settings version; the proposal
  must be ``awaiting_owner`` and not expired.
* ``autonomy:<level>`` (decision #254): the agents' ``apply_risk_result`` saw a ``send_now`` decision
  and moved the proposal ``risk_checked → sent`` (no approval window). The proposal must be ``sent``.
  If the fresh re-check no longer says ``send_now`` (the owner lowered autonomy, the size grew past
  the ask-above amount, a pause or breaker…), nothing is sent, the proposal is left ``risk_checked``
  and the response is 409 ``needs_owner`` so the caller can open the normal approval window.

Then:

1. re-runs the nine §10 checks (:func:`trading_core.risk.engine.evaluate` with
   ``allowed_statuses=("awaiting_owner",)``, which moves the row to ``risk_checked`` — a second
   approval of the same proposal is refused there);
2. a rejected re-check → proposal ``rejected`` / ``risk_rejected``; autonomy now 1 → ``advise_only``.
   In both cases **the Executor is not called**;
3. otherwise ``execute(new_risk_check_id, approved_by)``. An Executor refusal or failure that leaves the
   proposal un-sent closes it as ``rejected`` / ``execution_<reason>``.

Response (every status): the Executor's ExecutionResult payload when it ran, else
``{"ok": false, "status": "refused", "reason": …, "detail": …}``, plus ``"proposal_id"`` and
``"risk_check"`` (the re-check's decision body, or null). 200 sent · 404 unknown proposal ·
409 refused · 422 bad body · 502 failed / unprotected_closed.

Tests inject ``app.state.approval_execute`` (the Executor call), ``app.state.risk_redis`` and
``app.state.clock``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis
from atlas_shared import bus
from atlas_shared.db import Proposal
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from trading_core.approvals.common import PROPOSALS_STREAM, SOURCE, proposal_payload, publish
from trading_core.execution.executor import ExecutionResult, execute
from trading_core.execution.reconcile import not_reconciled_refusal
from trading_core.risk.api import decision_body
from trading_core.risk.engine import RiskDecision, RiskEngineError, evaluate
from trading_core.security import PROTECTED_PREFIX

logger = logging.getLogger("trading_core.approvals")

router = APIRouter(prefix=PROTECTED_PREFIX + "/proposals")

_STATUS = {"sent": 200, "refused": 409, "failed": 502, "unprotected_closed": 502}
OWNER = "owner"
AUTONOMY_RE = re.compile(r"^autonomy:[34]$")

ExecuteFn = Callable[..., Awaitable[ExecutionResult]]


@dataclass(frozen=True)
class _Refusal:
    http: int
    reason: str
    detail: str


def _refused(pid: str, refusal: _Refusal, risk: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=refusal.http,
        content={
            "ok": False,
            "status": "refused",
            "reason": refusal.reason,
            "detail": refusal.detail,
            "proposal_id": pid,
            "risk_check": risk,
        },
    )


def _recheck(
    pid: uuid.UUID,
    engine: Engine,
    rds: redis.Redis,
    now: Callable[[], datetime],
    approved_by: str = OWNER,
) -> RiskDecision | _Refusal:
    """Status/expiry gate, then the fresh risk check. A rejected or advise-only result closes the
    proposal here, so the caller never reaches the Executor with it."""
    at = now()
    owner = approved_by == OWNER
    expected = "awaiting_owner" if owner else "sent"
    with Session(engine) as s:
        row = s.get(Proposal, pid)
        if row is None:
            return _Refusal(404, "proposal_not_found", "No proposal with this id.")
        if row.status != expected:
            if owner:
                return _Refusal(
                    409, "not_awaiting_owner", f"The proposal is {row.status}, not awaiting approval."
                )
            return _Refusal(409, "not_sent", f"The proposal is {row.status}, not sent by autonomy.")
        if owner and (row.approval_expires_at is None or row.approval_expires_at <= at):
            return _Refusal(409, "approval_expired", "The approval window has closed.")
    try:
        decision = evaluate(pid, engine=engine, redis_client=rds, now=now, allowed_statuses=(expected,))
    except RiskEngineError as exc:
        return _Refusal(409, "risk_engine_error", str(exc))

    if decision.result == "rejected":
        status, reason = "rejected", "risk_rejected"
    elif decision.decision == "advise_only":
        status, reason = "advise_only", "advise_only"
    else:
        return decision
    _close(engine, rds, pid, status, reason, now(), decision, decided_by=approved_by)
    return decision


def _close(
    engine: Engine,
    rds: redis.Redis,
    pid: uuid.UUID,
    status: str,
    reason: str,
    at: datetime,
    decision: RiskDecision | None = None,
    *,
    decided_by: str = OWNER,
) -> None:
    """Move a proposal still at risk_checked (this approval's re-check) to its final status."""
    with Session(engine, expire_on_commit=False) as s, s.begin():
        row = s.scalar(
            update(Proposal)
            .where(Proposal.id == pid, Proposal.status == "risk_checked")
            .values(
                status=status,
                status_reason=reason,
                approval_expires_at=None,
                decided_by=decided_by,
                decided_at=at,
                updated_at=at,
            )
            .returning(Proposal)
        )
        if row is None:
            return
        extra: dict[str, Any] = {"decided_by": decided_by}
        if decision is not None:
            extra.update(risk_check_id=str(decision.risk_check_id), result=decision.result)
        payload = proposal_payload(row, **extra)
    publish(rds, PROPOSALS_STREAM, f"proposal.{status}", row, payload, SOURCE)


def _mark_approved(engine: Engine, pid: uuid.UUID, at: datetime, approved_by: str = OWNER) -> None:
    with Session(engine) as s, s.begin():
        s.execute(update(Proposal).where(Proposal.id == pid).values(decided_by=approved_by, decided_at=at))


def _status_of(engine: Engine, pid: uuid.UUID) -> str | None:
    with Session(engine) as s:
        return s.scalar(select(Proposal.status).where(Proposal.id == pid))


@router.post("/{proposal_id}/execute", response_model=None)
async def execute_proposal(proposal_id: str, request: Request) -> JSONResponse:
    try:
        pid = uuid.UUID(proposal_id)
    except ValueError:
        return JSONResponse(status_code=404, content={"ok": False, "error": "unknown_proposal"})
    raw = await request.body()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_json"})
    approved_by = body.get("approved_by") if isinstance(body, dict) else None
    if not isinstance(approved_by, str) or not (approved_by == OWNER or AUTONOMY_RE.match(approved_by)):
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_approved_by"})

    state = request.app.state
    refusal = not_reconciled_refusal(state)
    if refusal is not None:
        # Rule 8 (ATL-146): no owner approval reaches the Executor before the startup reconciliation.
        content = {**refusal, "proposal_id": proposal_id, "risk_check": None}
        return JSONResponse(status_code=409, content=content)
    engine: Engine = state.engine
    injected: redis.Redis | None = getattr(state, "risk_redis", None)
    now: Callable[[], datetime] = getattr(state, "clock", None) or (lambda: datetime.now(UTC))
    run: ExecuteFn = getattr(state, "approval_execute", None) or execute
    rds = injected if injected is not None else bus.make_client()
    try:
        checked = await asyncio.to_thread(_recheck, pid, engine, rds, now, approved_by)
        if isinstance(checked, _Refusal):
            return _refused(proposal_id, checked)
        risk = {k: v for k, v in decision_body(checked).items() if k != "ok"}
        if checked.result == "rejected":
            return _refused(proposal_id, _Refusal(409, "risk_rejected", checked.reason or ""), risk)
        if checked.decision == "advise_only":
            return _refused(
                proposal_id, _Refusal(409, "advise_only", "Autonomy is Advise only; nothing is sent."), risk
            )
        if approved_by != OWNER and checked.decision != "send_now":
            # Autonomy may only send what the fresh check itself says to send now; the proposal stays
            # risk_checked and the caller opens the owner's approval window.
            return _refused(
                proposal_id,
                _Refusal(409, "needs_owner", "The fresh risk check asks for the owner's approval."),
                risk,
            )

        await asyncio.to_thread(_mark_approved, engine, pid, now(), approved_by)
        result = await run(
            checked.risk_check_id,
            approved_by,
            engine=engine,
            redis_client=rds,
            client_factory=getattr(state, "executor_client_factory", None),
            app_key=state.settings.app_key,
            active_env=state.settings.env,
        )
        if result.status != "sent" and await asyncio.to_thread(_status_of, engine, pid) == "risk_checked":
            await asyncio.to_thread(
                _close,
                engine,
                rds,
                pid,
                "rejected",
                f"execution_{result.reason}"[:64],
                now(),
                decided_by=approved_by,
            )
        logger.info(
            "approval executed",
            extra={
                "proposal_id": proposal_id,
                "approved_by": approved_by,
                "status": result.status,
                "reason": result.reason,
            },
        )
        return JSONResponse(
            status_code=_STATUS[result.status],
            content={**result.payload(), "proposal_id": proposal_id, "risk_check": risk},
        )
    finally:
        if injected is None:
            rds.close()
