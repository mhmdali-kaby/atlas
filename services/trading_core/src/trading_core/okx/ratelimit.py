"""Per-endpoint async token buckets, configured from the documented OKX v5 rate limits."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from atlas_shared.defaults import DEFAULTS

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class RateLimit:
    requests: int
    per_seconds: float

    @property
    def rate(self) -> float:
        """Tokens added per second."""
        return self.requests / self.per_seconds


class TokenBucket:
    """A bucket of ``capacity`` tokens refilled continuously at ``rate`` tokens per second."""

    def __init__(
        self, limit: RateLimit, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep
    ) -> None:
        self.limit = limit
        self._capacity = float(limit.requests)
        self._tokens = float(limit.requests)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self.limit.rate)
        self._updated = now

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._sleep((1 - self._tokens) / self.limit.rate)


def default_rate_limits() -> dict[str, RateLimit]:
    raw: Mapping[str, Mapping[str, Any]] = DEFAULTS["okx_rest"]["rate_limits"]
    return {path: RateLimit(int(v["requests"]), float(v["per_seconds"])) for path, v in raw.items()}


class RateLimiter:
    """One token bucket per endpoint path (the part after ``/api/v5/``).

    Share one instance between clients that use the same key (OKX counts private limits per user ID)
    or the same IP (public limits).
    """

    def __init__(
        self,
        limits: Mapping[str, RateLimit] | None = None,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._limits = dict(default_rate_limits() if limits is None else limits)
        self._buckets = {p: TokenBucket(lim, clock=clock, sleep=sleep) for p, lim in self._limits.items()}

    def bucket(self, path: str) -> TokenBucket:
        try:
            return self._buckets[path]
        except KeyError:
            raise KeyError(
                f"no OKX rate limit configured for {path!r}; add it to okx_rest.rate_limits"
            ) from None

    async def acquire(self, path: str) -> None:
        await self.bucket(path).acquire()
