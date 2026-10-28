"""trading_core's internal FastAPI service (decision #235). Binds 127.0.0.1 only.

Unsigned: GET /health. Everything under /internal requires an HMAC signature (see security.py).
Add new internal routes to `internal_router`; the middleware already protects them.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from atlas_shared.db import make_engine
from atlas_shared.defaults import DEFAULTS
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from trading_core.config import Settings
from trading_core.connection_test import router as connection_test_router
from trading_core.logs import AccessLogMiddleware
from trading_core.okx.client import OkxClient
from trading_core.okx.errors import OkxClientError, OkxError, OkxNetworkError
from trading_core.scheduler import Scheduler
from trading_core.security import PROTECTED_PREFIX, InternalSignatureMiddleware
from trading_core.sync.account import STREAM as ACCOUNT_JOB
from trading_core.sync.account import account_job
from trading_core.sync.instruments import STREAM as INSTRUMENTS_JOB
from trading_core.sync.instruments import instruments_job

logger = logging.getLogger("trading_core")

try:
    VERSION = version("atlas-trading-core")
except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
    VERSION = "0.0.0"

internal_router = APIRouter(prefix=PROTECTED_PREFIX)


@internal_router.get("/ping")
async def internal_ping() -> dict[str, bool]:
    """Signed no-op, so Laravel can check the shared secret end to end."""
    return {"ok": True}


def _public_client(env: str) -> OkxClient:
    return OkxClient(env)


@internal_router.get("/time", response_model=None)
async def okx_time(request: Request) -> dict[str, Any] | JSONResponse:
    """OKX ``public/time`` for the active env and this Mac's clock drift from it (atlas:doctor, ATL-13).

    ``offset_ms`` is server minus local (midpoint of the round trip); ``drift_ms`` is its absolute value.
    Tests override the client with ``app.state.okx_public_client_factory``.
    """
    settings: Settings = request.app.state.settings
    factory = getattr(request.app.state, "okx_public_client_factory", _public_client)
    try:
        async with factory(settings.env) as okx:
            offset_ms = await okx.sync_clock()
    except OkxNetworkError:
        return JSONResponse(status_code=502, content={"ok": False, "error": "okx_unreachable"})
    except OkxError as exc:
        return JSONResponse(
            status_code=502, content={"ok": False, "error": "okx_error", "okx_code": exc.code}
        )
    except OkxClientError:
        return JSONResponse(status_code=502, content={"ok": False, "error": "okx_error"})
    local = datetime.now(UTC)
    server_ms = int(local.timestamp() * 1000) + offset_ms
    return {
        "ok": True,
        "env": settings.env,
        "server_time": datetime.fromtimestamp(server_ms / 1000, UTC).isoformat().replace("+00:00", "Z"),
        "local_time": local.isoformat().replace("+00:00", "Z"),
        "offset_ms": offset_ms,
        "drift_ms": abs(offset_ms),
    }


@internal_router.post("/sync/instruments", response_model=None)
async def sync_instruments_now(request: Request) -> dict[str, Any] | JSONResponse:
    """Run the instruments sync for the active env now (waits for a scheduled run in flight)."""
    scheduler: Scheduler = request.app.state.scheduler
    try:
        result: dict[str, Any] = await scheduler.run_now(INSTRUMENTS_JOB)
    except Exception as exc:
        logger.warning("instruments sync failed", extra={"error": type(exc).__name__})
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "instruments_sync_failed",
                "detail": f"{type(exc).__name__}: {exc}",
            },
        )
    return {"ok": True, **result}


@internal_router.post("/sync/account", response_model=None)
async def sync_account_now(request: Request) -> dict[str, Any] | JSONResponse:
    """Re-sync now: balances, positions and the equity point for the active env (409 if no
    verified connection)."""
    scheduler: Scheduler = request.app.state.scheduler
    try:
        result: dict[str, Any] = await scheduler.run_now(ACCOUNT_JOB)
    except Exception as exc:
        logger.warning("account sync failed", extra={"error": type(exc).__name__})
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "account_sync_failed",
                "detail": f"{type(exc).__name__}: {exc}",
            },
        )
    if result.get("skipped"):
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": result["reason"], "env": result["env"]},
        )
    return {"ok": True, **result}


def default_scheduler(settings: Settings, engine: Engine | None = None) -> Scheduler:
    """The jobs trading_core runs in production."""
    scheduler = Scheduler()
    engine = engine if engine is not None else make_engine()
    scheduler.add(
        INSTRUMENTS_JOB,
        DEFAULTS["sync"]["instruments_interval_seconds"],
        instruments_job(settings.env, engine),
    )
    scheduler.add(
        ACCOUNT_JOB,
        DEFAULTS["sync"]["account_interval_seconds"],
        account_job(settings.env, engine, settings.app_key),
    )
    return scheduler


def create_app(
    settings: Settings | None = None,
    extra_internal: APIRouter | None = None,
    *,
    scheduler: Scheduler | None = None,
    engine: Engine | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.internal_shared_secret:
        logger.warning("INTERNAL_SHARED_SECRET is empty; every /internal call will be rejected")
    db = engine if engine is not None else make_engine()
    jobs = scheduler if scheduler is not None else default_scheduler(settings, db)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.run_scheduler:
            jobs.start()
        try:
            yield
        finally:
            await jobs.stop()

    application = FastAPI(
        title="Atlas trading_core", version=VERSION, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    application.state.settings = settings
    application.state.scheduler = jobs
    application.state.engine = db

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.env, "version": VERSION}

    application.include_router(internal_router)
    application.include_router(connection_test_router)
    if extra_internal is not None:
        application.include_router(extra_internal)
    # Last added runs first: the access log wraps the signature check, so rejected calls are logged too.
    application.add_middleware(
        InternalSignatureMiddleware,
        secret=settings.internal_shared_secret,
        max_skew_seconds=settings.max_clock_skew_seconds,
    )
    application.add_middleware(AccessLogMiddleware)
    return application


app = create_app()
