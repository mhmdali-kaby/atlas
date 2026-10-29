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
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, TypeVar

import httpx
from sqlalchemy import Engine

from atlas_ingest.items import RawItem


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
