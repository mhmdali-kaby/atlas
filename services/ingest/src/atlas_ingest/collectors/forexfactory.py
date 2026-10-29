"""ForexFactory weekly economic calendar (ATL-61), the default calendar source (no key).

Feed: ``https://nfs.faireconomy.media/ff_calendar_thisweek.json`` — a JSON array of
``{"title", "country", "date", "impact", "forecast", "previous"[, "actual"]}`` where ``date`` is
ISO 8601 with an offset (``2026-10-28T08:30:00-04:00``) and ``impact`` is High/Medium/Low/Holiday.
The faireconomy mirror asks clients not to hammer it; the 15-minute cadence (60 s only while an
event is within ±10 min) keeps us far below that.

Rows go to ``economic_events`` (not news_items), upserted on (source, external_id) where the id is
sha1(country | title | UTC time). Impact maps through ``news.calendar_importance``; 0 (Holiday) is
not stored. When finnhub.calendar is enabled, keyed and not degraded this collector makes no
request at all: Finnhub is then the calendar source.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from atlas_ingest import calendar as cal
from atlas_ingest.collectors import CollectContext, CollectError, SourceConfig, register
from atlas_ingest.items import RawItem

logger = logging.getLogger("atlas_ingest.collectors.forexfactory")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:64] or None


def parse_week(body: Any) -> list[cal.EconomicRow]:
    if not isinstance(body, list):
        raise CollectError("unexpected response shape")
    rows: list[cal.EconomicRow] = []
    for entry in body:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("title") or "").strip()
        country = str(entry.get("country") or "").strip()
        stamp = entry.get("date")
        if not name or not country or not isinstance(stamp, str):
            continue
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            continue  # no offset: we cannot know the UTC time, so do not guess
        actual, forecast = _text(entry.get("actual")), _text(entry.get("forecast"))
        rows.append(
            cal.EconomicRow(
                country=country,
                name=name,
                importance=cal.importance_for(entry.get("impact")),
                scheduled_at=when.astimezone(UTC),
                actual=actual,
                forecast=forecast,
                previous=_text(entry.get("previous")),
                surprise=cal.surprise(actual, forecast),
            )
        )
    return rows


@register(keys=("forexfactory.calendar",))
class ForexFactoryCollector:
    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        if ctx.engine is None:
            raise CollectError("no database engine")
        if await asyncio.to_thread(cal.finnhub_calendar_active, ctx.engine, ctx.now):
            logger.debug("finnhub.calendar is active; skipping", extra={"source": source.key})
            return []
        r = await ctx.http.get(source.url or "", headers={"Accept": "application/json"})
        r.raise_for_status()
        try:
            body = cal.json_decimal(r.content)
        except ValueError as exc:
            raise CollectError("response is not JSON") from exc
        rows = parse_week(body)
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
