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

import gzip
import resource
import threading
import time
import zlib
from collections.abc import AsyncIterator, Iterator, Sequence
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


# ---- ATL-100 (security round 9, low): a caller-supplied host_check (a domain allow/deny policy,
# for news.fetch_article) is checked on every hop exactly like the SSRF host check, so a page
# that redirects from a host it allows to one it denies is never requested at the denied host ----


async def test_host_check_blocks_a_redirect_to_a_denied_host_with_no_second_request() -> None:
    hits: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        if request.url.host == "allowed.example":
            return httpx.Response(302, headers={"location": "http://denied.example/secret"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=CHUNK_OK_ARTICLE.encode())

    async def lie_everywhere(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_FAKE_ADDRESS]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        extractor = Extractor(
            http=http,
            timeout_seconds=2,
            max_bytes=2_000_000,
            resolve_host=lie_everywhere,
            verify_peer=lambda _response, _allowed: True,  # this test is about host_check, not peers
            host_check=lambda host: host != "denied.example",
        )
        result = await extractor.article_text("http://allowed.example/start", set())

    assert result is None
    assert hits == ["allowed.example"]  # the redirect target was never requested


async def test_host_check_blocks_the_initial_host_too() -> None:
    hits: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=CHUNK_OK_ARTICLE.encode())

    async def lie_everywhere(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_FAKE_ADDRESS]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        extractor = Extractor(
            http=http,
            timeout_seconds=2,
            max_bytes=2_000_000,
            resolve_host=lie_everywhere,
            verify_peer=lambda _response, _allowed: True,
            host_check=lambda _host: False,
        )
        result = await extractor.article_text("http://denied.example/start", set())

    assert result is None
    assert hits == []  # blocked before any request, like the SSRF host check


@pytest.mark.parametrize("dot", ["\u3002", "\uff0e", "\uff61"])
async def test_host_check_sees_the_idna_normalised_host_not_the_raw_unicode_one(dot: str) -> None:
    """Security round 9 review: a full-width dot (ideographic full stop U+3002, fullwidth full
    stop U+FF0E, or halfwidth ideographic full stop U+FF61) makes ``urlsplit``'s ``.hostname``
    read as a distinct string from ``denied.example``, so a ``host_check`` keyed on the ASCII
    name would be bypassed — while httpx's own IDNA encoding folds the dot back to "." for the
    real TCP connection, landing on the denied host anyway. ``host_check`` must see that same
    IDNA-normalised host, so the denied host is still recognised and no request is ever made."""
    hits: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=CHUNK_OK_ARTICLE.encode())

    async def lie_everywhere(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_FAKE_ADDRESS]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        extractor = Extractor(
            http=http,
            timeout_seconds=2,
            max_bytes=2_000_000,
            resolve_host=lie_everywhere,
            verify_peer=lambda _response, _allowed: True,
            host_check=lambda host: host != "denied.example",
        )
        result = await extractor.article_text(f"https://denied{dot}example/start", set())

    assert result is None
    assert hits == []  # blocked before any request, exactly like the plain-ASCII denied host


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

    # Wrap aiter_raw to see exactly what the extractor's cap (read_capped) actually consumed off
    # the wire, independent of how much the server managed to buffer/send. ATL-75: read_capped
    # now reads the wire bytes itself via aiter_raw (not httpx's auto-decoding aiter_bytes), so
    # that is the call to instrument here.
    read_total = 0
    original_aiter_raw = httpx.Response.aiter_raw

    def counting_aiter_raw(self: httpx.Response, chunk_size: int | None = None) -> object:
        async def wrapped() -> object:
            nonlocal read_total
            async for chunk in original_aiter_raw(self, chunk_size):
                read_total += len(chunk)
                yield chunk

        return wrapped()

    monkeypatch.setattr(httpx.Response, "aiter_raw", counting_aiter_raw)

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


# ---- ATL-75 (security round 7, QA's follow-up on the ATL-73 fix): a Content-Encoding: gzip body
# must not be handed to an unbounded decompressor ------------------------------------------------


# A gzip stream of 512 MB of a single repeated byte, compressed to well under 1 MB on the wire
# (DEFLATE's ordinary ~1000:1 ratio on maximally repetitive input) -- the "zip bomb" shape that,
# before ATL-75, blew well past read_capped's cap in memory before the cap ever got to look at
# it: httpx's own Content-Encoding handling (``Response.aiter_bytes``, used by ``read_capped``
# until this fix) calls zlib's decompressor with no bound on a single call's output, so one small
# compressed chunk decompresses to its full, uncapped size in one shot. 512 MB (not the 4 GB QA
# used by hand) is already two and a half times the 200 MB budget this task sets, decompresses in
# well under a second, and is more than enough to prove the bound holds: the fix caps every
# internal decompress() call at a fixed, small size independent of the total, so nothing about
# *how much* bigger the bomb is changes what it proves.
#
# Built once here, at import time, by feeding a small 64 KB chunk through zlib.compressobj() many
# times over rather than by compressing one 512 MB bytes object in a single call: this process's
# ru_maxrss is a lifetime high-water mark that never falls back down, so building the bomb the
# obvious way (``gzip.compress(b"A" * 512_000_000)``) would itself permanently plant a ~512 MB
# floor under every later measurement in this file -- including the ones that are supposed to
# prove the *fixed* code stays well under that -- and mask the very regression these tests exist
# to catch. zlib.compressobj(level, zlib.DEFLATED, zlib.MAX_WBITS | 16) is the documented way to
# get gzip-framed (not raw deflate) output from the streaming API, matching what a real server's
# `Content-Encoding: gzip` would send.
_bomb_compressor = zlib.compressobj(6, zlib.DEFLATED, zlib.MAX_WBITS | 16)
_bomb_chunk = b"A" * 65536
_bomb_pieces = [_bomb_compressor.compress(_bomb_chunk) for _ in range(512 * 1024 * 1024 // len(_bomb_chunk))]
_bomb_pieces.append(_bomb_compressor.flush())
GZIP_BOMB_512MB = b"".join(_bomb_pieces)
del _bomb_compressor, _bomb_chunk, _bomb_pieces


class _OneShotStream(httpx.AsyncByteStream):
    """Yields ``data`` as a single chunk: the worst case for how much raw/compressed data a
    single read off the wire can hand the decoder in one call. A real local HTTP server's actual
    read sizes for a body this size (~500 KB) vary with the machine and the moment it runs --
    one trial here measured a ~354 MB spike on the unfixed code, a second with a smaller
    ``max_bytes`` measured only ~84 MB, comfortably under this task's 200 MB budget even though
    the code was identically vulnerable both times. That is not a reliable way to prove a memory
    bound; forcing the single-worst-case shape deterministically, bypassing the real socket
    entirely via ``httpx.MockTransport``, removes the variance from what these tests prove."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._data

    async def aclose(self) -> None:
        return None


def _gzip_bomb_transport(content_type: str) -> httpx.MockTransport:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": content_type, "content-encoding": "gzip"},
            stream=_OneShotStream(GZIP_BOMB_512MB),
        )

    return httpx.MockTransport(handle)


async def test_gzip_bomb_article_body_is_capped_without_memory_blowup() -> None:
    """A feed-supplied article link whose server answers with a gzip-encoded body that
    decompresses to ~512 MB, delivered as one single read off the wire (the worst case), must
    still return ``None`` from ``article_text``, and the 512 MB must never exist in memory at
    once to get there. Without the ATL-75 fix this fails on the memory assertion even though the
    function still eventually returns ``None`` -- the bomb already did its damage by the time the
    cap got to react."""

    async def lying_resolver(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_FAKE_ADDRESS]

    async with httpx.AsyncClient(transport=_gzip_bomb_transport("text/html")) as http:
        extractor = Extractor(
            http=http,
            timeout_seconds=10,
            max_bytes=2_000_000,
            resolve_host=lying_resolver,
            verify_peer=lambda _response, _allowed: True,  # this test is about the body cap
        )
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.monotonic()
        result = await extractor.article_text("http://bomb.example/article", set())
        elapsed = time.monotonic() - started
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert result is None
    assert elapsed < 10  # aborted almost immediately, not after decompressing ~512 MB
    growth = after - before
    # ru_maxrss is KB on Linux, bytes on macOS; either way the ~512 MB this decompresses to must
    # never exist in memory at once -- the task's 200 MB budget, in ru_maxrss units.
    assert growth < 200_000_000, f"resident memory grew {growth} (ru_maxrss units) on a gzip bomb"


async def test_read_capped_bounds_a_single_gzip_decode_call() -> None:
    """Even one compressed read must never decompress to more than a small, fixed amount at once
    -- proof that the cap is enforced piece by piece as decoding happens, not only once an
    already-fully-decompressed total is compared against max_bytes (which is what a decoder that
    collected every bounded piece into a list before returning, rather than yielding them, would
    still do: the whole thing would already be in memory by the time the list existed). A
    CollectError alone would not prove this -- the pre-fix code raises the very same error, just
    after the 512 MB spike has already happened; the memory assertion is what actually tells the
    two apart, exactly as it does for the ``Extractor.article_text`` case above."""
    response = httpx.Response(
        200,
        headers={"content-type": "application/json", "content-encoding": "gzip"},
        stream=_OneShotStream(GZIP_BOMB_512MB),
    )
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with pytest.raises(CollectError, match="too large"):
        await read_capped(response, max_bytes=1_000_000)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    growth = after - before
    assert growth < 200_000_000, f"resident memory grew {growth} (ru_maxrss units) on a gzip bomb"


async def test_a_legitimately_gzip_encoded_body_still_decodes_correctly() -> None:
    """The flip side of the two tests above: an ordinary, well-behaved gzip-encoded response (not
    a bomb) must still come out of read_capped fully and correctly decoded. Nothing about reading
    the wire bytes ourselves and decompressing them a piece at a time, instead of letting httpx's
    own ``aiter_bytes`` do it in one call, may change what a legitimate response decodes to."""
    article = b"<html><body><article><p>" + b"word " * 60 + b"</p></article></body></html>"
    response = httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8", "content-encoding": "gzip"},
        stream=_OneShotStream(gzip.compress(article)),
    )
    body = await read_capped(response, max_bytes=1_000_000)
    assert body == article


# ---- ATL-75 round-1 review: a malformed encoded body must raise CollectError, not escape as a
# bare zlib.error -- and a legitimate raw-deflate body must still decode -----------------------


async def test_read_capped_raises_collect_error_on_malformed_gzip_body() -> None:
    """Garbage bytes under ``Content-Encoding: gzip`` used to raise a bare ``zlib.error`` out of
    ``read_capped``, which no caller catches (only ``CollectError``/``httpx.HTTPError`` are
    handled). This must come out as ``CollectError`` instead, exactly like the old
    httpx.DecodingError (an ``httpx.HTTPError``) path did before ATL-75."""
    response = httpx.Response(
        200,
        headers={"content-type": "application/json", "content-encoding": "gzip"},
        stream=_OneShotStream(b"not actually gzip data, just garbage bytes" * 10),
    )
    with pytest.raises(CollectError, match="could not be decoded"):
        await read_capped(response, max_bytes=1_000_000)


async def test_article_text_returns_none_for_malformed_gzip_body() -> None:
    """The end-to-end path a feed article hits: a gzip-labelled but non-gzip body must make
    ``article_text`` return ``None``, the same as any other fetch failure -- not raise and abort
    the whole poll via ``pipeline.py``'s ``asyncio.gather``."""

    async def lying_resolver(_host: str, _port: int) -> Sequence[str]:
        return [PUBLIC_FAKE_ADDRESS]

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=_OneShotStream(b"garbage, not gzip" * 10),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        extractor = Extractor(
            http=http,
            timeout_seconds=10,
            max_bytes=2_000_000,
            resolve_host=lying_resolver,
            verify_peer=lambda _response, _allowed: True,
        )
        result = await extractor.article_text("http://bad-gzip.example/article", set())
    assert result is None


async def test_read_capped_decodes_a_legitimate_raw_deflate_body() -> None:
    """Some servers send ``Content-Encoding: deflate`` as raw DEFLATE with no zlib wrapper, which
    ``httpx``'s own ``DeflateDecoder`` used to fall back to on the first failed decompress.
    ``_BoundedZlibDecoder`` must retry the same way rather than treating every raw-deflate body as
    malformed."""
    article = b"<html><body><article><p>" + b"word " * 60 + b"</p></article></body></html>"
    raw_deflate = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    body_bytes = raw_deflate.compress(article) + raw_deflate.flush()
    response = httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8", "content-encoding": "deflate"},
        stream=_OneShotStream(body_bytes),
    )
    body = await read_capped(response, max_bytes=1_000_000)
    assert body == article
