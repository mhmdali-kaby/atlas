"""POST /internal/execute/{risk_check_id} (ATL-143): Laravel asks the Executor to send an approved order.

Signed like every /internal route (InternalSignatureMiddleware); an unsigned call gets 401 before this
code runs. Body (optional JSON): ``{"approved_by": "owner"}`` when the owner approved an
``awaiting_owner`` risk check. Answers 200 when the order was sent with a confirmed stop, 409 when the
Executor refused without contacting OKX, 422 for a malformed body and 502 when OKX refused it or no
stop could be confirmed. The body is :meth:`ExecutionResult.payload`.

Tests override the OKX client with ``app.state.executor_client_factory`` and Redis with
``app.state.executor_redis``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from trading_core.execution.executor import execute
from trading_core.security import PROTECTED_PREFIX

router = APIRouter(prefix=PROTECTED_PREFIX)

_STATUS = {"sent": 200, "refused": 409, "failed": 502, "unprotected_closed": 502}


@router.post("/execute/{risk_check_id}", response_model=None)
async def execute_risk_check(risk_check_id: str, request: Request) -> JSONResponse:
    raw = await request.body()
    approved_by: Any = None
    if raw.strip():
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_json"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_body"})
        approved_by = body.get("approved_by")
        if approved_by is not None and not isinstance(approved_by, str):
            return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_approved_by"})
    state = request.app.state
    result = await execute(
        risk_check_id,
        approved_by,
        engine=state.engine,
        redis_client=getattr(state, "executor_redis", None),
        client_factory=getattr(state, "executor_client_factory", None),
        app_key=state.settings.app_key,
        active_env=state.settings.env,
    )
    return JSONResponse(status_code=_STATUS[result.status], content=result.payload())
