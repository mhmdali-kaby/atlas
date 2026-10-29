"""Article text extraction (trafilatura) and language detection (langdetect).

Extraction only runs for new items whose source gave a summary but no body. Each fetch has a
timeout; a host that times out or refuses is not asked again for the rest of that poll, so one slow
site cannot stall a feed. Every failure just leaves ``body`` empty: extraction never fails a poll.

ATL-73 (security round 6, medium): a feed entry's ``link`` is attacker-controlled text, not a
trusted URL. Before any request, and again before following any redirect, the link's host is
resolved with ``getaddrinfo`` and refused unless every address it resolves to is public
(``ipaddress.ip_address(a).is_global``) — this blocks loopback, RFC1918, 169.254/16, CGNAT and
``fc00::/7``/``::1``, and blocks a public page redirecting into one of them. The body is read with
a streamed cap (:data:`Extractor.max_bytes`): a ``Content-Length`` over the cap is rejected before
any read, and an unbounded/chunked body is aborted the moment the cap is passed, so neither a
malicious nor merely huge page can grow this process's memory without bound.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import trafilatura
from langdetect import DetectorFactory, LangDetectException, detect

logger = logging.getLogger("atlas_ingest.extract")

DetectorFactory.seed = 0  # langdetect is randomised; a fixed seed makes it deterministic

MAX_REDIRECTS = 3
"""Redirect hops an article fetch will follow, re-checking the target host each time."""

READ_CHUNK_BYTES = 65536

HostResolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def resolve_host_addresses(host: str, port: int) -> Sequence[str]:
    """Every address ``host`` resolves to, via the event loop's resolver (asyncio ``getaddrinfo``)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_global_address(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


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
    resolve_host: HostResolver | None = None
    """Overrides :func:`resolve_host_addresses`. Tests only: production always uses the real
    resolver, never a value that could be fooled by something other than real DNS."""

    def timeout_for(self, host: str) -> float:
        return self.host_timeouts.get(host, self.timeout_seconds)

    async def _host_is_safe(self, host: str, port: int) -> bool:
        """``True`` only if every address ``host`` resolves to is public. A host that fails to
        resolve, or resolves to even one loopback/private/link-local/CGNAT/ULA address, is not."""
        resolver = self.resolve_host or resolve_host_addresses
        try:
            addresses = await resolver(host, port)
        except OSError:
            return False
        if not addresses:
            return False
        return all(_is_global_address(address) for address in addresses)

    async def article_text(self, url: str, skip_hosts: set[str]) -> str | None:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https"):
                return None
            host = (parts.hostname or "").lower()
            if not host or host in skip_hosts:
                return None
            port = parts.port or (443 if parts.scheme == "https" else 80)
            if not await self._host_is_safe(host, port):
                skip_hosts.add(host)
                logger.info("article host blocked (not a public address)", extra={"host": host})
                return None
            try:
                async with self.http.stream(
                    "GET",
                    current,
                    timeout=httpx.Timeout(self.timeout_for(host)),
                    follow_redirects=False,
                    headers={"User-Agent": self.user_agent} if self.user_agent else None,
                ) as response:
                    if response.has_redirect_location:
                        if hop == MAX_REDIRECTS:
                            skip_hosts.add(host)
                            return None
                        current = str(response.url.join(response.headers["location"]))
                        continue
                    content_type = response.headers.get("content-type", "")
                    too_big = False
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            too_big = int(content_length) > self.max_bytes
                        except ValueError:
                            too_big = False
                    if response.status_code >= 400 or "html" not in content_type or too_big:
                        if response.status_code >= 400:
                            skip_hosts.add(host)
                        return None
                    body = bytearray()
                    overflowed = False
                    async for chunk in response.aiter_bytes(READ_CHUNK_BYTES):
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            overflowed = True
                            break
                    if overflowed:
                        return None
                    html_text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as exc:
                skip_hosts.add(host)
                logger.info("article fetch failed", extra={"host": host, "error": type(exc).__name__})
                return None
            try:
                text = await asyncio.to_thread(
                    trafilatura.extract, html_text, include_comments=False, include_tables=False
                )
            except Exception as exc:  # a parser crash on one page must not fail the poll
                logger.info("article extract failed", extra={"host": host, "error": type(exc).__name__})
                return None
            return text.strip() if isinstance(text, str) and text.strip() else None
        return None  # pragma: no cover - unreachable, every branch above returns or continues


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
