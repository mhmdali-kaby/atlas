"""Async, typed, read-only OKX v5 REST client (brief §4.1, §4.2; Phase 0).

Signs private requests, sends ``x-simulated-trading: 1`` in demo only, keeps a clock offset from
``public/time`` (refreshed every ``okx_rest.clock_sync_interval_seconds``), retries network errors, 5xx,
429 and the retryable OKX codes with jittered exponential backoff, and rate-limits each endpoint with a
token bucket. A 429 or an OKX "too many requests" code waits at least one rate-limit window of that endpoint
before the retry. Public market-data methods (ATL-45) are unsigned. Read-only: there are no order, leverage,
withdrawal or transfer methods here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar
from urllib.parse import urlencode

import httpx
from atlas_shared.defaults import DEFAULTS

from trading_core.okx.errors import OkxError, OkxHttpError, OkxNetworkError, OkxResponseError
from trading_core.okx.fixtures import fixture_transport, fixtures_enabled
from trading_core.okx.models import (
    AccountBalance,
    AccountConfig,
    AssetBalance,
    Bill,
    Candle,
    DepositRecord,
    FundingRate,
    FundingRateHistory,
    Instrument,
    LongShortRatio,
    MarkPrice,
    OkxModel,
    OpenInterest,
    OpenInterestVolume,
    Position,
    PositionHistory,
    ServerTime,
    TakerVolume,
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
Cursor = datetime | int | str | None
"""A paging cursor or time bound: an aware ``datetime``, epoch milliseconds, or OKX's own ms string."""


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
    rate_limit_codes: frozenset[str] = frozenset()

    @classmethod
    def from_defaults(cls) -> RetryPolicy:
        cfg: Mapping[str, Any] = DEFAULTS["okx_rest"]
        return cls(
            max_attempts=int(cfg["max_attempts"]),
            backoff_base_seconds=float(cfg["backoff_base_seconds"]),
            backoff_max_seconds=float(cfg["backoff_max_seconds"]),
            retryable_codes=frozenset(str(c) for c in cfg["retryable_codes"]),
            rate_limit_codes=frozenset(str(c) for c in cfg["rate_limit_codes"]),
        )

    def delay(self, attempt: int, rand: Callable[[], float]) -> float:
        """Full-jitter backoff before retry number ``attempt`` (1-based)."""
        cap = min(self.backoff_max_seconds, self.backoff_base_seconds * (2.0 ** (attempt - 1)))
        return cap * rand()


class _Retryable(Exception):
    def __init__(self, error: Exception, *, rate_limited: bool = False) -> None:
        self.error = error
        self.rate_limited = rate_limited


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
            base_url=str(envs[env]["rest_base_url"]),
            timeout=float(cfg["timeout_seconds"]),
            # ATLAS_OKX_FIXTURES=1 answers from recorded JSON; nothing reaches OKX (ATL-25).
            transport=fixture_transport() if fixtures_enabled() else None,
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
                    if r.rate_limited:
                        # OKX refills per window; retrying sooner would only be refused again.
                        delay = max(delay, self._limiter.bucket(endpoint).limit.per_seconds)
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

        rate_limited = resp.status_code == 429 or code in self._retry.rate_limit_codes
        if resp.status_code == 429 or resp.status_code >= 500:
            err: Exception = (
                OkxError(code, msg, path=endpoint, http_status=resp.status_code)
                if code not in (None, "0")
                else OkxHttpError(resp.status_code, path=endpoint)
            )
            raise _Retryable(err, rate_limited=rate_limited)
        if code is not None and code != "0":
            err = OkxError(code, msg, path=endpoint, http_status=resp.status_code)
            if code in self._retry.retryable_codes or rate_limited:
                raise _Retryable(err, rate_limited=rate_limited)
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

    # ---- public market data (ATL-45) ---------------------------------------------------------

    async def get_candles(
        self,
        inst_id: str,
        *,
        bar: str = "1m",
        after: Cursor = None,
        before: Cursor = None,
        limit: int | None = None,
    ) -> list[Candle]:
        """``market/candles``: recent bars, newest first. ``after`` returns bars older than that ts,
        ``before`` bars newer than it. ``limit`` up to ``okx_rest.page_limits['market/candles']``."""
        return await self._candles("market/candles", inst_id, bar, after, before, limit)

    async def get_history_candles(
        self,
        inst_id: str,
        *,
        bar: str = "1m",
        after: Cursor = None,
        before: Cursor = None,
        limit: int | None = None,
    ) -> list[Candle]:
        """``market/history-candles``: older bars (years back), newest first, same cursors."""
        return await self._candles("market/history-candles", inst_id, bar, after, before, limit)

    async def iter_candles(
        self,
        inst_id: str,
        *,
        bar: str = "1m",
        after: Cursor = None,
        since: Cursor = None,
        limit: int | None = None,
        history: bool = False,
    ) -> AsyncIterator[list[Candle]]:
        """Walk back in time page by page with the ``after`` cursor (each page newest first).

        Starts at ``after`` (or the newest bar) and stops on an empty page, a page that reaches
        ``since`` (bars older than ``since`` are dropped from the last page), or a cursor that does
        not move. ``history=True`` reads ``market/history-candles``.
        """
        endpoint = "market/history-candles" if history else "market/candles"
        floor = _cursor_ms(since)
        cursor = _cursor_ms(after)
        while True:
            page = await self._candles(endpoint, inst_id, bar, cursor, None, limit)
            if floor is not None:
                page = [c for c in page if _epoch_ms(c.ts) >= int(floor)]
            if not page:
                return
            yield page
            oldest = str(min(_epoch_ms(c.ts) for c in page))
            if oldest == cursor or (floor is not None and int(oldest) <= int(floor)):
                return
            cursor = oldest

    async def _candles(
        self, endpoint: str, inst_id: str, bar: str, after: Cursor, before: Cursor, limit: int | None
    ) -> list[Candle]:
        params = {
            "instId": inst_id,
            "bar": bar,
            "after": _cursor_ms(after),
            "before": _cursor_ms(before),
            "limit": _limit(endpoint, limit),
        }
        return self._parse(Candle, await self._get(endpoint, params, signed=False))

    async def get_funding_rate(self, inst_id: str) -> FundingRate:
        """``public/funding-rate`` for one perpetual swap."""
        rows = await self._get("public/funding-rate", {"instId": inst_id}, signed=False)
        if not rows:
            raise OkxResponseError("public/funding-rate returned no data")
        return FundingRate.model_validate(rows[0])

    async def get_funding_rate_history(
        self, inst_id: str, *, after: Cursor = None, before: Cursor = None, limit: int | None = None
    ) -> list[FundingRateHistory]:
        """``public/funding-rate-history``, newest first; cursors are ``funding_time`` values."""
        params = {
            "instId": inst_id,
            "after": _cursor_ms(after),
            "before": _cursor_ms(before),
            "limit": _limit("public/funding-rate-history", limit),
        }
        rows = await self._get("public/funding-rate-history", params, signed=False)
        return self._parse(FundingRateHistory, rows)

    async def get_open_interest(
        self, inst_type: str, *, inst_family: str | None = None, inst_id: str | None = None
    ) -> list[OpenInterest]:
        """``public/open-interest`` (``inst_type`` SWAP, FUTURES or OPTION)."""
        params = {"instType": inst_type, "instFamily": inst_family, "instId": inst_id}
        return self._parse(OpenInterest, await self._get("public/open-interest", params, signed=False))

    async def get_open_interest_volume(
        self, ccy: str, *, period: str = "5m", begin: Cursor = None, end: Cursor = None
    ) -> list[OpenInterestVolume]:
        """``rubik/stat/contracts/open-interest-volume`` for a currency (``BTC``), newest first."""
        return self._parse(
            OpenInterestVolume,
            await self._rubik("rubik/stat/contracts/open-interest-volume", ccy, None, period, begin, end),
        )

    async def get_long_short_ratio(
        self, ccy: str, *, period: str = "5m", begin: Cursor = None, end: Cursor = None
    ) -> list[LongShortRatio]:
        """``rubik/stat/contracts/long-short-account-ratio`` for a currency, newest first."""
        return self._parse(
            LongShortRatio,
            await self._rubik("rubik/stat/contracts/long-short-account-ratio", ccy, None, period, begin, end),
        )

    async def get_taker_volume(
        self,
        ccy: str,
        *,
        inst_type: str = "CONTRACTS",
        period: str = "5m",
        begin: Cursor = None,
        end: Cursor = None,
    ) -> list[TakerVolume]:
        """``rubik/stat/taker-volume`` (``inst_type`` SPOT or CONTRACTS), newest first."""
        return self._parse(
            TakerVolume, await self._rubik("rubik/stat/taker-volume", ccy, inst_type, period, begin, end)
        )

    async def _rubik(
        self, endpoint: str, ccy: str, inst_type: str | None, period: str, begin: Cursor, end: Cursor
    ) -> list[Any]:
        params = {
            "ccy": ccy,
            "instType": inst_type,
            "period": period,
            "begin": _cursor_ms(begin),
            "end": _cursor_ms(end),
        }
        return await self._get(endpoint, params, signed=False)

    async def get_mark_price(
        self, inst_type: str, *, inst_family: str | None = None, inst_id: str | None = None
    ) -> list[MarkPrice]:
        """``public/mark-price`` (``inst_type`` MARGIN, SWAP, FUTURES or OPTION)."""
        params = {"instType": inst_type, "instFamily": inst_family, "instId": inst_id}
        return self._parse(MarkPrice, await self._get("public/mark-price", params, signed=False))

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


def _epoch_ms(ts: datetime) -> int:
    return int(ts.timestamp()) * 1000 + ts.microsecond // 1000


def _cursor_ms(value: Cursor) -> str | None:
    """A cursor as OKX's epoch-millisecond string. A naive datetime is refused (UTC only)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("cursor datetimes must be timezone-aware (UTC)")
        return str(_epoch_ms(value))
    if isinstance(value, bool):
        raise ValueError("a bool is not a cursor")
    if isinstance(value, int):
        return str(value)
    if not value.isdigit():
        raise ValueError(f"cursor must be epoch milliseconds, got {value!r}")
    return value


def _limit(endpoint: str, limit: int | None) -> str | None:
    if limit is None:
        return None
    cap = int(DEFAULTS["okx_rest"]["page_limits"][endpoint])
    if not 1 <= limit <= cap:
        raise ValueError(f"{endpoint} limit must be 1..{cap}, got {limit}")
    return str(limit)


__all__ = ["OkxClient", "OkxCredentials", "RetryPolicy"]
