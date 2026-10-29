"""Generic RSS/Atom collector (feedparser), for every ``kind: rss`` source.

Serves CoinDesk, Cointelegraph, The Block, Decrypt, CNBC, MarketWatch and Yahoo Finance. A URL with
``{symbol}`` is a per-symbol template (Yahoo): it is expanded once per stock/ETF perp underlying in
``instruments``. A per-symbol source fails only when every symbol's feed fails; single feeds fail on
any HTTP error or an unparseable document.
"""

from __future__ import annotations

import calendar
import html
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import feedparser
import httpx

from atlas_ingest.collectors import CollectContext, CollectError, SourceConfig, register
from atlas_ingest.items import RawItem

logger = logging.getLogger("atlas_ingest.collectors.rss")

SYMBOL_PLACEHOLDER = "{symbol}"
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def strip_html(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value))).strip()
    return text or None


def _when(entry: Any) -> datetime | None:
    for name in ("published_parsed", "updated_parsed"):
        parsed = entry.get(name)
        if isinstance(parsed, time.struct_time):
            # feedparser normalises to UTC; timegm reads the struct as UTC.
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    return None


def _content(entry: Any) -> str | None:
    blocks = entry.get("content")
    if not isinstance(blocks, list):
        return None
    texts = [strip_html(b.get("value")) for b in blocks if isinstance(b, dict)]
    joined = "\n\n".join(t for t in texts if t)
    return joined or None


def parse_feed(document: bytes, *, symbol: str | None = None) -> list[RawItem]:
    parsed = feedparser.parse(document)
    entries = list(parsed.get("entries") or [])
    if parsed.get("bozo") and not entries:
        raise CollectError("unparseable feed")
    language = parsed.get("feed", {}).get("language")
    items: list[RawItem] = []
    for entry in entries:
        link = entry.get("link")
        title = strip_html(entry.get("title"))
        if not isinstance(link, str) or not link.startswith(("http://", "https://")) or not title:
            continue
        summary = strip_html(entry.get("summary"))
        body = _content(entry)
        if body is not None and summary is not None and body == summary:
            body = None  # content that only repeats the summary is not the article
        raw: dict[str, Any] = {
            "id": entry.get("id"),
            "title": entry.get("title"),
            "link": link,
            "summary": entry.get("summary"),
            "published": entry.get("published") or entry.get("updated"),
            "author": entry.get("author"),
            "tags": [t.get("term") for t in entry.get("tags") or [] if isinstance(t, dict)],
        }
        if symbol is not None:
            raw["symbol"] = symbol
        items.append(
            RawItem(
                url=link,
                title=title,
                summary=summary,
                body=body,
                published_at=_when(entry),
                language=language if isinstance(language, str) else None,
                raw=raw,
            )
        )
    return items


@register(kinds=("rss",))
class RssCollector:
    async def _fetch(self, http: httpx.AsyncClient, url: str, symbol: str | None) -> list[RawItem]:
        response = await http.get(url, headers={"Accept": ACCEPT})
        response.raise_for_status()
        return parse_feed(response.content, symbol=symbol)

    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        if not source.url:
            raise CollectError("source has no url")
        if SYMBOL_PLACEHOLDER not in source.url:
            return await self._fetch(ctx.http, source.url, None)

        symbols = await ctx.stock_symbols()
        items: list[RawItem] = []
        failures: list[Exception] = []
        for symbol in symbols:
            url = source.url.replace(SYMBOL_PLACEHOLDER, quote(symbol, safe=""))
            try:
                items.extend(await self._fetch(ctx.http, url, symbol))
            except (httpx.HTTPError, CollectError) as exc:
                failures.append(exc)
                logger.warning(
                    "symbol feed failed",
                    extra={"source": source.key, "symbol": symbol, "error": type(exc).__name__},
                )
        if symbols and len(failures) == len(symbols):
            raise failures[0]
        return items
