"""Alternative.me Crypto Fear & Greed Index (ATL-61), public, no key.

``GET https://api.alternative.me/fng/?limit=N`` →
``{"name": "Fear and Greed Index", "data": [{"value": "40", "value_classification": "Fear",
"timestamp": "1761696000", "time_until_update": "..."}], "metadata": {"error": null}}``.
The index is published once a day; each value becomes one ``market_sentiment`` row with index_key
``news.sentiment.fng_index_key`` (crypto_fng) and ts = the published timestamp, so re-polling the
same values writes nothing. Free use with attribution; the provider asks for no more than a few
requests per minute, and this source polls hourly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from atlas_shared.defaults import DEFAULTS

from atlas_ingest import calendar as cal
from atlas_ingest.collectors import CollectContext, CollectError, SourceConfig, read_capped, register
from atlas_ingest.items import RawItem


def parse_fng(body: Any, index_key: str) -> list[cal.SentimentRow]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise CollectError("unexpected response shape")
    error = (body.get("metadata") or {}).get("error") if isinstance(body.get("metadata"), dict) else None
    if error:
        raise CollectError("provider error")
    rows: list[cal.SentimentRow] = []
    for entry in body["data"]:
        if not isinstance(entry, dict):
            continue
        try:
            value = Decimal(str(entry.get("value")))
            ts = datetime.fromtimestamp(int(str(entry.get("timestamp"))), tz=UTC)
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            continue
        if not value.is_finite():
            continue
        label = str(entry.get("value_classification") or "").strip()
        rows.append(
            cal.SentimentRow(index_key=index_key, value=value, classification=label[:64] or None, ts=ts)
        )
    return rows


@register(keys=("alternative_me.fng",))
class FearGreedCollector:
    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        if ctx.engine is None:
            raise CollectError("no database engine")
        cfg: dict[str, Any] = DEFAULTS["news"]["sentiment"]
        async with ctx.http.stream(
            "GET", source.url or "", params={"limit": str(int(cfg["fng_limit"]))}
        ) as r:
            r.raise_for_status()
            raw = await read_capped(r)
        try:
            body = cal.json_decimal(raw)
        except ValueError as exc:
            raise CollectError("response is not JSON") from exc
        rows = parse_fng(body, str(cfg["fng_index_key"]))
        ctx.stats.written += await asyncio.to_thread(cal.upsert_sentiment, ctx.engine, rows, ctx.now)
        return []
