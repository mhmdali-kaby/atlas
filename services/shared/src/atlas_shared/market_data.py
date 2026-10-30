"""The live market-data snapshot trading_core's WebSocket feed keeps in Redis (ATL-125).

One Redis hash per instrument, ``<market_ws.key_prefix>:<env>:<instId>`` (``atlas:md:demo:BTC-USDT-SWAP``):

``last_msg_ms``  epoch ms (UTC) of the last WebSocket message of any channel for the instrument
``ticker``       JSON of the last ``tickers`` push (OKX field names, strings as OKX sends them)
``mark``         JSON of the last ``mark-price`` push
``books5``       JSON of the last ``books5`` push: ``{"asks": [[px, sz, "0", n], ...], "bids": ..., "ts"}``
``<channel>_ms`` epoch ms at which that channel last pushed (``tickers_ms``, ``books5_ms``, ...)
``closed:<bar>`` open time (epoch ms) of the newest confirmed bar already published as candle.closed
``index``        JSON ``{"idxPx", "ts"}`` of the instrument's index price (``market/index-tickers``),
                 written every 5 min by trading_core's derivatives poller (ATL-134), not the socket
``index_ms``     OKX's ``ts`` of that index price (so ``channel_at["index"]`` is its age)

The Risk Engine and the agents read freshness from here without touching the socket: an
instrument is **stale** when it has had no message for ``market_ws.market_data_max_age_seconds``
(or has never had one).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from atlas_shared.defaults import DEFAULTS

LAST_MSG_MS = "last_msg_ms"
TICKER = "ticker"
MARK = "mark"
BOOKS5 = "books5"
INDEX = "index"
CLOSED_PREFIX = "closed:"


class HashReader(Protocol):
    def hgetall(self, name: str) -> Any: ...


def cfg() -> dict[str, Any]:
    value: dict[str, Any] = DEFAULTS["market_ws"]
    return value


def max_age_seconds() -> float:
    return float(cfg()["market_data_max_age_seconds"])


def md_key(env: str, inst_id: str) -> str:
    return f"{cfg()['key_prefix']}:{env}:{inst_id}"


def to_ms(ts: datetime) -> int:
    return int(ts.timestamp()) * 1000 + ts.microsecond // 1000


def from_ms(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _json(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    value = json.loads(raw)
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class MarketSnapshot:
    env: str
    inst_id: str
    last_msg_at: datetime | None
    ticker: dict[str, Any] | None
    mark: dict[str, Any] | None
    books5: dict[str, Any] | None
    channel_at: dict[str, datetime]
    index: dict[str, Any] | None = None

    def age_seconds(self, now: datetime) -> float | None:
        if self.last_msg_at is None:
            return None
        return (now - self.last_msg_at).total_seconds()

    def is_stale(self, now: datetime, max_age: float | None = None) -> bool:
        """True when nothing arrived for more than ``max_age`` seconds (default from config)."""
        age = self.age_seconds(now)
        limit = max_age_seconds() if max_age is None else max_age
        return age is None or age > limit

    def channel_stale(self, channel: str, now: datetime, max_age: float | None = None) -> bool:
        """Like :meth:`is_stale` but for one channel (``books5``, ``tickers``, ``mark-price``)."""
        at = self.channel_at.get(channel)
        limit = max_age_seconds() if max_age is None else max_age
        return at is None or (now - at).total_seconds() > limit


def read_snapshot(client: HashReader, env: str, inst_id: str) -> MarketSnapshot:
    raw: dict[str, str] = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in (client.hgetall(md_key(env, inst_id)) or {}).items()
    }
    last = raw.get(LAST_MSG_MS)
    channel_at = {
        k[: -len("_ms")]: from_ms(v) for k, v in raw.items() if k.endswith("_ms") and k != LAST_MSG_MS
    }
    return MarketSnapshot(
        env=env,
        inst_id=inst_id,
        last_msg_at=from_ms(last) if last else None,
        ticker=_json(raw.get(TICKER)),
        mark=_json(raw.get(MARK)),
        books5=_json(raw.get(BOOKS5)),
        channel_at=channel_at,
        index=_json(raw.get(INDEX)),
    )


__all__ = [
    "BOOKS5",
    "CLOSED_PREFIX",
    "INDEX",
    "LAST_MSG_MS",
    "MARK",
    "TICKER",
    "MarketSnapshot",
    "from_ms",
    "max_age_seconds",
    "md_key",
    "read_snapshot",
    "to_ms",
]
