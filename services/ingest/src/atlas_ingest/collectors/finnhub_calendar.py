"""Finnhub economic calendar (ATL-61), key required.

``GET /api/v1/calendar/economic?from=YYYY-MM-DD&to=YYYY-MM-DD`` with the key in the
``X-Finnhub-Token`` header (never in the URL). Response::

    {"economicCalendar": [{"actual": 0.4, "country": "US", "estimate": 0.3, "event": "CPI MoM",
                           "impact": "high", "prev": 0.2, "time": "2026-10-28 12:30:00", "unit": "%"}]}

``time`` is UTC. Numbers are kept as published with their unit ("0.4%") and surprise is
actual - estimate. Free tier: 60 calls/min per key, shared with finnhub.news/earnings; this source
makes one call per poll. When this source is enabled, keyed and not degraded, forexfactory.calendar
stands down (see forexfactory.py).

A 429 raises ATL-60's ``RateLimited`` (the same ``news.collectors.finnhub.rate_limit_window_seconds``
default as finnhub.news), so it is one failure and the next poll waits at least ``Retry-After``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
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


def _published(value: Any, unit: str) -> str | None:
    if value is None or value == "":
        return None
    text = format(value, "f") if isinstance(value, Decimal) else str(value)
    return f"{text}{unit}"[:64]


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | Decimal):
        return Decimal(value)
    return cal.parse_number(str(value))


def parse_calendar(body: Any) -> list[cal.EconomicRow]:
    if not isinstance(body, dict) or not isinstance(body.get("economicCalendar"), list):
        raise CollectError("unexpected response shape")
    rows: list[cal.EconomicRow] = []
    for entry in body["economicCalendar"]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("event") or "").strip()
        country = str(entry.get("country") or "").strip()
        stamp = entry.get("time")
        if not name or not country or not isinstance(stamp, str):
            continue
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        when = when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
        unit = str(entry.get("unit") or "")
        actual, estimate = _number(entry.get("actual")), _number(entry.get("estimate"))
        rows.append(
            cal.EconomicRow(
                country=country,
                name=name,
                importance=cal.importance_for(entry.get("impact")),
                scheduled_at=when,
                actual=_published(entry.get("actual"), unit),
                forecast=_published(entry.get("estimate"), unit),
                previous=_published(entry.get("prev"), unit),
                surprise=actual - estimate if actual is not None and estimate is not None else None,
            )
        )
    return rows


@register(keys=("finnhub.calendar",))
class FinnhubCalendarCollector:
    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        if ctx.engine is None:
            raise CollectError("no database engine")
        if not source.api_key:
            raise CollectError("api key missing")
        cfg: dict[str, Any] = DEFAULTS["news"]["calendar"]
        day = ctx.now.astimezone(UTC).date()
        params = {
            "from": (day - timedelta(days=int(cfg["finnhub_days_back"]))).isoformat(),
            "to": (day + timedelta(days=int(cfg["finnhub_days_ahead"]))).isoformat(),
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
        rows = parse_calendar(body)
        ctx.stats.written += await asyncio.to_thread(
            cal.upsert_economic, ctx.engine, source.key, rows, ctx.now
        )
        return []

    async def next_poll_seconds(self, source: SourceConfig, ctx: CollectContext) -> float | None:
        if ctx.engine is None:
            return None
        return await asyncio.to_thread(
            cal.calendar_poll_seconds, ctx.engine, ctx.now, float(source.poll_seconds)
        )
