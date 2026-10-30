"""Article text extraction (trafilatura) and language detection (langdetect).

Extraction only runs for new items whose source gave a summary but no body. Each fetch has a
timeout; a host that times out or refuses is not asked again for the rest of that poll, so one slow
site cannot stall a feed. Every failure just leaves ``body`` empty: extraction never fails a poll.

ATL-73 (security round 6, medium): a feed entry's ``link`` is attacker-controlled text, not a
trusted URL. Before any request, and again before following any redirect, the link's host is
resolved with ``getaddrinfo`` and refused unless every address it resolves to is public
(``ipaddress.ip_address(a).is_global``) — this blocks loopback, RFC1918, 169.254/16, CGNAT and
``fc00::/7``/``::1``, and blocks a public page redirecting into one of them.

That check and the actual connection are two separate steps (DNS lookup, then ``httpx`` resolving
and dialling again), so a DNS answer that changes between them (TTL-0 "DNS rebinding") could pass
the check and still land the TCP connection on a loopback/private address. Every hop therefore also
verifies, from the live connection itself (``response.extensions["network_stream"]``, the
``server_addr`` the socket actually dialled), that the peer it ended up talking to is one of the
addresses the pre-connect check approved — before any header or body is read. No network stream or
peer address available is treated as unsafe, the same as a mismatch.

The body is read with the same streamed cap every collector uses (:func:`atlas_ingest.collectors.
read_capped`): a ``Content-Length`` over the cap is rejected before any read, and an
unbounded/chunked body is aborted the moment the cap is passed, so neither a malicious nor merely
huge page can grow this process's memory without bound.

``Extractor.host_check`` (ATL-100) is an optional extra policy on top of the SSRF guard above, run
for the initial host and every redirect hop before anything else for that hop: this module stays
generic (the ingest pipeline passes none), and ``atlas_agents.tools.news`` wires its
``agents.web.allow_domains``/``deny_domains`` policy into it for the agent-facing ``fetch_article``
tool.
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

from atlas_ingest.collectors import CollectError, read_capped

logger = logging.getLogger("atlas_ingest.extract")

DetectorFactory.seed = 0  # langdetect is randomised; a fixed seed makes it deterministic

MAX_REDIRECTS = 3
"""Redirect hops an article fetch will follow, re-checking the target host each time."""

HostResolver = Callable[[str, int], Awaitable[Sequence[str]]]
PeerVerifier = Callable[[httpx.Response, Sequence[str]], bool]
HostCheck = Callable[[str], bool]


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


def peer_matches(response: httpx.Response, allowed: Sequence[str]) -> bool:
    """``True`` only if the socket this ``response`` actually came in on is dialled to one of
    ``allowed`` — the addresses the pre-connect host check approved. Fails closed: a transport
    that does not expose a real network stream (none does outside production: respx and other
    mock transports have none) reports no peer, so there is nothing to verify against."""
    network_stream = response.extensions.get("network_stream")
    if network_stream is None:
        return False
    server_addr = network_stream.get_extra_info("server_addr")
    if not server_addr:
        return False
    peer_ip = str(server_addr[0])
    return peer_ip in set(allowed)


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
    verify_peer: PeerVerifier | None = None
    """Overrides :func:`peer_matches`, the post-connect DNS-rebinding check. Tests only:
    production always verifies the connection really landed on an address the host check
    approved."""
    host_check: HostCheck | None = None
    """An extra per-host policy, checked for the initial host and again for every redirect
    target, before any DNS resolution or request for that hop (ATL-100, security round 9): a
    caller-supplied domain allow/deny list, for example. ``None`` (the default) imposes no extra
    policy; the SSRF checks above still apply either way. A host this rejects is treated exactly
    like one that fails the SSRF check: added to ``skip_hosts`` and the fetch returns ``None``."""

    def timeout_for(self, host: str) -> float:
        return self.host_timeouts.get(host, self.timeout_seconds)

    async def _resolve_safe_addresses(self, host: str, port: int) -> Sequence[str] | None:
        """Every address ``host`` resolves to, if every one of them is public — ``None`` if the
        host fails to resolve, or resolves to even one loopback/private/link-local/CGNAT/ULA
        address. The returned addresses are what the post-connect peer check is verified against,
        so a DNS answer that changes before the actual connection cannot slip past it."""
        resolver = self.resolve_host or resolve_host_addresses
        try:
            addresses = await resolver(host, port)
        except OSError:
            return None
        if not addresses or not all(_is_global_address(address) for address in addresses):
            return None
        return addresses

    async def article_text(self, url: str, skip_hosts: set[str]) -> str | None:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https"):
                return None
            host = (parts.hostname or "").lower()
            if not host or host in skip_hosts:
                return None
            if self.host_check is not None and not self.host_check(host):
                skip_hosts.add(host)
                logger.info("article host blocked by domain policy", extra={"host": host})
                return None
            port = parts.port or (443 if parts.scheme == "https" else 80)
            allowed_addresses = await self._resolve_safe_addresses(host, port)
            if allowed_addresses is None:
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
                    verifier = self.verify_peer or peer_matches
                    if not verifier(response, allowed_addresses):
                        skip_hosts.add(host)
                        logger.info(
                            "article host failed peer verification (possible DNS rebinding)",
                            extra={"host": host},
                        )
                        return None
                    if response.has_redirect_location:
                        if hop == MAX_REDIRECTS:
                            skip_hosts.add(host)
                            return None
                        current = str(response.url.join(response.headers["location"]))
                        continue
                    content_type = response.headers.get("content-type", "")
                    if response.status_code >= 400 or "html" not in content_type:
                        if response.status_code >= 400:
                            skip_hosts.add(host)
                        return None
                    try:
                        body = await read_capped(response, self.max_bytes)
                    except CollectError:
                        return None
                    html_text = body.decode(response.encoding or "utf-8", errors="replace")
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
