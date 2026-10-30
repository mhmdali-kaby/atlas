"""The agents process's signed client for trading_core's internal API (decision #235, ATL-160).

The Orchestrator's cycle uses exactly one call: ``POST /internal/risk/evaluate/{proposal_id}``, the
Risk Engine on a proposal whose vote passed. There is deliberately no Executor call here: an order is
sent only after the owner approves (ATL-162), through Laravel.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

import httpx
from atlas_shared.defaults import DEFAULTS
from atlas_shared.internal_api import signed_headers

RISK_EVALUATE_PATH: Final = "/internal/risk/evaluate/{proposal_id}"


class TradingCoreError(Exception):
    """trading_core refused the call or could not be reached. ``code`` is trading_core's ``error``
    (``risk_engine_error``, ``unknown_proposal``) or ``unavailable`` / ``not_configured`` /
    ``http_<status>``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _cfg(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["orchestrator"]
    return cfg


class TradingCoreClient:
    """``base_url`` defaults to TRADING_CORE_URL, ``secret`` to INTERNAL_SHARED_SECRET (both exported
    from backend/.env by ``./atlas up``). The secret is never logged or put in an error."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        secret: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        cfg = _cfg()
        self.base_url = (
            base_url or os.environ.get("TRADING_CORE_URL") or str(cfg["trading_core_url"])
        ).rstrip("/")
        self._secret = secret if secret is not None else os.environ.get("INTERNAL_SHARED_SECRET", "")
        self.timeout = float(timeout if timeout is not None else cfg["risk_timeout_seconds"])
        self._transport = transport

    async def risk_evaluate(self, proposal_id: str) -> dict[str, Any]:
        """The Risk Engine's decision body (``ok``, ``risk_check_id``, ``result``, ``decision``,
        ``reason``, ``failed_check``, ``binding_cap``, ``final_contracts``, ``final_notional_usd``,
        ``leverage``, ``settings_version``, ``checks``). Raises :class:`TradingCoreError`."""
        if not self._secret:
            raise TradingCoreError("not_configured", "INTERNAL_SHARED_SECRET is not set")
        path = RISK_EVALUATE_PATH.format(proposal_id=proposal_id)
        headers = signed_headers(self._secret, "POST", path, b"")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                resp = await client.post(self.base_url + path, content=b"", headers=headers)
        except httpx.HTTPError as exc:
            raise TradingCoreError("unavailable", type(exc).__name__) from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code != 200 or not isinstance(body, dict) or body.get("ok") is not True:
            code = body.get("error") if isinstance(body, dict) else None
            detail = body.get("detail") if isinstance(body, dict) else None
            raise TradingCoreError(str(code or f"http_{resp.status_code}"), str(detail or resp.status_code))
        return body


__all__ = ["RISK_EVALUATE_PATH", "TradingCoreClient", "TradingCoreError"]
