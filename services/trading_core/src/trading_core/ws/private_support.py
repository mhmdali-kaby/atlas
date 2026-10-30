"""Helpers for the private OKX WebSocket (ATL-142): URL, login signing, the credential source, the
frame-logging guard, and the ``account.position`` / ``account.balance`` event payloads.

Kept apart from :mod:`trading_core.ws.private` so that module stays the socket lifecycle and dispatch.
"""

from __future__ import annotations

import base64
import hmac
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from hashlib import sha256
from typing import Any

from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine
from websockets.asyncio.client import connect as ws_connect

from trading_core.execution.order_store import blank, dec, iso, s
from trading_core.okx.client import OkxCredentials
from trading_core.okx.models import AccountBalance, Position

VERIFY_PATH = "/users/self/verify"

CredentialSource = Callable[[], "OkxCredentials | None"]

# websockets logs every frame at DEBUG, and the login frame carries the API key, passphrase and
# signature. The private socket gets its own protocol logger that never goes below INFO, whatever
# level the process (or a test) sets.
_frames_logger = logging.getLogger("trading_core.ws.private.protocol")


class _NoDebug(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.INFO


_frames_logger.addFilter(_NoDebug())
_frames_logger.setLevel(logging.INFO)


def private_connector(url: str) -> AbstractAsyncContextManager[Any]:
    # OKX's keepalive is the text "ping"/"pong" pair, so the protocol-level ping is off.
    return ws_connect(
        url, ping_interval=None, open_timeout=10, close_timeout=5, max_size=2**22, logger=_frames_logger
    )


def private_cfg() -> dict[str, Any]:
    value: dict[str, Any] = DEFAULTS["private_ws"]
    return value


def private_url(env: str) -> str:
    envs: Mapping[str, Any] = DEFAULTS["okx"]["environments"]
    if env not in envs:
        raise ValueError(f"unknown OKX environment {env!r}")
    return str(envs[env]["ws_private_url"])


def login_args(creds: OkxCredentials, epoch_seconds: int) -> dict[str, str]:
    """OKX WS login: sign = Base64(HMAC-SHA256(secret, timestamp + "GET" + "/users/self/verify")),
    timestamp in Unix epoch seconds."""
    ts = str(epoch_seconds)
    digest = hmac.new(creds.secret.encode(), f"{ts}GET{VERIFY_PATH}".encode(), sha256).digest()
    return {
        "apiKey": creds.api_key,
        "passphrase": creds.passphrase,
        "timestamp": ts,
        "sign": base64.b64encode(digest).decode("ascii"),
    }


def connection_credentials(env: str, engine: Engine, app_key: str) -> CredentialSource:
    """Read the env's verified connection on every (re)connect, so a new key is picked up and a cleared
    one stops the feed."""
    from trading_core.okx.connection import credentials_from_ciphertexts
    from trading_core.sync.account import verified_connection

    def load() -> OkxCredentials | None:
        if not app_key:
            return None
        row = verified_connection(engine, env)
        if row is None:
            return None
        return credentials_from_ciphertexts(row.api_key, row.api_secret, row.passphrase, app_key)

    return load


# -- event payloads ----------------------------------------------------------------------------------


def position_payload(p: Position) -> dict[str, Any]:
    return {
        "inst_id": p.inst_id,
        "inst_type": p.inst_type,
        "pos_side": p.pos_side,
        "mgn_mode": p.mgn_mode,
        "pos": s(p.pos),
        "avg_px": s(p.avg_px),
        "mark_px": s(p.mark_px),
        "liq_px": s(p.liq_px),
        "lever": s(p.lever),
        "upl": s(p.upl),
        "upl_ratio": s(p.upl_ratio),
        "margin": s(p.margin),
        "mgn_ratio": s(p.mgn_ratio),
        "u_time": iso(p.u_time),
    }


def account_payload(bal: AccountBalance) -> dict[str, Any]:
    return {
        "channel": "account",
        "total_eq": s(bal.total_eq),
        "upl": s(bal.upl),
        "u_time": iso(bal.u_time),
        "balances": [
            {
                "ccy": d.ccy,
                "eq": s(d.eq),
                "cash_bal": s(d.cash_bal),
                "avail_bal": s(d.avail_bal),
                "frozen_bal": s(d.frozen_bal),
                "eq_usd": s(d.eq_usd),
                "upl": s(d.upl),
            }
            for d in bal.details
        ],
    }


def balance_and_position_payloads(row: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(``account.balance`` payload, ``account.position`` payload) for one balance_and_position row."""
    bal = [
        {"ccy": blank(b.get("ccy")), "cash_bal": s(dec(b.get("cashBal")))}
        for b in row.get("balData") or []
        if isinstance(b, dict)
    ]
    pos = [
        {
            "inst_id": blank(p.get("instId")),
            "inst_type": blank(p.get("instType")),
            "pos_side": blank(p.get("posSide")),
            "mgn_mode": blank(p.get("mgnMode")),
            "pos": s(dec(p.get("pos"))),
            "avg_px": s(dec(p.get("avgPx"))),
        }
        for p in row.get("posData") or []
        if isinstance(p, dict)
    ]
    common = {"channel": "balance_and_position", "event_type": blank(row.get("eventType"))}
    return {**common, "balances": bal}, {**common, "positions": pos}
