"""Signed ``POST /internal/proposals/{proposal_id}/execute`` (ATL-162): the owner approved a proposal.

Body: ``{"approved_by": "owner"}`` (required). Laravel already checked the status, the expiry and the
settings version; this endpoint checks the status and the expiry again under the row lock, then:

1. re-runs the nine §10 checks (:func:`trading_core.risk.engine.evaluate` with
   ``allowed_statuses=("awaiting_owner",)``, which moves the row to ``risk_checked`` — a second
   approval of the same proposal is refused there);
2. a rejected re-check → proposal ``rejected`` / ``risk_rejected``; autonomy now 1 → ``advise_only``.
   In both cases **the Executor is not called**;
3. otherwise ``execute(new_risk_check_id, "owner")``. An Executor refusal or failure that leaves the
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
from trading_core.risk.api import decision_body
from trading_core.risk.engine import RiskDecision, RiskEngineError, evaluate
from trading_core.security import PROTECTED_PREFIX

logger = logging.getLogger("trading_core.approvals")

router = APIRouter(prefix=PROTECTED_PREFIX + "/proposals")

_STATUS = {"sent": 200, "refused": 409, "failed": 502, "unprotected_closed": 502}

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
    pid: uuid.UUID, engine: Engine, rds: redis.Redis, now: Callable[[], datetime]
) -> RiskDecision | _Refusal:
    """Status/expiry gate, then the fresh risk check. A rejected or advise-only result closes the
    proposal here, so the caller never reaches the Executor with it."""
    at = now()
    with Session(engine) as s:
        row = s.get(Proposal, pid)
        if row is None:
            return _Refusal(404, "proposal_not_found", "No proposal with this id.")
        if row.status != "awaiting_owner":
            return _Refusal(
                409, "not_awaiting_owner", f"The proposal is {row.status}, not awaiting approval."
            )
        if row.approval_expires_at is None or row.approval_expires_at <= at:
            return _Refusal(409, "approval_expired", "The approval window has closed.")
    try:
        decision = evaluate(
            pid, engine=engine, redis_client=rds, now=now, allowed_statuses=("awaiting_owner",)
        )
    except RiskEngineError as exc:
        return _Refusal(409, "risk_engine_error", str(exc))

    if decision.result == "rejected":
        status, reason = "rejected", "risk_rejected"
    elif decision.decision == "advise_only":
        status, reason = "advise_only", "advise_only"
    else:
        return decision
    _close(engine, rds, pid, status, reason, now(), decision)
    return decision


def _close(
    engine: Engine,
    rds: redis.Redis,
    pid: uuid.UUID,
    status: str,
    reason: str,
    at: datetime,
    decision: RiskDecision | None = None,
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
                decided_by="owner",
                decided_at=at,
                updated_at=at,
            )
            .returning(Proposal)
        )
        if row is None:
            return
        extra: dict[str, Any] = {"decided_by": "owner"}
        if decision is not None:
            extra.update(risk_check_id=str(decision.risk_check_id), result=decision.result)
        payload = proposal_payload(row, **extra)
    publish(rds, PROPOSALS_STREAM, f"proposal.{status}", row, payload, SOURCE)


def _mark_approved(engine: Engine, pid: uuid.UUID, at: datetime) -> None:
    with Session(engine) as s, s.begin():
        s.execute(update(Proposal).where(Proposal.id == pid).values(decided_by="owner", decided_at=at))


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
    if not isinstance(body, dict) or body.get("approved_by") != "owner":
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_approved_by"})

    state = request.app.state
    engine: Engine = state.engine
    injected: redis.Redis | None = getattr(state, "risk_redis", None)
    now: Callable[[], datetime] = getattr(state, "clock", None) or (lambda: datetime.now(UTC))
    run: ExecuteFn = getattr(state, "approval_execute", None) or execute
    rds = injected if injected is not None else bus.make_client()
    try:
        checked = await asyncio.to_thread(_recheck, pid, engine, rds, now)
        if isinstance(checked, _Refusal):
            return _refused(proposal_id, checked)
        risk = {k: v for k, v in decision_body(checked).items() if k != "ok"}
        if checked.result == "rejected":
            return _refused(proposal_id, _Refusal(409, "risk_rejected", checked.reason or ""), risk)
        if checked.decision == "advise_only":
            return _refused(
                proposal_id, _Refusal(409, "advise_only", "Autonomy is Advise only; nothing is sent."), risk
            )

        await asyncio.to_thread(_mark_approved, engine, pid, now())
        result = await run(
            checked.risk_check_id,
            "owner",
            engine=engine,
            redis_client=rds,
            client_factory=getattr(state, "executor_client_factory", None),
            app_key=state.settings.app_key,
            active_env=state.settings.env,
        )
        if result.status != "sent" and await asyncio.to_thread(_status_of, engine, pid) == "risk_checked":
            await asyncio.to_thread(
                _close, engine, rds, pid, "rejected", f"execution_{result.reason}"[:64], now()
            )
        logger.info(
            "owner approval executed",
            extra={"proposal_id": proposal_id, "status": result.status, "reason": result.reason},
        )
        return JSONResponse(
            status_code=_STATUS[result.status],
            content={**result.payload(), "proposal_id": proposal_id, "risk_check": risk},
        )
    finally:
        if injected is None:
            rds.close()
