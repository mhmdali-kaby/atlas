"""Signed ``POST /internal/risk/check`` (ATL-128, decision #249 F).

The owner's manual risk check: a hand-written :class:`~atlas_shared.proposals.TradeProposal` (JSON,
Decimal fields) is stored as a ``proposals`` row (``author = owner:manual``, ``status = draft``, env =
the env this trading_core runs) and run through :func:`trading_core.risk.engine.evaluate`, then closed
as ``expired`` so a what-if never blocks an agent proposal or reaches the Executor. Nothing is
sent to OKX: evaluate reads Postgres and the market-data hash only, and this module imports no OKX
client. The signature middleware already rejects unsigned and replayed calls with 401.

200 ``{"ok": true, "proposal_id", "risk_check_id", "env", "settings_version", "result", "decision",
"reason", "failed_check", "binding_cap", "final_contracts", "final_notional_usd", "leverage",
"checks": [9 entries]}`` (decimals as plain strings, trailing zeros dropped).
422 ``{"ok": false, "error": "invalid", "errors": [{"loc", "msg", "type"}]}`` for a body that is not
a valid TradeProposal (no stop, stop on the wrong side, unknown field ...); nothing is written.
409 ``{"ok": false, "error": "risk_engine_error", "detail"}`` when the engine cannot run (no settings
row); the draft row is removed again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import redis
from atlas_shared import bus
from atlas_shared.db import Proposal
from atlas_shared.proposals import TradeProposal
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session

from trading_core.config import Settings
from trading_core.risk.engine import RiskDecision, RiskEngineError, evaluate
from trading_core.security import PROTECTED_PREFIX

logger = logging.getLogger("trading_core.risk")

router = APIRouter(prefix=PROTECTED_PREFIX + "/risk")

AUTHOR = "owner:manual"
STATUS = "draft"
# A manual check is a what-if: once evaluated it is closed at once, so it never counts as a pending
# proposal (the Strategist's no_duplicate check) and can never be approved and sent by the Executor.
FINAL_STATUS = "expired"


def _s(value: Decimal | None) -> str | None:
    """A plain decimal string without trailing zeros or an exponent (250, 1000.5)."""
    return None if value is None else format(value.normalize(), "f")


def proposal_row(model: TradeProposal, env: str, at: datetime) -> Proposal:
    """The draft ``proposals`` row for a hand-written entry (same column mapping as the agents'
    submit_proposal)."""
    return Proposal(
        id=uuid.uuid4(),
        env=env,
        cycle_id=None,
        version=1,
        parent_id=None,
        kind="entry",
        author=AUTHOR,
        inst_id=model.inst_id,
        side=model.side,
        order_type=model.order_type,
        entry=model.entry,
        entry_zone=[str(v) for v in model.entry_zone] if model.entry_zone else None,
        stop=model.stop,
        take_profits=[{"price": str(t.price), "pct": str(t.pct)} for t in model.take_profits],
        time_stop_minutes=model.time_stop_minutes,
        size_suggestion_pct=model.size_suggestion_pct_equity,
        leverage_suggestion=model.leverage_suggestion,
        expected_r=model.expected_r_multiple,
        probability=model.probability_estimate,
        rationale_md=model.rationale_md,
        invalidation=model.invalidation,
        evidence=[e.model_dump() for e in model.evidence],
        status=STATUS,
        created_at=at,
        updated_at=at,
    )


def decision_body(decision: RiskDecision) -> dict[str, Any]:
    sizing = decision.checks[3] if len(decision.checks) > 3 else {}
    binding = (sizing.get("values") or {}).get("binding_cap") if decision.result != "rejected" else None
    return {
        "ok": True,
        "proposal_id": str(decision.proposal_id),
        "risk_check_id": str(decision.risk_check_id),
        "env": decision.env,
        "settings_version": decision.settings_version,
        "result": decision.result,
        "decision": decision.decision,
        "reason": decision.reason,
        "failed_check": decision.failed_check,
        "binding_cap": binding,
        "final_contracts": _s(decision.final_size),
        "final_notional_usd": _s(decision.final_notional_usd),
        "leverage": decision.leverage,
        "checks": decision.checks,
    }


def _run(
    model: TradeProposal,
    env: str,
    engine: Engine,
    rds: redis.Redis,
    now: Callable[[], datetime],
) -> RiskDecision:
    row = proposal_row(model, env, now())
    pid = row.id
    with Session(engine) as session, session.begin():
        session.add(row)
    try:
        decision = evaluate(pid, engine=engine, redis_client=rds, now=now, allowed_statuses=(STATUS,))
    except RiskEngineError:
        with engine.begin() as c:
            c.execute(delete(Proposal).where(Proposal.id == pid))
        raise
    with engine.begin() as c:
        c.execute(
            update(Proposal).where(Proposal.id == pid).values(status=FINAL_STATUS, updated_at=now())
        )
    return decision


@router.post("/check", response_model=None)
async def risk_check(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    raw = await request.body()
    try:
        model = TradeProposal.model_validate_json(raw)
    except ValidationError as exc:
        errors = json.loads(exc.json(include_url=False, include_input=False, include_context=False))
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid", "errors": errors})

    engine: Engine = request.app.state.engine
    # Tests inject a Redis client and a fixed clock on app.state; production opens one client per call.
    injected: redis.Redis | None = getattr(request.app.state, "risk_redis", None)
    now: Callable[[], datetime] = getattr(request.app.state, "clock", None) or (lambda: datetime.now(UTC))
    rds = injected if injected is not None else bus.make_client()
    try:
        decision = await asyncio.to_thread(_run, model, settings.env, engine, rds, now)
    except RiskEngineError as exc:
        logger.warning("manual risk check could not run", extra={"error": str(exc)})
        return JSONResponse(
            status_code=409, content={"ok": False, "error": "risk_engine_error", "detail": str(exc)}
        )
    finally:
        if injected is None:
            rds.close()
    return JSONResponse(status_code=200, content=decision_body(decision))
