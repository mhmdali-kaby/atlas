"""Async, typed, read-only OKX v5 REST client (brief §4.1, §4.2; Phase 0).

Signs private requests, sends ``x-simulated-trading: 1`` in demo only, keeps a clock offset from
``public/time`` (refreshed every ``okx_rest.clock_sync_interval_seconds``), retries network errors, 5xx,
429 and the retryable OKX codes with jittered exponential backoff, and rate-limits each endpoint with a
token bucket. Phase 0 is read-only: there are no order, leverage, withdrawal or transfer methods here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlencode

import httpx
from atlas_shared.defaults import DEFAULTS

from trading_core.okx.errors import OkxError, OkxHttpError, OkxNetworkError, OkxResponseError
from trading_core.okx.models import (
    AccountBalance,
    AccountConfig,
    AssetBalance,
    Bill,
    DepositRecord,
    Instrument,
    OkxModel,
    Position,
    PositionHistory,
    ServerTime,
    Ticker,
    TradeFee,
    WithdrawalRecord,
)
from trading_core.okx.ratelimit import RateLimiter
from trading_core.okx.signing import iso_timestamp, sign

log = logging.getLogger("trading_core.okx")

API_PREFIX = "/api/v5/"

M = TypeVar("M", bound=OkxModel)
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class OkxCredentials:
    api_key: str
    secret: str
    passphrase: str

    def __repr__(self) -> str:  # never print secrets, even by accident
        return f"OkxCredentials(api_key=...{self.api_key[-4:]})"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    retryable_codes: frozenset[str]

    @classmethod
    def from_defaults(cls) -> RetryPolicy:
        cfg: Mapping[str, Any] = DEFAULTS["okx_rest"]
        return cls(
            max_attempts=int(cfg["max_attempts"]),
            backoff_base_seconds=float(cfg["backoff_base_seconds"]),
            backoff_max_seconds=float(cfg["backoff_max_seconds"]),
            retryable_codes=frozenset(str(c) for c in cfg["retryable_codes"]),
        )

    def delay(self, attempt: int, rand: Callable[[], float]) -> float:
        """Full-jitter backoff before retry number ``attempt`` (1-based)."""
        cap = min(self.backoff_max_seconds, self.backoff_base_seconds * (2.0 ** (attempt - 1)))
        return cap * rand()


class _Retryable(Exception):
    def __init__(self, error: Exception) -> None:
        self.error = error


class OkxClient:
    """One client per (environment, credentials). Use ``async with`` or call ``aclose()``."""

    def __init__(
        self,
        env: str,
        credentials: OkxCredentials | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        rate_limiter: RateLimiter | None = None,
        retry: RetryPolicy | None = None,
        clock_sync_interval_seconds: float | None = None,
        sleep: Sleep = asyncio.sleep,
        wall_clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        monotonic: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
    ) -> None:
        envs: Mapping[str, Any] = DEFAULTS["okx"]["environments"]
        if env not in envs:
            raise ValueError(f"unknown OKX environment {env!r}; expected one of {sorted(envs)}")
        cfg: Mapping[str, Any] = DEFAULTS["okx_rest"]
        self.env = env
        self._creds = credentials
        self._env_headers: dict[str, str] = {str(k): str(v) for k, v in envs[env]["headers"].items()}
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            base_url=str(envs[env]["rest_base_url"]), timeout=float(cfg["timeout_seconds"])
        )
        self._limiter = rate_limiter or RateLimiter(sleep=sleep)
        self._retry = retry or RetryPolicy.from_defaults()
        self._sync_interval = float(
            cfg["clock_sync_interval_seconds"]
            if clock_sync_interval_seconds is None
            else clock_sync_interval_seconds
        )
        self._sleep = sleep
        self._wall_ms = wall_clock_ms
        self._monotonic = monotonic
        self._rand = rand
        self._offset_ms = 0
        self._synced_at: float | None = None
        self._sync_lock = asyncio.Lock()

    async def __aenter__(self) -> OkxClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ---- clock -------------------------------------------------------------------------------

    @property
    def clock_offset_ms(self) -> int:
        """Server time minus local time, in milliseconds, from the last ``public/time`` sync."""
        return self._offset_ms

    async def sync_clock(self) -> int:
        """Fetch ``public/time`` and store the offset (server minus local, midpoint of the round trip)."""
        before = self._wall_ms()
        server = await self.get_server_time()
        after = self._wall_ms()
        self._offset_ms = server.epoch_ms - (before + after) // 2
        self._synced_at = self._monotonic()
        return self._offset_ms

    async def _ensure_clock(self) -> None:
        if self._synced_at is not None and self._monotonic() - self._synced_at < self._sync_interval:
            return
        async with self._sync_lock:
            if self._synced_at is None or self._monotonic() - self._synced_at >= self._sync_interval:
                await self.sync_clock()

    # ---- transport ---------------------------------------------------------------------------

    def _headers(self, method: str, request_path: str, body: str, signed: bool) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self._env_headers}
        if signed:
            if self._creds is None:
                raise ValueError("this OKX endpoint needs credentials; build the client with OkxCredentials")
            ts = iso_timestamp(self._wall_ms() + self._offset_ms)
            headers.update(
                {
                    "OK-ACCESS-KEY": self._creds.api_key,
                    "OK-ACCESS-SIGN": sign(self._creds.secret, ts, method, request_path, body),
                    "OK-ACCESS-TIMESTAMP": ts,
                    "OK-ACCESS-PASSPHRASE": self._creds.passphrase,
                }
            )
        return headers

    async def _get(
        self, endpoint: str, params: Mapping[str, str | None] | None, *, signed: bool
    ) -> list[Any]:
        """GET ``/api/v5/<endpoint>`` and return the ``data`` list, or raise a typed error."""
        if signed:
            await self._ensure_clock()
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        request_path = API_PREFIX + endpoint + (("?" + urlencode(clean)) if clean else "")
        attempts = self._retry.max_attempts
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            await self._limiter.acquire(endpoint)
            try:
                return await self._once(request_path, signed)
            except _Retryable as r:
                last = r.error
                if attempt < attempts:
                    delay = self._retry.delay(attempt, self._rand)
                    log.warning(
                        "okx retry",
                        extra={
                            "path": endpoint,
                            "attempt": attempt,
                            "delay_s": round(delay, 3),
                            "error": str(last),
                        },
                    )
                    await self._sleep(delay)
        assert last is not None
        raise last

    async def _once(self, request_path: str, signed: bool) -> list[Any]:
        endpoint = request_path[len(API_PREFIX) :].split("?", 1)[0]
        headers = self._headers("GET", request_path, "", signed)
        try:
            resp = await self._http.get(request_path, headers=headers)
        except httpx.TransportError as exc:
            raise _Retryable(OkxNetworkError(type(exc).__name__, path=endpoint)) from exc

        body: Any = None
        try:
            body = resp.json()
        except (json.JSONDecodeError, ValueError):
            body = None

        code = str(body.get("code")) if isinstance(body, dict) and "code" in body else None
        msg = str(body.get("msg", "")) if isinstance(body, dict) else ""

        if resp.status_code == 429 or resp.status_code >= 500:
            err: Exception = (
                OkxError(code, msg, path=endpoint, http_status=resp.status_code)
                if code not in (None, "0")
                else OkxHttpError(resp.status_code, path=endpoint)
            )
            raise _Retryable(err)
        if code is not None and code != "0":
            err = OkxError(code, msg, path=endpoint, http_status=resp.status_code)
            if code in self._retry.retryable_codes:
                raise _Retryable(err)
            raise err
        if resp.status_code >= 400:
            raise OkxHttpError(resp.status_code, path=endpoint)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise OkxResponseError(f"unexpected OKX body shape on {endpoint}")
        data: list[Any] = body["data"]
        return data

    @staticmethod
    def _parse(model: type[M], rows: Sequence[Any]) -> list[M]:
        return [model.model_validate(row) for row in rows]

    # ---- public ------------------------------------------------------------------------------

    async def get_server_time(self) -> ServerTime:
        rows = await self._get("public/time", None, signed=False)
        if not rows:
            raise OkxResponseError("public/time returned no data")
        return ServerTime.model_validate(rows[0])

    async def get_instruments(
        self, inst_type: str, *, inst_family: str | None = None, inst_id: str | None = None
    ) -> list[Instrument]:
        params = {"instType": inst_type, "instFamily": inst_family, "instId": inst_id}
        return self._parse(Instrument, await self._get("public/instruments", params, signed=False))

    async def get_tickers(self, inst_type: str, *, inst_family: str | None = None) -> list[Ticker]:
        params = {"instType": inst_type, "instFamily": inst_family}
        return self._parse(Ticker, await self._get("market/tickers", params, signed=False))

    # ---- account (read) ----------------------------------------------------------------------

    async def get_account_config(self) -> AccountConfig:
        rows = await self._get("account/config", None, signed=True)
        if not rows:
            raise OkxResponseError("account/config returned no data")
        return AccountConfig.model_validate(rows[0])

    async def get_balance(self, ccy: Sequence[str] | None = None) -> AccountBalance:
        params = {"ccy": ",".join(ccy) if ccy else None}
        rows = await self._get("account/balance", params, signed=True)
        if not rows:
            raise OkxResponseError("account/balance returned no data")
        return AccountBalance.model_validate(rows[0])

    async def get_positions(
        self, *, inst_type: str | None = None, inst_id: str | None = None, pos_id: str | None = None
    ) -> list[Position]:
        params = {"instType": inst_type, "instId": inst_id, "posId": pos_id}
        return self._parse(Position, await self._get("account/positions", params, signed=True))

    async def get_positions_history(
        self,
        *,
        inst_type: str | None = None,
        inst_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[PositionHistory]:
        params = _page({"instType": inst_type, "instId": inst_id}, after, before, limit)
        return self._parse(PositionHistory, await self._get("account/positions-history", params, signed=True))

    async def get_bills(
        self,
        *,
        inst_type: str | None = None,
        ccy: str | None = None,
        type: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[Bill]:
        """``account/bills``: the last 7 days. ``after``/``before`` are bill ids."""
        params = _page({"instType": inst_type, "ccy": ccy, "type": type}, after, before, limit)
        return self._parse(Bill, await self._get("account/bills", params, signed=True))

    async def get_bills_archive(
        self,
        *,
        inst_type: str | None = None,
        ccy: str | None = None,
        type: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[Bill]:
        """``account/bills-archive``: the last 3 months."""
        params = _page({"instType": inst_type, "ccy": ccy, "type": type}, after, before, limit)
        return self._parse(Bill, await self._get("account/bills-archive", params, signed=True))

    async def get_trade_fee(
        self, inst_type: str, *, inst_id: str | None = None, inst_family: str | None = None
    ) -> TradeFee:
        params = {"instType": inst_type, "instId": inst_id, "instFamily": inst_family}
        rows = await self._get("account/trade-fee", params, signed=True)
        if not rows:
            raise OkxResponseError("account/trade-fee returned no data")
        return TradeFee.model_validate(rows[0])

    # ---- asset (read) ------------------------------------------------------------------------

    async def get_asset_balances(self, ccy: Sequence[str] | None = None) -> list[AssetBalance]:
        params = {"ccy": ",".join(ccy) if ccy else None}
        return self._parse(AssetBalance, await self._get("asset/balances", params, signed=True))

    async def get_deposit_history(
        self,
        *,
        ccy: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[DepositRecord]:
        """``after``/``before`` are epoch-millisecond timestamps as strings."""
        params = _page({"ccy": ccy}, after, before, limit)
        return self._parse(DepositRecord, await self._get("asset/deposit-history", params, signed=True))

    async def get_withdrawal_history(
        self,
        *,
        ccy: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[WithdrawalRecord]:
        """Read-only history for the Cash flows page. Atlas has no way to create a withdrawal."""
        params = _page({"ccy": ccy}, after, before, limit)
        return self._parse(WithdrawalRecord, await self._get("asset/withdrawal-history", params, signed=True))


def _page(
    params: dict[str, str | None], after: str | None, before: str | None, limit: int | None
) -> dict[str, str | None]:
    return {**params, "after": after, "before": before, "limit": None if limit is None else str(limit)}


__all__ = ["OkxClient", "OkxCredentials", "RetryPolicy"]
