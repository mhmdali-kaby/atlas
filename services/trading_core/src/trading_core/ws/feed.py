"""OKX public + business WebSocket market feed for the active env (ATL-125, brief §4.1/§4.2, #249 F).

Two connections, URLs from ``okx.environments.<env>.ws_public_url`` / ``ws_business_url``:

* public: ``market_ws.public_channels`` (tickers, mark-price, books5)
* business: ``market_ws.business_channels`` (candle1m, candle5m, candle1H)

for every instrument in :func:`~trading_core.ws.subscriptions.desired_instruments`, re-read every
``subscriptions_refresh_seconds`` (and on :meth:`MarketFeed.refresh`), with subscribe/unsubscribe
diffs sent on the live sockets. Each socket sends OKX's text ``ping`` every ``ping_interval_seconds``;
when a socket drops it reconnects with jittered exponential backoff and resubscribes everything.

Every push stamps the instrument's Redis hash (:mod:`atlas_shared.market_data`) with the message
time and stores the latest ticker / mark price / books5. ``market.tick`` goes on ``atlas.market`` at
most once per ``tick_publish_min_interval_seconds`` per instrument. A confirmed candle is upserted
into ``candles`` through the poller's ON CONFLICT path and published once as ``candle.closed``
(the Redis field ``closed:<bar>`` remembers the newest published bar, across restarts). An
instrument with no message for ``market_data_max_age_seconds`` is stale: ``market.stale`` is
published when it goes stale and ``market.fresh`` when data comes back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from atlas_shared import bus
from atlas_shared import market_data as md
from atlas_shared.db import Candle as CandleRow
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine
from websockets.asyncio.client import connect as ws_connect

from trading_core.backfill.runner import upsert_rows
from trading_core.marketdata.grid import candle_rows
from trading_core.okx.models import Candle, MarkPrice, Ticker
from trading_core.ws.clock import Clock, RealClock
from trading_core.ws.subscriptions import desired_instruments

logger = logging.getLogger("trading_core.ws")

STREAM = "atlas.market"
SOURCE = "service:trading_core.market_ws"
TICK = "market.tick"
CANDLE_CLOSED = "candle.closed"
STALE = "market.stale"
FRESH = "market.fresh"
CANDLE_PREFIX = "candle"
SUBSCRIBE_BATCH = 50


def ws_cfg() -> dict[str, Any]:
    value: dict[str, Any] = DEFAULTS["market_ws"]
    return value


def ws_urls(env: str) -> dict[str, str]:
    """``{"public": ..., "business": ...}`` for ``env``, from config/defaults.yaml only."""
    envs: Mapping[str, Any] = DEFAULTS["okx"]["environments"]
    if env not in envs:
        raise ValueError(f"unknown OKX environment {env!r}")
    return {"public": str(envs[env]["ws_public_url"]), "business": str(envs[env]["ws_business_url"])}


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


Connector = Callable[[str], AbstractAsyncContextManager[Any]]
Publisher = Callable[[str, EventEnvelope], object]
InstrumentSource = Callable[[], set[str]]


def default_connector(url: str) -> AbstractAsyncContextManager[Any]:
    # OKX's keepalive is the text "ping"/"pong" pair, so the protocol-level ping is off.
    return ws_connect(url, ping_interval=None, open_timeout=10, close_timeout=5, max_size=2**22)


@dataclass
class Link:
    name: str
    url: str
    channels: tuple[str, ...]
    ws: Any = None
    subscribed: set[tuple[str, str]] = field(default_factory=set)
    connects: int = 0
    pings: int = 0
    backoffs: list[float] = field(default_factory=list)


def backoff_seconds(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """``min(max, base * 2^attempt)`` scaled by a factor in ``[1 - jitter, 1]``."""
    cfg = ws_cfg()
    raw: float = min(float(cfg["reconnect_max_seconds"]), float(cfg["reconnect_base_seconds"]) * 2.0**attempt)
    jitter = float(cfg["reconnect_jitter"])
    return float(raw * (1 - jitter * rand()))


def _ms(ts: datetime) -> int:
    return md.to_ms(ts)


def _dec(value: Any) -> str | None:
    return None if value is None else str(value)


class MarketFeed:
    def __init__(
        self,
        env: str,
        engine: Engine,
        redis: Any,
        *,
        publish: Publisher | None = None,
        connect: Connector = default_connector,
        clock: Clock | None = None,
        instruments: InstrumentSource | None = None,
        urls: Mapping[str, str] | None = None,
        rand: Callable[[], float] = random.random,
    ) -> None:
        cfg = ws_cfg()
        self.env = env
        self.engine = engine
        self.redis = redis
        self._publish: Publisher = publish or (lambda s, e: bus.publish(s, e, client=redis))
        self._connect = connect
        self.clock: Clock = clock or RealClock()
        self._instruments: InstrumentSource = instruments or (
            lambda: desired_instruments(engine, env, self.clock.now())
        )
        self._rand = rand
        resolved = dict(urls) if urls is not None else ws_urls(env)
        self.links = {
            "public": Link("public", resolved["public"], tuple(cfg["public_channels"])),
            "business": Link("business", resolved["business"], tuple(cfg["business_channels"])),
        }
        self.ping_interval = float(cfg["ping_interval_seconds"])
        self.max_age = float(cfg["market_data_max_age_seconds"])
        self.refresh_every = float(cfg["subscriptions_refresh_seconds"])
        self.stale_every = float(cfg["stale_check_seconds"])
        self.tick_every = float(cfg["tick_publish_min_interval_seconds"])
        self.ttl = int(cfg["snapshot_ttl_seconds"])
        self.instruments: set[str] = set()
        self._since: dict[str, datetime] = {}
        self._last_msg: dict[str, datetime] = {}
        self._last_tick_pub: dict[str, datetime] = {}
        self._closed: dict[tuple[str, str], int] = {}
        self._stale: set[str] = set()
        self._tasks: list[asyncio.Task[None]] = []

    # -- lifecycle ---------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)

    async def start(self) -> None:
        if self.running:
            return
        await self.refresh(send=False)
        self._tasks = [
            asyncio.create_task(self._link_loop(link), name=f"ws:{link.name}") for link in self.links.values()
        ]
        self._tasks.append(
            asyncio.create_task(self._every(self.refresh_every, self.refresh), name="ws:refresh")
        )
        self._tasks.append(
            asyncio.create_task(self._every(self.stale_every, self.check_stale), name="ws:stale")
        )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _every(self, seconds: float, fn: Callable[[], Any]) -> None:
        while True:
            await self.clock.sleep(seconds)
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("market feed job failed")

    # -- subscriptions -----------------------------------------------------------------------------

    async def refresh(self, send: bool = True) -> set[str]:
        """Re-read the instrument set and, on live sockets, subscribe/unsubscribe the difference."""
        wanted = await asyncio.to_thread(self._instruments)
        now = self.clock.now()
        for inst in wanted - self.instruments:
            self._since[inst] = now
        for inst in self.instruments - wanted:
            self._since.pop(inst, None)
            self._stale.discard(inst)
        self.instruments = set(wanted)
        if send:
            for link in self.links.values():
                if link.ws is not None:
                    await self._sync(link)
        return self.instruments

    def _desired(self, link: Link) -> set[tuple[str, str]]:
        return {(ch, inst) for ch in link.channels for inst in self.instruments}

    async def _sync(self, link: Link) -> None:
        ws = link.ws
        if ws is None:
            return
        wanted = self._desired(link)
        add = sorted(wanted - link.subscribed)
        drop = sorted(link.subscribed - wanted)
        await self._send_op(ws, "unsubscribe", drop)
        await self._send_op(ws, "subscribe", add)
        link.subscribed = (link.subscribed - set(drop)) | set(add)

    @staticmethod
    async def _send_op(ws: Any, op: str, pairs: Sequence[tuple[str, str]]) -> None:
        for start in range(0, len(pairs), SUBSCRIBE_BATCH):
            args = [{"channel": ch, "instId": inst} for ch, inst in pairs[start : start + SUBSCRIBE_BATCH]]
            await ws.send(json.dumps({"op": op, "args": args}))

    # -- connections -------------------------------------------------------------------------------

    async def _link_loop(self, link: Link) -> None:
        attempt = 0
        while True:
            try:
                async with self._connect(link.url) as ws:
                    link.ws = ws
                    link.connects += 1
                    link.subscribed = set()
                    attempt = 0
                    await self._sync(link)
                    pinger = asyncio.create_task(self._ping_loop(link, ws), name=f"ws:{link.name}:ping")
                    try:
                        async for raw in ws:
                            await self.on_message(raw)
                    finally:
                        pinger.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await pinger
                        link.ws = None
                logger.warning("market socket closed", extra={"link": link.name})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "market socket failed",
                    extra={"link": link.name, "error": f"{type(exc).__name__}: {exc}"[:300]},
                )
            delay = backoff_seconds(attempt, self._rand)
            attempt += 1
            link.backoffs.append(delay)
            await self.clock.sleep(delay)

    async def _ping_loop(self, link: Link, ws: Any) -> None:
        while True:
            await self.clock.sleep(self.ping_interval)
            await ws.send("ping")
            link.pings += 1

    # -- messages ----------------------------------------------------------------------------------

    async def on_message(self, raw: str | bytes) -> None:
        text = raw.decode() if isinstance(raw, bytes) else raw
        if text == "pong":
            return
        try:
            msg = json.loads(text)
        except ValueError:
            logger.warning("market socket sent non-JSON", extra={"head": text[:120]})
            return
        if not isinstance(msg, dict):
            return
        if "event" in msg:
            if msg.get("event") == "error":
                logger.warning(
                    "market socket error event",
                    extra={"code": msg.get("code"), "msg": str(msg.get("msg"))[:300]},
                )
            return
        arg = msg.get("arg") or {}
        channel = str(arg.get("channel", ""))
        inst = arg.get("instId")
        data = msg.get("data") or []
        if not inst or not isinstance(data, list) or not data:
            return
        inst = str(inst)
        now = self.clock.now()
        fields: dict[str, str] = {md.LAST_MSG_MS: str(_ms(now)), f"{channel}_ms": str(_ms(now))}
        try:
            if channel == "tickers":
                fields[md.TICKER] = json.dumps(data[-1])
            elif channel == "mark-price":
                MarkPrice.model_validate(data[-1])
                fields[md.MARK] = json.dumps(data[-1])
            elif channel == "books5":
                fields[md.BOOKS5] = json.dumps(data[-1])
        except ValueError:
            logger.warning("market push did not parse", extra={"channel": channel, "inst_id": inst})
            return
        self._write(inst, fields)
        self._last_msg[inst] = now
        if inst in self._stale:
            self._stale.discard(inst)
            self._emit(FRESH, {"inst_id": inst, "last_msg_at": now.isoformat()})
        if channel == "tickers":
            self._maybe_tick(inst, data[-1], now)
        elif channel.startswith(CANDLE_PREFIX):
            await self._candles(inst, channel[len(CANDLE_PREFIX) :], data)

    def _write(self, inst: str, fields: Mapping[str, str]) -> None:
        key = md.md_key(self.env, inst)
        pipe = self.redis.pipeline()
        pipe.hset(key, mapping=dict(fields))
        pipe.expire(key, self.ttl)
        pipe.execute()

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        envelope = EventEnvelope(type=kind, env=self.env, ts=self.clock.now(), source=SOURCE, payload=payload)
        try:
            self._publish(STREAM, envelope)
        except Exception as exc:
            logger.warning(
                "market event publish failed", extra={"type": kind, "error": f"{type(exc).__name__}"}
            )

    def _maybe_tick(self, inst: str, row: Any, now: datetime) -> None:
        prev = self._last_tick_pub.get(inst)
        if prev is not None and (now - prev).total_seconds() < self.tick_every:
            return
        try:
            t = Ticker.model_validate(row)
        except ValueError:
            return
        self._last_tick_pub[inst] = now
        self._emit(
            TICK,
            {
                "inst_id": inst,
                "last": _dec(t.last),
                "bid_px": _dec(t.bid_px),
                "bid_sz": _dec(t.bid_sz),
                "ask_px": _dec(t.ask_px),
                "ask_sz": _dec(t.ask_sz),
                "open24h": _dec(t.open24h),
                "high24h": _dec(t.high24h),
                "low24h": _dec(t.low24h),
                "vol24h": _dec(t.vol24h),
                "vol_ccy24h": _dec(t.vol_ccy24h),
                "ts": t.ts.isoformat() if t.ts else None,
                "received_at": now.isoformat(),
            },
        )

    def _closed_ms(self, inst: str, bar: str) -> int | None:
        key = (inst, bar)
        if key not in self._closed:
            stored = self.redis.hget(md.md_key(self.env, inst), f"{md.CLOSED_PREFIX}{bar}")
            if stored:
                self._closed[key] = int(stored)
        return self._closed.get(key)

    async def _candles(self, inst: str, bar: str, data: Sequence[Any]) -> None:
        for raw in data:
            try:
                candle = Candle.model_validate(raw)
            except ValueError:
                logger.warning("candle push did not parse", extra={"inst_id": inst, "bar": bar})
                continue
            if not candle.confirm:
                continue
            ts_ms = _ms(candle.ts)
            prev = self._closed_ms(inst, bar)
            if prev is not None and ts_ms <= prev:
                continue
            rows = candle_rows(self.env, inst, bar, [candle])
            if not rows:
                continue
            await asyncio.to_thread(self._upsert, rows)
            self._closed[(inst, bar)] = ts_ms
            self.redis.hset(md.md_key(self.env, inst), f"{md.CLOSED_PREFIX}{bar}", str(ts_ms))
            self._emit(
                CANDLE_CLOSED,
                {
                    "inst_id": inst,
                    "bar": bar,
                    "ts": candle.ts.isoformat(),
                    "o": _dec(candle.o),
                    "h": _dec(candle.h),
                    "l": _dec(candle.l),
                    "c": _dec(candle.c),
                    "vol": _dec(candle.vol),
                    "vol_ccy": _dec(candle.vol_ccy),
                    "vol_ccy_quote": _dec(candle.vol_ccy_quote),
                },
            )

    def _upsert(self, rows: Sequence[dict[str, Any]]) -> int:
        with self.engine.begin() as conn:
            return upsert_rows(conn, CandleRow, rows)

    # -- staleness ---------------------------------------------------------------------------------

    def is_stale(self, inst: str, now: datetime | None = None) -> bool:
        """No message for more than ``market_data_max_age_seconds`` (or none since subscribing)."""
        at = now or self.clock.now()
        last = self._last_msg.get(inst) or self._since.get(inst)
        return last is None or at - last > timedelta(seconds=self.max_age)

    def stale_instruments(self, now: datetime | None = None) -> set[str]:
        return {i for i in self.instruments if self.is_stale(i, now)}

    async def check_stale(self) -> set[str]:
        now = self.clock.now()
        stale = self.stale_instruments(now)
        for inst in sorted(stale - self._stale):
            last = self._last_msg.get(inst)
            self._emit(
                STALE,
                {
                    "inst_id": inst,
                    "last_msg_at": last.isoformat() if last else None,
                    "max_age_seconds": self.max_age,
                },
            )
        self._stale = (self._stale & self.instruments) | stale
        return stale


def default_feed(env: str, engine: Engine) -> MarketFeed:
    return MarketFeed(env, engine, bus.make_client())


__all__ = [
    "CANDLE_CLOSED",
    "FRESH",
    "STALE",
    "STREAM",
    "TICK",
    "Link",
    "MarketFeed",
    "backoff_seconds",
    "default_feed",
    "ws_cfg",
    "ws_urls",
]
