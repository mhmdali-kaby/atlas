"""trading_core's internal FastAPI service (decision #235). Binds 127.0.0.1 only.

Unsigned: GET /health. Everything under /internal requires an HMAC signature (see security.py).
Add new internal routes to `internal_router`; the middleware already protects them.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, FastAPI

from trading_core.config import Settings
from trading_core.logs import AccessLogMiddleware
from trading_core.security import PROTECTED_PREFIX, InternalSignatureMiddleware

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


def create_app(settings: Settings | None = None, extra_internal: APIRouter | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.internal_shared_secret:
        logger.warning("INTERNAL_SHARED_SECRET is empty; every /internal call will be rejected")

    application = FastAPI(title="Atlas trading_core", version=VERSION, docs_url=None, redoc_url=None)
    application.state.settings = settings

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.env, "version": VERSION}

    application.include_router(internal_router)
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
