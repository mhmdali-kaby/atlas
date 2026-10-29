"""ATL-73 (security round 6, medium): the article extractor must not fetch a feed-supplied link's
host, or any redirect target, unless every address it resolves to is public; must refuse to use a
connection whose actual peer is not one of those checked addresses (DNS rebinding); and must never
read an unbounded body into memory.

These tests use real local HTTP servers, not respx: the guard does its own real DNS resolution and
real peer verification per hop, and that needs a genuine socket to prove "no request made", "no
second hop fetched" and "the connection's real peer was checked". ``conftest._no_real_dns``
replaces the real resolver and the real peer check with ones that trust everything, everywhere
else in this package (so respx-mocked public hostnames never need network and never need a real
socket to verify); here we restore the real resolver and/or the real peer check explicitly, or lie
about one specific host to simulate "DNS said this was public".
"""

from __future__ import annotations

import resource
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from atlas_ingest.collectors import CollectError, read_capped
from atlas_ingest.collectors.rss import RssCollector
from atlas_ingest.extract import Extractor
from atlas_ingest.extract import peer_matches as real_peer_matches
from atlas_ingest.extract import resolve_host_addresses as real_resolve_host_addresses

PUBLIC_FAKE_ADDRESS = "93.184.216.34"  # a real, globally-routable address; never dialled in tests
CHUNK_OK_ARTICLE = "<html><body><article>" + ("word " * 40) + "</article></body></html>"


@contextmanager
def local_server(make_handler: type[BaseHTTPRequestHandler]) -> Iterator[tuple[str, int]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _handler(hits: list[str], respond: object) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            hits.append(self.path)
            respond(self)  # type: ignore[operator]

    return Handler


# ---- AC1a: a direct link to a loopback address is never requested -----------------------------


async def test_loopback_link_returns_none_with_no_request_made() -> None:
    hits: list[str] = []

    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        handler.end_headers()
        handler.wfile.write(CHUNK_OK_ARTICLE.encode())

    with local_server(_handler(hits, respond)) as (host, port):
        async with httpx.AsyncClient() as http:
            extractor = Extractor(
                http=http, timeout_seconds=2, max_bytes=2_000_000, resolve_host=real_resolve_host_addresses
            )
            result = await extractor.article_text(f"http://{host}:{port}/up", set())

    assert result is None
    assert hits == []  # the host check ran before any socket was opened


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9/x",  # loopback
        "http://169.254.169.254/latest/meta-data/",  # link-local (cloud metadata)
        "http://10.0.0.5/x",  # RFC1918
        "http://[::1]/x",  # IPv6 loopback
        "ftp://127.0.0.1/x",  # not http/https at all
    ],
)
async def test_non_global_or_disallowed_links_are_never_fetched(url: str) -> None:
    async with httpx.AsyncClient() as http:
        extractor = Extractor(
            http=http, timeout_seconds=1, max_bytes=2_000_000, resolve_host=real_resolve_host_addresses
        )
        assert await extractor.article_text(url, set()) is None


# ---- AC1b: a redirect's target is re-checked, so a "public" page cannot pivot into a loopback one


async def test_redirect_to_a_loopback_target_is_never_followed() -> None:
    public_hits: list[str] = []
    internal_hits: list[str] = []

    with local_server(_handler(internal_hits, lambda h: (h.send_response(200), h.end_headers()))) as (
        internal_host,
        internal_port,
    ):

        def respond_redirect(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(302)
            handler.send_header("Location", f"http://{internal_host}:{internal_port}/secret")
            handler.end_headers()

        with local_server(_handler(public_hits, respond_redirect)) as (public_host, public_port):
            # The resolver lies only about the first hop (simulating a genuinely public DNS
            # answer); the redirect target is checked for real, and 127.0.0.1 is never global.
            async def lie_about_first_hop(host: str, port: int) -> list[str]:
                if (host, port) == (public_host, public_port):
                    return [PUBLIC_FAKE_ADDRESS]
                return list(await real_resolve_host_addresses(host, port))

            async with httpx.AsyncClient() as http:
                extractor = Extractor(
                    http=http, timeout_seconds=2, max_bytes=2_000_000, resolve_host=lie_about_first_hop
                )
                result = await extractor.article_text(f"http://{public_host}:{public_port}/start", set())

    assert result is None
    assert internal_hits == []  # the redirect target was never requested


# ---- AC1: a public DNS answer at check time that differs from the real connection (DNS
# rebinding, e.g. a TTL-0 attacker record) is still blocked, from the live socket itself ---------


async def test_dns_rebinding_after_check_is_blocked() -> None:
    """The host check approves ``PUBLIC_FAKE_ADDRESS`` (simulating a genuinely public DNS answer
    at check time); the real connection lands on the local server instead (simulating the record
    changing before httpx dials). The mismatch must be caught from the connection itself, not
    assumed away because the earlier getaddrinfo check passed."""
    hits: list[str] = []

    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        handler.end_headers()
        handler.wfile.write(CHUNK_OK_ARTICLE.encode())

    async def lying_resolver(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_FAKE_ADDRESS]

    with local_server(_handler(hits, respond)) as (host, port):
        async with httpx.AsyncClient() as http:
            extractor = Extractor(
                http=http,
                timeout_seconds=2,
                max_bytes=2_000_000,
                resolve_host=lying_resolver,
                verify_peer=real_peer_matches,  # restore the real check: conftest trusts by default
            )
            result = await extractor.article_text(f"http://{host}:{port}/internal", set())

    assert result is None
    assert hits == ["/internal"]  # the request did reach the server: only the result is blocked


# ---- AC1c: an unbounded body is capped, not read fully into memory -----------------------------


async def test_oversized_stream_is_capped_without_reading_it_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """The host check must pass (so the request is really made) while the connection still lands
    on this real local server: the check is lied to (``PUBLIC_FAKE_ADDRESS`` for any host) and the
    post-connect peer check is bypassed on purpose, the same way conftest does it everywhere else
    in this package, because this test is about the body cap, not the rebinding guard (covered by
    ``test_dns_rebinding_after_check_is_blocked`` above)."""
    sent = bytearray()

    def respond_huge(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        handler.end_headers()
        chunk = b"<p>" + b"A" * 65536 + b"</p>"
        try:
            for _ in range(8000):  # ~512 MB, no Content-Length (forces chunked/close-delimited)
                handler.wfile.write(chunk)
                sent.extend(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client closed the socket once it had read past the cap: expected

    async def lying_resolver(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_FAKE_ADDRESS]

    # Wrap aiter_bytes to see exactly what the extractor's cap (read_capped) actually consumed
    # from the stream, independent of how much the server managed to buffer/send.
    read_total = 0
    original_aiter_bytes = httpx.Response.aiter_bytes

    def counting_aiter_bytes(self: httpx.Response, chunk_size: int | None = None) -> object:
        async def wrapped() -> object:
            nonlocal read_total
            async for chunk in original_aiter_bytes(self, chunk_size):
                read_total += len(chunk)
                yield chunk

        return wrapped()

    monkeypatch.setattr(httpx.Response, "aiter_bytes", counting_aiter_bytes)

    hits: list[str] = []
    max_bytes = 2_000_000
    with local_server(_handler(hits, respond_huge)) as (host, port):
        async with httpx.AsyncClient() as http:
            extractor = Extractor(
                http=http,
                timeout_seconds=10,
                max_bytes=max_bytes,
                resolve_host=lying_resolver,
                verify_peer=lambda _response, _allowed: True,
            )
            before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            started = time.monotonic()
            result = await extractor.article_text(f"http://{host}:{port}/big", set())
            elapsed = time.monotonic() - started
            after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert result is None
    assert hits == ["/big"]  # the request really was made: the cap stopped the read, not the check
    # What the extractor actually consumed from the stream must stay at the cap plus at most one
    # more chunk, proving read_capped aborted early rather than draining the ~512 MB on offer.
    assert read_total <= max_bytes + 65536, f"read {read_total} bytes past the {max_bytes} cap"
    assert elapsed < 10  # aborted almost immediately, not after streaming the full ~512 MB
    # ru_maxrss is KB on Linux, bytes on macOS; either way this must stay far under the 512 MB
    # the server offered, proving the cap stopped the read rather than just discarding the result.
    growth = after - before
    assert growth < 200_000_000, f"resident memory grew {growth} (ru_maxrss units) while capped"


# ---- AC1d: RssCollector._fetch (and, by the same helper, the API collectors) obey the same cap --


async def test_rss_fetch_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    import functools

    import atlas_ingest.collectors as collectors_mod

    # read_capped's own default is bound at def time from config (10 MB): swap in a small cap
    # for this test rather than sending 10 MB+ over a local socket.
    monkeypatch.setattr(
        "atlas_ingest.collectors.rss.read_capped",
        functools.partial(collectors_mod.read_capped, max_bytes=1_000_000),
    )

    oversized = b"<rss><channel>" + b"<item><title>x</title></item>" * 100_000 + b"</channel></rss>"
    assert len(oversized) > 1_000_000

    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/rss+xml")
        handler.end_headers()
        handler.wfile.write(oversized)

    hits: list[str] = []
    with local_server(_handler(hits, respond)) as (host, port):
        async with httpx.AsyncClient() as http:
            with pytest.raises(CollectError, match="too large"):
                await RssCollector()._fetch(http, f"http://{host}:{port}/feed.xml", None)


async def test_read_capped_rejects_declared_content_length_up_front() -> None:
    """A Content-Length over the cap is refused before any chunk is read."""

    def respond(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", "50000000")  # lies: declares 50 MB
        handler.end_headers()
        try:
            handler.wfile.write(b"{}")  # far short of the declared length
        except (BrokenPipeError, ConnectionResetError):
            pass

    hits: list[str] = []
    with local_server(_handler(hits, respond)) as (host, port):
        async with httpx.AsyncClient() as http:
            async with http.stream("GET", f"http://{host}:{port}/x") as response:
                with pytest.raises(CollectError, match="too large"):
                    await read_capped(response, max_bytes=1_000_000)
