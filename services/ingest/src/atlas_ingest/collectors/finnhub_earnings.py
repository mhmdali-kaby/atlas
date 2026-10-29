"""Finnhub earnings calendar (ATL-61), key required.

``GET /api/v1/calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD`` with the key in the
``X-Finnhub-Token`` header. Response::

    {"earningsCalendar": [{"date": "2026-10-30", "epsActual": null, "epsEstimate": 1.6,
                           "hour": "amc", "quarter": 4, "revenueActual": null,
                           "revenueEstimate": 94500000000, "symbol": "AAPL", "year": 2026}]}

One call per poll covers every company; only symbols that are stock/ETF perp underlyings in
``instruments`` (``ctx.stock_symbols()``) are kept. Rows upsert on (symbol, date); a duplicated
symbol+date in one response is collapsed first (last one wins).

A 429 raises ATL-60's ``RateLimited`` (the same ``news.collectors.finnhub.rate_limit_window_seconds``
default as finnhub.news), so it is one failure and the next poll waits at least ``Retry-After``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Any

from atlas_shared.defaults import DEFAULTS

from atlas_ingest import calendar as cal
from atlas_ingest.collectors import (
    CollectContext,
    CollectError,
    RateLimited,
    SourceConfig,
    register,
    retry_after_seconds,
)
from atlas_ingest.items import RawItem


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | Decimal):
        return Decimal(value)
    return cal.parse_number(str(value))


def parse_earnings(body: Any, symbols: set[str]) -> list[cal.EarningsRow]:
    if not isinstance(body, dict) or not isinstance(body.get("earningsCalendar"), list):
        raise CollectError("unexpected response shape")
    rows: list[cal.EarningsRow] = []
    for entry in body["earningsCalendar"]:
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("symbol") or "").strip().upper()
        if symbol not in symbols:
            continue
        try:
            day = date.fromisoformat(str(entry.get("date")))
        except ValueError:
            continue
        hour = str(entry.get("hour") or "").strip().lower()
        rows.append(
            cal.EarningsRow(
                symbol=symbol,
                date=day,
                time=hour[:16] or None,
                eps_est=_number(entry.get("epsEstimate")),
                eps_act=_number(entry.get("epsActual")),
                rev_est=_number(entry.get("revenueEstimate")),
                rev_act=_number(entry.get("revenueActual")),
            )
        )
    return rows


@register(keys=("finnhub.earnings",))
class FinnhubEarningsCollector:
    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        if ctx.engine is None:
            raise CollectError("no database engine")
        if not source.api_key:
            raise CollectError("api key missing")
        symbols = {s.upper() for s in await ctx.stock_symbols()}
        if not symbols:
            return []  # no stock/ETF perps synced yet: nothing to ask for
        cfg: dict[str, Any] = DEFAULTS["news"]["earnings"]
        day = ctx.now.astimezone(UTC).date()
        params = {
            "from": (day - timedelta(days=int(cfg["days_back"]))).isoformat(),
            "to": (day + timedelta(days=int(cfg["days_ahead"]))).isoformat(),
        }
        r = await ctx.http.get(source.url or "", params=params, headers={"X-Finnhub-Token": source.api_key})
        if r.status_code == 429:
            window = float(DEFAULTS["news"]["collectors"]["finnhub"]["rate_limit_window_seconds"])
            raise RateLimited(retry_after_seconds(r, window))
        r.raise_for_status()
        try:
            body = cal.json_decimal(r.content)
        except ValueError as exc:
            raise CollectError("response is not JSON") from exc
        rows = parse_earnings(body, symbols)
        ctx.stats.written += await asyncio.to_thread(cal.upsert_earnings, ctx.engine, rows, ctx.now)
        return []
