"""Signed ``POST /internal/backfill/start`` and ``/internal/backfill/pause`` (ATL-46).

Body (both optional): ``{"env": "demo", "inst_id": "BTC-USDT-SWAP"}`` as sent by Laravel's Data API
(ATL-48). ``env`` must be the env this trading_core runs (409 ``env_not_active`` otherwise);
``inst_id`` null = every eligible instrument. The signature middleware already rejects unsigned calls.

start → 202 ``{"ok": true, "env", "inst_id", "jobs", "created", "resumed", "instruments", "started"}``:
creates the missing jobs, re-queues paused/failed ones, and starts the runner in the background
(``started`` false when it was already running; that run picks the new jobs up).
pause → 202 ``{"ok": true, "env", "inst_id", "jobs"}`` with the number of jobs paused.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from atlas_shared.defaults import DEFAULTS
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from trading_core.backfill.plan import ensure_jobs, pause_jobs
from trading_core.backfill.runner import STREAM
from trading_core.config import Settings
from trading_core.scheduler import Scheduler
from trading_core.security import PROTECTED_PREFIX

logger = logging.getLogger("trading_core.backfill")

router = APIRouter(prefix=PROTECTED_PREFIX + "/backfill")

_INST_ID = re.compile(r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")


def _invalid(field: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"ok": False, "error": "invalid", "field": field, "detail": message}
    )


async def _target(request: Request) -> tuple[str, str | None] | JSONResponse:
    settings: Settings = request.app.state.settings
    raw = await request.body()
    try:
        body: Any = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _invalid("body", "body must be a JSON object")
    if not isinstance(body, dict):
        return _invalid("body", "body must be a JSON object")
    env = body.get("env") or settings.env
    if env not in DEFAULTS["env"]["allowed"]:
        return _invalid("env", "env must be demo or live")
    inst_id = body.get("inst_id")
    if inst_id is not None and (
        not isinstance(inst_id, str) or len(inst_id) > 64 or not _INST_ID.match(inst_id)
    ):
        return _invalid("inst_id", "inst_id must be an OKX instrument id")
    if env != settings.env:
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": "env_not_active", "env": env, "active_env": settings.env},
        )
    return env, inst_id


@router.post("/start", response_model=None)
async def start_backfill(request: Request) -> JSONResponse:
    target = await _target(request)
    if isinstance(target, JSONResponse):
        return target
    env, inst_id = target
    engine: Engine = request.app.state.engine
    result = await asyncio.to_thread(ensure_jobs, engine, env, inst_id=inst_id)
    if inst_id is not None and result.instruments == 0:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "unknown_instrument", "env": env, "inst_id": inst_id},
        )
    scheduler: Scheduler = request.app.state.scheduler
    started = scheduler.trigger(STREAM) if STREAM in scheduler.jobs else False
    logger.info("backfill start requested", extra={"env": env, "inst_id": inst_id, **result.as_dict()})
    return JSONResponse(
        status_code=202, content={"ok": True, **result.as_dict(), "inst_id": inst_id, "started": started}
    )


@router.post("/pause", response_model=None)
async def pause_backfill(request: Request) -> JSONResponse:
    target = await _target(request)
    if isinstance(target, JSONResponse):
        return target
    env, inst_id = target
    engine: Engine = request.app.state.engine
    paused = await asyncio.to_thread(pause_jobs, engine, env, inst_id=inst_id)
    logger.info("backfill pause requested", extra={"env": env, "inst_id": inst_id, "jobs": paused})
    return JSONResponse(status_code=202, content={"ok": True, "env": env, "inst_id": inst_id, "jobs": paused})
