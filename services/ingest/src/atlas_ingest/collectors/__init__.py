"""Collectors: one module per collector in this package, each registering itself.

A collector turns one ``news_sources`` row into ``RawItem``s. Add one by creating
``atlas_ingest/collectors/<name>.py`` with::

    from atlas_ingest.collectors import CollectContext, SourceConfig, register
    from atlas_ingest.items import RawItem

    @register(keys=("cryptopanic",))           # or kinds=("calendar",) for every source of a kind
    class CryptoPanicCollector:
        async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]: ...

:func:`discover` imports every module in this package, so nothing else needs editing. Lookup
prefers a collector registered for the source's exact key, then one registered for its kind.

Raise :class:`CollectError` (its message is stored in ``news_sources.last_error``, so keep it short
and never put a key or a keyed URL in it) or let an ``httpx`` error propagate; the runner turns
either into a failure. Returning an empty list is a success.

ATL-73: fetch every source response with :func:`read_capped` instead of ``ctx.http.get(...)``/
``response.content``, so a feed or a provider that answers with (or is tricked into sending) an
unbounded body cannot grow this process's memory without limit.

ATL-75 (security round 7, same medium re-filed before ATL-73 had merged; QA's round-7 follow-up
on the merged fix): a ``Content-Encoding: gzip``/``deflate`` response is not read through
``httpx``'s own automatic decoding any more. That decoding (``Response.aiter_bytes``) calls
``zlib``'s decompressor with no output bound on a single call, so one small, legitimately-sized
compressed chunk can expand (a "zip bomb" — ~1000:1 is ordinary for DEFLATE) into hundreds of
megabytes *before* :func:`read_capped` ever gets to compare anything against ``max_bytes``; a feed
that serves one as a chunked, undeclared-length body measured at a 415 MB RSS peak even with the
cap in place. :func:`read_capped` now reads the still-encoded wire bytes itself
(``response.aiter_raw``), capping their total the same way the decoded total is capped, and
decompresses gzip/deflate through :class:`_BoundedZlibDecoder`, which calls
``zlib.Decompress.decompress(data, max_length)`` in a loop over ``unconsumed_tail`` so a single
call can never produce more than ``READ_CHUNK_BYTES`` worth of output, however compressible the
input was. An encoding this module does not decompress itself (anything other than a single
``gzip`` or ``deflate`` value — brotli and zstd are never installed here, so ``httpx`` already left
those as opaque bytes) is left exactly as the server sent it rather than ever handed to an
unbounded decompressor.

ATL-75 round-1 review fix: a malformed ``gzip``/``deflate`` body (garbage bytes under a
``Content-Encoding`` header a feed or article server attached) used to raise ``zlib.error``
straight out of :func:`read_capped`. No caller catches that — only :class:`CollectError` and
``httpx.HTTPError`` are handled in ``extract.py``, ``rss.py``'s per-symbol loop and the API
collectors — so one bad article aborted the whole poll via ``pipeline.py``'s ``asyncio.gather``,
and three such polls in a row disabled the source. :func:`read_capped` now catches ``zlib.error``
around every call into the decoder and re-raises it as :class:`CollectError`, the same family the
old ``httpx``-decoded path raised (``httpx.DecodingError`` is an ``httpx.HTTPError``). It also
restores the raw-deflate fallback ``httpx.DeflateDecoder`` used to provide: some servers send
``Content-Encoding: deflate`` as raw DEFLATE (no zlib wrapper) rather than zlib-wrapped DEFLATE:
:class:`_BoundedZlibDecoder` retries once, on the very first decoded chunk only, with a raw
(``-zlib.MAX_WBITS``) decompressor before giving up.
"""

from __future__ import annotations

import importlib
import pkgutil
import zlib
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, TypeVar

import httpx
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine

from atlas_ingest.items import RawItem

MAX_RESPONSE_BYTES: int = int(DEFAULTS["news"]["max_response_bytes"])
READ_CHUNK_BYTES = 65536


@dataclass(frozen=True)
class SourceConfig:
    key: str
    kind: str
    category: str
    name: str
    url: str | None
    poll_seconds: int
    api_key: str | None = field(default=None, repr=False)
    """Decrypted key for keyed sources. Use it in requests only: never log or store it."""


@dataclass(frozen=True)
class CollectContext:
    http: httpx.AsyncClient
    now: datetime
    stock_symbols: Callable[[], Awaitable[list[str]]]
    """Underlying symbols of every live stock/ETF instrument in ``instruments`` for the runner's
    active env (e.g. ``TSLA``, ``SPY``)."""
    engine: Engine | None = None
    """For collectors that write structured rows themselves (calendar, earnings, sentiment)."""
    stats: PollStats = field(default_factory=lambda: PollStats())
    """Collectors that write their own rows add the count to ``stats.written``."""


@dataclass
class PollStats:
    written: int = 0
    """Rows a structured collector inserted or updated; counts like news items for last_item_at."""


class Collector(Protocol):
    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]: ...


class PollIntervalHook(Protocol):
    """Optional on a collector: after a successful poll the runner asks for the delay until the
    next one. ``None`` keeps ``poll_seconds``. Used by the calendar collectors to poll every minute
    around scheduled events."""

    async def next_poll_seconds(self, source: SourceConfig, ctx: CollectContext) -> float | None: ...


class CollectError(Exception):
    """A failure whose message is safe to store as last_error (short, no secrets, no keyed URL)."""


class RateLimited(CollectError):
    """The provider refused with a rate limit (HTTP 429). Counts as one failure, and the runner
    does not poll the source again for at least ``retry_after_seconds``."""

    def __init__(self, retry_after_seconds: float, message: str = "rate limited (HTTP 429)") -> None:
        super().__init__(message)
        self.retry_after_seconds = max(float(retry_after_seconds), 0.0)


def retry_after_seconds(response: httpx.Response, default: float) -> float:
    """Seconds from a Retry-After header (delta form), never less than ``default``."""
    value = response.headers.get("retry-after", "").strip()
    try:
        return max(float(value), default) if value else default
    except ValueError:
        return default


class _IdentityDecoder:
    """No ``Content-Encoding`` recognised (including none at all): the wire bytes are the body."""

    def decode(self, data: bytes) -> Iterator[bytes]:
        if data:
            yield data

    def flush(self) -> Iterator[bytes]:
        return iter(())


class _BoundedZlibDecoder:
    """A single ``gzip``/``deflate`` layer, decompressed in ``READ_CHUNK_BYTES`` pieces (ATL-75).

    ``decode`` is a *generator*: it must yield one piece at a time rather than build and return a
    list, so the caller's cap check runs — and can abort — between pieces, before the next one is
    even produced. A version that collected every piece into a list first (an earlier draft of
    this fix did exactly that) would still materialise the whole decompressed output in memory
    before the caller ever got to look at it, defeating the point.

    Each piece comes from ``zlib.Decompress.decompress(data, max_length)``, which returns at most
    ``max_length`` bytes and leaves whatever it could not fit in ``unconsumed_tail``; looping on
    that tail bounds the output of *every* call to ``max_length``, regardless of how compressible
    ``data`` was. A plain ``decompress(data)`` — what ``httpx``'s own decoder, and this module
    before ATL-75, both use — does not: it decompresses everything a chunk yields in one call,
    with no cap, which is how a few hundred KB of attacker-supplied compressed bytes became a
    415 MB resident-memory spike even with the ATL-73 cap already in place.

    ``retry_raw_deflate`` mirrors ``httpx.DeflateDecoder``'s fallback: a server labelled
    ``Content-Encoding: deflate`` may send either zlib-wrapped DEFLATE (``zlib.MAX_WBITS``, what
    this decoder is constructed with for ``deflate``) or raw DEFLATE with no zlib header
    (``-zlib.MAX_WBITS``) — both are "deflate" in the wild, and there is no way to tell which from
    the header alone. If the very first decode call raises ``zlib.error``, and only then, this
    rebuilds the decompressor in raw mode and retries that same chunk once; any later failure (a
    second bad chunk, or a failure after the retry already happened) is never retried again and
    propagates as ``zlib.error`` for :func:`read_capped` to turn into :class:`CollectError`. gzip
    has an unambiguous magic header, so it is never retried this way.
    """

    def __init__(self, wbits: int, *, retry_raw_deflate: bool = False) -> None:
        self._z = zlib.decompressobj(wbits)
        self._retry_raw_deflate = retry_raw_deflate
        self._first_chunk = True

    def decode(self, data: bytes) -> Iterator[bytes]:
        may_retry = self._retry_raw_deflate and self._first_chunk
        self._first_chunk = False
        pending = data
        while True:
            try:
                out = self._z.decompress(pending, READ_CHUNK_BYTES)
            except zlib.error:
                if may_retry:
                    may_retry = False
                    self._retry_raw_deflate = False
                    self._z = zlib.decompressobj(-zlib.MAX_WBITS)
                    pending = data
                    continue
                raise
            if out:
                yield out
            pending = self._z.unconsumed_tail
            if not pending:
                return

    def flush(self) -> Iterator[bytes]:
        tail = self._z.flush()
        if tail:
            yield tail


_ZLIB_WBITS_BY_ENCODING = {"gzip": zlib.MAX_WBITS | 16, "deflate": zlib.MAX_WBITS}


def _decoder_for(content_encoding: str) -> _IdentityDecoder | _BoundedZlibDecoder:
    """A bounded decoder for exactly one recognised ``Content-Encoding`` value; identity (the
    bytes are left untouched) for anything else, including no header, an unrecognised value, and
    more than one value (chained encodings are not something any of our sources send; refusing to
    guess at a safe way to bound a chain is safer than running one through an unbounded one)."""
    encoding = content_encoding.strip().lower()
    wbits = _ZLIB_WBITS_BY_ENCODING.get(encoding)
    if wbits is None:
        return _IdentityDecoder()
    return _BoundedZlibDecoder(wbits, retry_raw_deflate=(encoding == "deflate"))


async def read_capped(response: httpx.Response, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """The full decoded body of a streamed ``response``, read in bounded chunks (ATL-73, ATL-75).

    A declared ``Content-Length`` over ``max_bytes`` is rejected before any read. The wire
    (encoded) bytes are read via ``aiter_raw`` rather than ``httpx``'s own auto-decoding
    ``aiter_bytes``, and their running total is capped at ``max_bytes`` too, exactly like the
    decoded total below — so a tiny, well-formed compressed body can never be used to read an
    unlimited amount off the wire either. Decoding, when the response is ``gzip``/``deflate``
    encoded, goes through :class:`_BoundedZlibDecoder` so a single call can never produce more
    than ``READ_CHUNK_BYTES`` of decoded output regardless of the compression ratio ("zip bomb")
    of the chunk fed to it; either cap being passed raises :class:`CollectError` immediately, so
    neither an unbounded body nor a compression bomb is ever held in memory past the cap. A body
    that is simply not valid gzip/deflate under the ``Content-Encoding`` it was served with (a
    malformed or truncated compressed body, not a bomb) raises ``zlib.error`` from the decoder;
    that is caught here and re-raised as :class:`CollectError`, the family every caller of this
    function already handles, instead of escaping uncaught (ATL-75 round-1 review). Call inside
    ``async with ctx.http.stream(...) as response:``.
    """
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise CollectError("response too large")
        except ValueError:
            pass
    decoder = _decoder_for(response.headers.get("content-encoding", ""))
    body = bytearray()
    raw_total = 0
    try:
        async for raw_chunk in response.aiter_raw(READ_CHUNK_BYTES):
            raw_total += len(raw_chunk)
            if raw_total > max_bytes:
                raise CollectError("response too large")
            for decoded_chunk in decoder.decode(raw_chunk):
                body.extend(decoded_chunk)
                if len(body) > max_bytes:
                    raise CollectError("response too large")
        for decoded_chunk in decoder.flush():
            body.extend(decoded_chunk)
            if len(body) > max_bytes:
                raise CollectError("response too large")
    except zlib.error as exc:
        raise CollectError("response could not be decoded") from exc
    return bytes(body)


C = TypeVar("C")

_by_key: dict[str, Callable[[], Collector]] = {}
_by_kind: dict[str, Callable[[], Collector]] = {}


def register(*, keys: tuple[str, ...] = (), kinds: tuple[str, ...] = ()) -> Callable[[C], C]:
    """Class decorator: the class is instantiated (no arguments) once per runner."""
    if not keys and not kinds:
        raise ValueError("register() needs at least one key or kind")

    def decorate(cls: C) -> C:
        for key in keys:
            if key in _by_key:
                raise ValueError(f"a collector is already registered for source {key!r}")
            _by_key[key] = cls  # type: ignore[assignment]
        for kind in kinds:
            if kind in _by_kind:
                raise ValueError(f"a collector is already registered for kind {kind!r}")
            _by_kind[kind] = cls  # type: ignore[assignment]
        return cls

    return decorate


def discover() -> None:
    """Import every collector module in this package so its @register runs."""
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")


def factory_for(key: str, kind: str) -> Callable[[], Collector] | None:
    return _by_key.get(key) or _by_kind.get(kind)
