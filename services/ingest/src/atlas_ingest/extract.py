"""Article text extraction (trafilatura) and language detection (langdetect).

Extraction only runs for new items whose source gave a summary but no body. Each fetch has a
timeout; a host that times out or refuses is not asked again for the rest of that poll, so one slow
site cannot stall a feed. Every failure just leaves ``body`` empty: extraction never fails a poll.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import trafilatura
from langdetect import DetectorFactory, LangDetectException, detect

logger = logging.getLogger("atlas_ingest.extract")

DetectorFactory.seed = 0  # langdetect is randomised; a fixed seed makes it deterministic


@dataclass
class Extractor:
    http: httpx.AsyncClient
    timeout_seconds: float
    max_bytes: int
    host_timeouts: dict[str, float] = field(default_factory=dict)
    """Overrides of ``timeout_seconds`` for slow hosts, keyed by lowercase host."""
    user_agent: str | None = None
    """Article pages answer a bot user agent with 429/403 on several news sites (CoinDesk);
    feeds are fetched with the client's own agent."""

    def timeout_for(self, host: str) -> float:
        return self.host_timeouts.get(host, self.timeout_seconds)

    async def article_text(self, url: str, skip_hosts: set[str]) -> str | None:
        host = (urlsplit(url).hostname or "").lower()
        if not host or host in skip_hosts:
            return None
        try:
            response = await self.http.get(
                url,
                timeout=httpx.Timeout(self.timeout_for(host)),
                follow_redirects=True,
                headers={"User-Agent": self.user_agent} if self.user_agent else None,
            )
        except httpx.HTTPError as exc:
            skip_hosts.add(host)
            logger.info("article fetch failed", extra={"host": host, "error": type(exc).__name__})
            return None
        content_type = response.headers.get("content-type", "")
        if (
            response.status_code >= 400
            or "html" not in content_type
            or len(response.content) > self.max_bytes
        ):
            if response.status_code >= 400:
                skip_hosts.add(host)
            return None
        try:
            text = await asyncio.to_thread(
                trafilatura.extract, response.text, include_comments=False, include_tables=False
            )
        except Exception as exc:  # a parser crash on one page must not fail the poll
            logger.info("article extract failed", extra={"host": host, "error": type(exc).__name__})
            return None
        return text.strip() if isinstance(text, str) and text.strip() else None


def detect_language(text: str, hint: str | None = None) -> str | None:
    """ISO 639-1 code (``en``, ``ar``; ``zh-cn`` for Chinese), from the text, else the feed's hint."""
    sample = text.strip()[:2000]
    if len(sample) >= 20:
        try:
            return str(detect(sample))[:16]
        except LangDetectException:
            pass
    if hint:
        return hint.strip().lower().split("-")[0][:16] or None
    return None
