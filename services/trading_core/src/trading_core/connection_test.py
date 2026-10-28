"""Test connection (ATL-8, brief §14.2 and §18, decision #237).

POST /internal/connections/{id}/test loads the ``connections`` row, decrypts its key with APP_KEY
(decision #235), calls OKX ``account/config`` and writes the verification columns back.

- A live key whose permissions include ``withdraw`` is refused: ok=false, error
  ``withdraw_permission_enabled`` and ``last_verified_at`` stays null.
- A demo key with ``withdraw`` is verified (CEO instruction, ATL-30) and the answer carries
  ``warnings: ["withdraw_permission_enabled"]`` so the UI and doctor can say so. Atlas still has no
  withdrawal or transfer code (brief rule 5).
- Spot mode (acctLv "1") still passes; the raw ``acct_lv`` is returned so the UI can warn.
- The full UID is never stored, logged or returned: only its last 4 characters leave this module.

Errors are returned as short slugs (``error``) plus the OKX code (``okx_code``); Laravel turns the
slug into the human message from its lang files.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from atlas_shared.db import Connection
from atlas_shared.laravel_crypt import DecryptError
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from trading_core.okx import (
    OkxClient,
    OkxClientError,
    OkxError,
    OkxNetworkError,
    client_for_connection,
)
from trading_core.security import PROTECTED_PREFIX

logger = logging.getLogger("trading_core.connection_test")

# OKX account/config acctLv (documented values). Anything else is stored as "unknown".
ACCOUNT_MODES: dict[str, str] = {
    "1": "spot",
    "2": "futures",
    "3": "multi_currency_margin",
    "4": "portfolio_margin",
}
UNKNOWN_ACCOUNT_MODE = "unknown"

WITHDRAW_PERMISSION = "withdraw"
WITHDRAW_ERROR = "withdraw_permission_enabled"
# Environments where a withdraw-enabled key is accepted with a warning instead of refused.
WITHDRAW_TOLERATED_ENVS = frozenset({"demo"})

# OKX error codes → Atlas error slugs. Everything else is "okx_error" with the code attached.
OKX_ERROR_SLUGS: dict[str, str] = {
    "50111": "invalid_credentials",  # Invalid OK-ACCESS-KEY
    "50113": "invalid_credentials",  # Invalid Sign (wrong secret)
    "50105": "invalid_credentials",  # OK-ACCESS-PASSPHRASE incorrect
    "50101": "wrong_environment",  # APIKey does not match current environment
    "50110": "ip_not_allowed",  # IP not in the key's allow-list
    "50102": "clock_out_of_sync",  # Timestamp request expired
    "50100": "key_frozen",  # API frozen
    "50119": "key_not_found",  # API key doesn't exist
}


def account_mode_name(acct_lv: str) -> str:
    return ACCOUNT_MODES.get(acct_lv.strip(), UNKNOWN_ACCOUNT_MODE)


def uid_last4(uid: str) -> str | None:
    uid = uid.strip()
    return uid[-4:] if uid else None


@dataclass
class ConnectionTestResult:
    ok: bool
    acct_lv: str | None = None
    account_mode: str | None = None
    position_mode: str | None = None
    permissions: list[str] = field(default_factory=list)
    uid_last4: str | None = None
    error: str | None = None
    okx_code: str | None = None
    verified_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "acct_lv": self.acct_lv,
            "account_mode": self.account_mode,
            "position_mode": self.position_mode,
            "permissions": self.permissions,
            "warnings": self.warnings,
            "uid_last4": self.uid_last4,
            "error": self.error,
            "okx_code": self.okx_code,
            "verified_at": None
            if self.verified_at is None
            else self.verified_at.isoformat().replace("+00:00", "Z"),
        }


class ConnectionNotFound(LookupError):
    pass


ClientFactory = Callable[[Connection, str], OkxClient]


def _default_client(row: Connection, app_key: str) -> OkxClient:
    return client_for_connection(row, app_key)


async def run_connection_test(
    connection_id: uuid.UUID,
    *,
    engine: Engine,
    app_key: str,
    client_factory: ClientFactory = _default_client,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ConnectionTestResult:
    """Run account/config for one stored connection and record the outcome on its row."""
    with Session(engine, expire_on_commit=False) as session:
        row = session.get(Connection, connection_id)
    if row is None:
        raise ConnectionNotFound(str(connection_id))

    result = await _probe(row, app_key, client_factory)
    if result.ok:
        result.verified_at = now()

    values: dict[str, Any] = {
        "last_verified_at": result.verified_at,
        "last_test_error": result.error,
        "updated_at": now(),
    }
    if result.account_mode is not None:  # account/config answered: record what it said
        values |= {
            "account_mode": result.account_mode,
            "position_mode": result.position_mode,
            "permissions": result.permissions,
            "uid_last4": result.uid_last4,
        }
    with engine.begin() as conn:
        conn.execute(update(Connection).where(Connection.id == connection_id).values(**values))

    logger.info(
        "connection tested",
        extra={"connection_id": str(connection_id), "ok": result.ok, "error": result.error},
    )
    return result


async def _probe(row: Connection, app_key: str, client_factory: ClientFactory) -> ConnectionTestResult:
    try:
        client = client_factory(row, app_key)
    except DecryptError:
        return ConnectionTestResult(ok=False, error="decrypt_failed")

    try:
        async with client as okx:
            cfg = await okx.get_account_config()
    except OkxError as exc:
        return ConnectionTestResult(
            ok=False, error=OKX_ERROR_SLUGS.get(exc.code, "okx_error"), okx_code=exc.code
        )
    except OkxNetworkError:
        return ConnectionTestResult(ok=False, error="okx_unreachable")
    except OkxClientError:
        return ConnectionTestResult(ok=False, error="okx_error")

    permissions = sorted(cfg.permissions)
    result = ConnectionTestResult(
        ok=True,
        acct_lv=cfg.acct_lv,
        account_mode=account_mode_name(cfg.acct_lv),
        position_mode=cfg.pos_mode,
        permissions=permissions,
        uid_last4=uid_last4(cfg.uid),
    )
    if WITHDRAW_PERMISSION in permissions:
        if row.env in WITHDRAW_TOLERATED_ENVS:
            result.warnings.append(WITHDRAW_ERROR)
        else:
            result.ok = False
            result.error = WITHDRAW_ERROR
    return result


router = APIRouter(prefix=PROTECTED_PREFIX)


@router.post("/connections/{connection_id}/test", response_model=None)
async def test_connection_route(connection_id: uuid.UUID, request: Request) -> dict[str, Any] | JSONResponse:
    settings = request.app.state.settings
    engine: Engine = request.app.state.engine
    factory: ClientFactory = getattr(request.app.state, "okx_client_factory", _default_client)
    if not settings.app_key:
        return JSONResponse(status_code=503, content={"ok": False, "error": "app_key_missing"})
    try:
        result = await run_connection_test(
            connection_id, engine=engine, app_key=settings.app_key, client_factory=factory
        )
    except ConnectionNotFound:
        return JSONResponse(status_code=404, content={"ok": False, "error": "connection_not_found"})
    return result.as_dict()
