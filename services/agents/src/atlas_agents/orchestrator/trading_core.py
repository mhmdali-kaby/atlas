"""The agents process's signed client for trading_core's internal API (decision #235, ATL-160).

Two calls: ``POST /internal/risk/evaluate/{proposal_id}`` (the Risk Engine on a proposal whose vote
passed) and, only for a ``send_now`` decision at autonomy 3-4 (ATL-213, decision #254),
``POST /internal/proposals/{proposal_id}/execute`` with ``approved_by = "autonomy:<level>"`` — the
same route the owner's Approve uses. That route re-runs the Risk Engine and calls the Executor only
if the fresh check says ``send_now``. There is deliberately no direct Executor or OKX call here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Final

import httpx
from atlas_shared.defaults import DEFAULTS
from atlas_shared.internal_api import signed_headers

RISK_EVALUATE_PATH: Final = "/internal/risk/evaluate/{proposal_id}"
EXECUTE_PATH: Final = "/internal/proposals/{proposal_id}/execute"


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
        self.execute_timeout = max(self.timeout, float(cfg.get("execute_timeout_seconds", self.timeout)))
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

    async def execute_proposal(self, proposal_id: str, approved_by: str) -> tuple[int, dict[str, Any]]:
        """Signed ``POST /internal/proposals/{id}/execute``; returns ``(http_status, body)``.

        A refusal (409: ``risk_rejected``, ``needs_owner``, ``not_sent``…) or an Executor failure (502)
        is an answer, not an error: its body carries ``status``/``reason``/``detail`` and the
        re-check's ``risk_check``. Raises :class:`TradingCoreError` only when trading_core could not
        be reached or did not answer with a JSON object."""
        if not self._secret:
            raise TradingCoreError("not_configured", "INTERNAL_SHARED_SECRET is not set")
        path = EXECUTE_PATH.format(proposal_id=proposal_id)
        payload = json.dumps({"approved_by": approved_by}).encode()
        headers = {**signed_headers(self._secret, "POST", path, payload), "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.execute_timeout, transport=self._transport) as client:
                resp = await client.post(self.base_url + path, content=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise TradingCoreError("unavailable", type(exc).__name__) from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise TradingCoreError(f"http_{resp.status_code}", str(resp.status_code))
        return resp.status_code, body


__all__ = ["EXECUTE_PATH", "RISK_EVALUATE_PATH", "TradingCoreClient", "TradingCoreError"]
