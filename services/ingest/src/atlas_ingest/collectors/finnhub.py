"""Finnhub news collector (ATL-60, source key ``finnhub.news``).

Each poll makes at most ``news.collectors.finnhub.max_calls_per_poll`` requests:

1. ``GET <source url>?category=general`` (the seeded URL is ``https://finnhub.io/api/v1/news``);
2. ``GET .../company-news?symbol=<SYM>&from=<date>&to=<date>`` for the next symbols of the active
   env's stock/ETF underlyings (``ctx.stock_symbols()``, read from ``instruments``), rotating
   through the list across polls so every symbol is covered within a few polls.

The key goes in the ``X-Finnhub-Token`` header (Finnhub accepts it there or as ``token=``), so no
URL ever carries it.

Terms (Finnhub's pricing/docs page; not re-read at build time because the build had no network
access, so re-check on a networked run): the free tier allows 60 API calls per minute per key and
30 calls per second, and answers 429 above that. With 20 calls per poll, a 0.25 s gap between
calls and the seeded 180 s poll, finnhub.news averages under 7 calls/minute and never bursts above
4/second, leaving room for finnhub.calendar and finnhub.earnings on the same key. A 429 aborts the
poll as one failure, and the runner waits at least ``Retry-After`` (else
``rate_limit_window_seconds``, Finnhub's one-minute window) before polling again.

Response (documented shape, both endpoints)::

    [{"category": "company", "datetime": 1761721200, "headline": "...", "id": 137000001,
      "image": "...", "related": "AAPL", "source": "Yahoo", "summary": "...", "url": "https://..."}]
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from atlas_shared.defaults import DEFAULTS

from atlas_ingest.collectors import (
    CollectContext,
    CollectError,
    RateLimited,
    SourceConfig,
    read_capped,
    register,
    retry_after_seconds,
)
from atlas_ingest.items import RawItem

DEFAULT_URL = "https://finnhub.io/api/v1/news"


def company_news_url(news_url: str) -> str:
    """``https://finnhub.io/api/v1/news`` → ``https://finnhub.io/api/v1/company-news``."""
    return urljoin(news_url, "company-news")


def parse_news(body: Any, *, symbol: str | None = None) -> list[RawItem]:
    if not isinstance(body, list):
        raise CollectError("unexpected response shape")
    items: list[RawItem] = []
    for row in body:
        if not isinstance(row, dict):
            continue
        title = str(row.get("headline") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        published: datetime | None = None
        stamp = row.get("datetime")
        if isinstance(stamp, int | float) and stamp > 0:
            published = datetime.fromtimestamp(stamp, UTC)
        summary = str(row.get("summary") or "").strip() or None
        raw: dict[str, Any] = {
            "provider": "finnhub",
            "id": row.get("id"),
            "category": row.get("category"),
            "publisher": row.get("source"),
            "related": row.get("related"),
        }
        if symbol:
            raw["symbol"] = symbol
        items.append(RawItem(url=url, title=title, summary=summary, published_at=published, raw=raw))
    return items


@register(keys=("finnhub.news",))
class FinnhubNewsCollector:
    def __init__(self) -> None:
        conf: dict[str, Any] = DEFAULTS["news"]["collectors"]["finnhub"]
        self.max_calls_per_poll = max(int(conf["max_calls_per_poll"]), 1)
        self.request_interval = float(Decimal(str(conf["request_interval_seconds"])))
        self.company_news_days = int(conf["company_news_days"])
        self.rate_limit_window = float(conf["rate_limit_window_seconds"])
        self._cursor = 0
        """Index into the sorted symbol list where the next poll's company-news batch starts."""
        self.requested_symbols: list[str] = []
        """Symbols requested by the last poll (diagnostics and tests)."""

    def next_batch(self, symbols: list[str]) -> list[str]:
        size = min(self.max_calls_per_poll - 1, len(symbols))
        if size <= 0:
            return []
        start = self._cursor % len(symbols)
        batch = [symbols[(start + i) % len(symbols)] for i in range(size)]
        return batch

    async def _get(self, ctx: CollectContext, url: str, params: dict[str, str], key: str) -> Any:
        async with ctx.http.stream(
            "GET", url, params=params, headers={"X-Finnhub-Token": key, "Accept": "application/json"}
        ) as response:
            if response.status_code == 429:
                raise RateLimited(retry_after_seconds(response, self.rate_limit_window))
            response.raise_for_status()
            raw = await read_capped(response)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise CollectError("response is not JSON") from exc

    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        if not source.api_key:
            raise CollectError("api key missing")
        news_url = source.url or DEFAULT_URL
        items = parse_news(await self._get(ctx, news_url, {"category": "general"}, source.api_key))

        symbols = sorted(set(await ctx.stock_symbols()))
        batch = self.next_batch(symbols)
        self.requested_symbols = []
        today = ctx.now.astimezone(UTC).date()
        window = {
            "from": (today - timedelta(days=self.company_news_days)).isoformat(),
            "to": today.isoformat(),
        }
        url = company_news_url(news_url)
        for symbol in batch:
            if self.request_interval > 0:
                await asyncio.sleep(self.request_interval)
            body = await self._get(ctx, url, {"symbol": symbol, **window}, source.api_key)
            self.requested_symbols.append(symbol)
            self._cursor += 1  # a 429 mid-batch resumes at the symbol that was refused
            items.extend(parse_news(body, symbol=symbol))
        return items
