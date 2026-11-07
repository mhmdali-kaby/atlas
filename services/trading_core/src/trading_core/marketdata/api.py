"""Signed ``POST /internal/quality/check`` (ATL-47): run the candle quality checks on demand.

Body as for the backfill routes (both optional): ``{"env": "demo", "inst_id": "BTC-USDT-SWAP"}``.

- With an ``inst_id``: that instrument's series are checked (and its open gaps repaired) now →
  200 ``{"ok": true, "env", "inst_id", "series", "found": {kind: n}, "new", "repaired", "repair_failed"}``.
- Without: the scheduled ``quality`` job is started in the background → 202
  ``{"ok": true, "env", "inst_id": null, "started"}`` (``started`` false when a run is in flight).

Errors (422 invalid, 409 env_not_active) are the backfill routes' (``backfill.api._target``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from trading_core.backfill.api import _target
from trading_core.marketdata.quality import JOB, QualityChecker
from trading_core.scheduler import Scheduler
from trading_core.security import PROTECTED_PREFIX

logger = logging.getLogger("trading_core.marketdata")

router = APIRouter(prefix=PROTECTED_PREFIX + "/quality")


@router.post("/check", response_model=None)
async def check_quality(request: Request) -> JSONResponse:
    target = await _target(request)
    if isinstance(target, JSONResponse):
        return target
    env, inst_id = target
    if inst_id is None:
        scheduler: Scheduler = request.app.state.scheduler
        started = scheduler.trigger(JOB) if JOB in scheduler.jobs else False
        logger.info("quality check requested", extra={"env": env, "started": started})
        return JSONResponse(
            status_code=202, content={"ok": True, "env": env, "inst_id": None, "started": started}
        )
    engine: Engine = request.app.state.engine
    result = await QualityChecker(env, engine).run(inst_id)
    return JSONResponse(status_code=200, content={"ok": True, **result.as_dict(), "inst_id": inst_id})
