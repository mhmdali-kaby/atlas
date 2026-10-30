"""Signed internal routes for the owner's trading controls (ATL-147, brief §10, decision #251).

All are under ``/internal`` and HMAC-signed (InternalSignatureMiddleware answers 401 otherwise):

- ``POST /internal/system/pause`` ``{"reason"?: str}`` → 200 ``system.trading_state`` payload
- ``POST /internal/system/resume`` → 200 payload (clears owner-clearable breakers, unpauses)
- ``POST /internal/system/kill`` ``{"flatten": bool}`` → 200 kill report
- ``POST /internal/positions/{inst_id}/close`` ``{"pos_side"?: "net"|"long"|"short"}`` → 200 / 404 / 502
- ``POST /internal/orders/{order_id}/cancel`` → 200 / 404 / 409 / 502

They act on the env trading_core runs for. Tests override the OKX client with
``app.state.executor_client_factory`` and Redis with ``app.state.executor_redis`` (as for /execute).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from trading_core.execution import kill as control
from trading_core.security import PROTECTED_PREFIX

router = APIRouter(prefix=PROTECTED_PREFIX)

POS_SIDES = ("net", "long", "short")


async def _body(request: Request) -> dict[str, Any] | JSONResponse:
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_json"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_body"})
    return body


def _deps(request: Request) -> dict[str, Any]:
    state = request.app.state
    return {
        "engine": state.engine,
        "redis_client": getattr(state, "executor_redis", None),
        "client_factory": getattr(state, "executor_client_factory", None),
        "app_key": state.settings.app_key,
    }


def _refused(err: control.ControlError) -> JSONResponse:
    return JSONResponse(
        status_code=err.status, content={"ok": False, "error": err.reason, "detail": err.detail}
    )


@router.post("/system/pause", response_model=None)
async def pause(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_reason"})
    state = request.app.state
    payload = control.pause(
        state.engine, state.settings.env, reason=reason, redis_client=getattr(state, "executor_redis", None)
    )
    return JSONResponse(status_code=200, content={"ok": True, **payload})


@router.post("/system/resume", response_model=None)
async def resume(request: Request) -> JSONResponse:
    state = request.app.state
    payload = control.resume(
        state.engine, state.settings.env, redis_client=getattr(state, "executor_redis", None)
    )
    return JSONResponse(status_code=200, content={"ok": True, **payload})


@router.post("/system/kill", response_model=None)
async def kill(request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    flatten = body.get("flatten", False)
    if not isinstance(flatten, bool):
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_flatten"})
    report = await control.kill(request.app.state.settings.env, flatten=flatten, **_deps(request))
    return JSONResponse(status_code=200, content=report)


@router.post("/positions/{inst_id}/close", response_model=None)
async def close_position(inst_id: str, request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    pos_side = body.get("pos_side")
    if pos_side is not None and pos_side not in POS_SIDES:
        return JSONResponse(status_code=422, content={"ok": False, "error": "invalid_pos_side"})
    try:
        report = await control.close_position(
            request.app.state.settings.env, inst_id, pos_side, **_deps(request)
        )
    except control.ControlError as err:
        return _refused(err)
    return JSONResponse(status_code=200 if report["ok"] else 502, content=report)


@router.post("/orders/{order_id}/cancel", response_model=None)
async def cancel_order(order_id: str, request: Request) -> JSONResponse:
    try:
        report = await control.cancel_order(request.app.state.settings.env, order_id, **_deps(request))
    except control.ControlError as err:
        return _refused(err)
    return JSONResponse(status_code=200 if report["ok"] else 502, content=report)
