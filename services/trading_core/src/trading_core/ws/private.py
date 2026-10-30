"""OKX private WebSocket for the active env (ATL-142, brief §4.2 private channels, §4.4).

One socket to ``okx.environments.<env>.ws_private_url``. On every connect it logs in with the env's
verified connection (decrypted with APP_KEY, decision #235; the key, secret, passphrase and signature
are never logged or published), then subscribes ``private_ws.channels``: account, positions,
balance_and_position, orders, orders-algo and liquidation-warning. Ping, jittered reconnect and the
30 s staleness rule are the public feed's (:mod:`trading_core.ws.feed`).

Order and algo persistence is :mod:`trading_core.execution.order_store`, shared with reconciliation.

Each push:

* ``orders`` — upsert ``orders`` by (env, clOrdId), then (env, ordId); an order Atlas does not know is
  inserted with origin ``external``. A push with ``fillSz > 0`` inserts one ``fills`` row, deduped on
  (env, instId, tradeId). Publishes ``account.order`` (and ``account.fill`` for a new fill) on
  ``atlas.account``, and ``order.state_changed`` on ``atlas.orders`` when the state moved.
* ``orders-algo`` — upsert ``algo_orders`` by (env, algoClOrdId), then (env, algoId); unknown ones are
  external. Publishes ``account.algo_order`` and, on a state move, ``algo_order.state_changed``.
* ``positions`` / ``account`` — kept in memory and written as one full snapshot tick
  (``positions_snapshots`` + ``balances_snapshots`` + ``equity_curve`` + ``sync_status`` stream
  ``account`` last_success_at), written by the REST sync's own ``write_tick``, at most once per
  ``snapshot_min_interval_seconds``. Funding-account rows are carried over from the newest REST tick
  (the private socket does not cover the funding account). Publish ``account.position`` /
  ``account.balance``.
* ``balance_and_position`` — publishes ``account.balance`` and ``account.position``; no DB write
  (its position rows carry no mark price or PnL; the ``positions`` push that follows does).
* ``liquidation-warning`` — ``system.liquidation_warning`` on ``atlas.system``.

Every reconnect that logs in again publishes ``ws.private.reconnected`` on ``atlas.system`` and calls
the in-process callbacks registered with :meth:`PrivateFeed.on_reconnected` (reconciliation).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from atlas_shared import bus
from atlas_shared import market_data as md
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine

from trading_core.execution import order_store
from trading_core.execution.order_store import iso as _iso
from trading_core.okx.client import OkxCredentials
from trading_core.okx.models import AccountBalance, Position
from trading_core.sync import account as account_sync
from trading_core.ws import private_support as support
from trading_core.ws.clock import Clock, RealClock
from trading_core.ws.feed import Connector, backoff_seconds, ws_cfg
from trading_core.ws.private_support import (
    CredentialSource,
    connection_credentials,
    login_args,
    private_cfg,
    private_connector,
    private_url,
)

logger = logging.getLogger("trading_core.ws.private")

SOURCE = "service:trading_core.private_ws"
ACCOUNT_STREAM = "atlas.account"
ORDERS_STREAM = "atlas.orders"
SYSTEM_STREAM = "atlas.system"

ORDER = "account.order"
ALGO_ORDER = "account.algo_order"
FILL = "account.fill"
POSITION = "account.position"
BALANCE = "account.balance"
ORDER_STATE_CHANGED = "order.state_changed"
ALGO_STATE_CHANGED = "algo_order.state_changed"
LIQUIDATION_WARNING = "system.liquidation_warning"
RECONNECTED = "ws.private.reconnected"
STALE = "ws.private.stale"
FRESH = "ws.private.fresh"

Publisher = Callable[[str, EventEnvelope], object]
ReconnectCallback = Callable[[dict[str, Any]], "Awaitable[None] | None"]


@dataclass
class _Socket:
    url: str
    ws: Any = None
    connects: int = 0
    logins: int = 0
    pings: int = 0
    backoffs: list[float] = field(default_factory=list)


class PrivateFeed:
    def __init__(
        self,
        env: str,
        engine: Engine,
        redis: Any,
        *,
        credentials: CredentialSource,
        publish: Publisher | None = None,
        connect: Connector = private_connector,
        clock: Clock | None = None,
        url: str | None = None,
        rand: Callable[[], float] = random.random,
    ) -> None:
        cfg = ws_cfg()
        pcfg = private_cfg()
        self.env = env
        self.engine = engine
        self.redis = redis
        self._credentials = credentials
        self._publish: Publisher = publish or (lambda s, e: bus.publish(s, e, client=redis))
        self._connect = connect
        self.clock: Clock = clock or RealClock()
        self._rand = rand
        self.socket = _Socket(url or private_url(env))
        self.channels: list[dict[str, str]] = [dict(c) for c in pcfg["channels"]]
        self.ping_interval = float(cfg["ping_interval_seconds"])
        self.max_age = float(cfg["market_data_max_age_seconds"])
        self.stale_every = float(cfg["stale_check_seconds"])
        self.ttl = int(cfg["snapshot_ttl_seconds"])
        self.no_connection_retry = float(pcfg["no_connection_retry_seconds"])
        self.snapshot_every = float(pcfg["snapshot_min_interval_seconds"])
        self.logged_in = False
        self._last_msg: datetime | None = None
        self._stale = False
        self._callbacks: list[ReconnectCallback] = []
        self._tasks: list[asyncio.Task[None]] = []
        # Account state rebuilt from pushes on each connection; written as one snapshot tick.
        self._balance: AccountBalance | None = None
        self._positions: dict[tuple[str, str], Position] = {}
        self._positions_ready = False
        self._dirty = False
        self._last_snapshot: datetime | None = None

    # -- lifecycle ---------------------------------------------------------------------------------

    def on_reconnected(self, callback: ReconnectCallback) -> None:
        """Called (sync or async) with the event payload after every re-login on a new socket."""
        self._callbacks.append(callback)

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)

    async def start(self) -> None:
        if self.running:
            return
        self._tasks = [
            asyncio.create_task(self._loop(), name="ws:private"),
            asyncio.create_task(self._stale_loop(), name="ws:private:stale"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # -- connection --------------------------------------------------------------------------------

    async def _loop(self) -> None:
        attempt = 0
        while True:
            creds = await self._load_credentials()
            if creds is None:
                self._write_fresh({md.CONNECTED: "0"})
                await self.clock.sleep(self.no_connection_retry)
                continue
            try:
                async with self._connect(self.socket.url) as ws:
                    self.socket.ws = ws
                    self.socket.connects += 1
                    self.logged_in = False
                    self._reset_state()
                    epoch = int(self.clock.now().timestamp())
                    await ws.send(json.dumps({"op": "login", "args": [login_args(creds, epoch)]}))
                    del creds
                    pinger = asyncio.create_task(self._ping_loop(ws), name="ws:private:ping")
                    try:
                        async for raw in ws:
                            if await self._handle(ws, raw):
                                attempt = 0
                    finally:
                        pinger.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await pinger
                        self.socket.ws = None
                        self.logged_in = False
                        self._write_fresh({md.CONNECTED: "0"})
                logger.warning("private socket closed", extra={"env": self.env})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "private socket failed",
                    extra={"env": self.env, "error": type(exc).__name__},
                )
            delay = backoff_seconds(attempt, self._rand)
            attempt += 1
            self.socket.backoffs.append(delay)
            await self.clock.sleep(delay)

    async def _load_credentials(self) -> OkxCredentials | None:
        try:
            return await asyncio.to_thread(self._credentials)
        except Exception as exc:
            # A DecryptError message never contains the plaintext; the type is enough.
            logger.warning("private socket has no usable credentials", extra={"error": type(exc).__name__})
            return None

    async def _ping_loop(self, ws: Any) -> None:
        while True:
            await self.clock.sleep(self.ping_interval)
            await ws.send("ping")
            self.socket.pings += 1

    def _reset_state(self) -> None:
        self._balance = None
        self._positions = {}
        self._positions_ready = False
        self._dirty = False

    # -- frames ------------------------------------------------------------------------------------

    async def _handle(self, ws: Any, raw: str | bytes) -> bool:
        """Returns True once the socket has logged in. One bad frame never tears the socket down."""
        try:
            return await self.on_frame(ws, raw)
        except Exception as exc:
            logger.warning(
                "private push handling failed", extra={"error": f"{type(exc).__name__}: {exc}"[:300]}
            )
            return self.logged_in

    async def on_frame(self, ws: Any, raw: str | bytes) -> bool:
        text = raw.decode() if isinstance(raw, bytes) else raw
        now = self.clock.now()
        self._touch(now)
        if text == "pong":
            return self.logged_in
        try:
            msg = json.loads(text)
        except ValueError:
            logger.warning("private socket sent non-JSON", extra={"head": text[:80]})
            return self.logged_in
        if not isinstance(msg, dict):
            return self.logged_in
        event = msg.get("event")
        if event == "login":
            if str(msg.get("code", "0")) == "0":
                await self._logged_in(ws, now)
            else:
                logger.warning("private socket login refused", extra={"code": msg.get("code")})
                await ws.close()
            return self.logged_in
        if event == "error":
            logger.warning(
                "private socket error event",
                extra={"code": msg.get("code"), "okx_msg": str(msg.get("msg"))[:200]},
            )
            if not self.logged_in:
                await ws.close()
            return self.logged_in
        if event is not None:
            return self.logged_in
        arg = msg.get("arg")
        data = msg.get("data")
        if not isinstance(arg, dict) or not isinstance(data, list):
            return self.logged_in
        await self.on_push(str(arg.get("channel", "")), data, now)
        return self.logged_in

    async def _logged_in(self, ws: Any, now: datetime) -> None:
        self.logged_in = True
        self.socket.logins += 1
        await ws.send(json.dumps({"op": "subscribe", "args": self.channels}))
        self._write_fresh({md.CONNECTED: "1", md.LOGIN_MS: str(md.to_ms(now))})
        logger.info("private socket logged in", extra={"env": self.env, "connects": self.socket.connects})
        if self.socket.connects > 1:
            payload = {"connects": self.socket.connects, "logged_in_at": _iso(now)}
            self._emit(SYSTEM_STREAM, RECONNECTED, payload)
            for cb in list(self._callbacks):
                try:
                    result = cb(payload)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("ws.private.reconnected callback failed")

    async def on_push(self, channel: str, data: Sequence[Any], now: datetime) -> None:
        self._write_fresh({f"{channel}_ms": str(md.to_ms(now))})
        rows = [r for r in data if isinstance(r, dict)]
        if channel == "orders":
            for row in rows:
                await self._order(row, now)
        elif channel == "orders-algo":
            for row in rows:
                await self._algo(row, now)
        elif channel == "positions":
            self._on_positions(rows)
            await self._maybe_snapshot(now)
        elif channel == "account":
            self._on_account(rows)
            await self._maybe_snapshot(now)
        elif channel == "balance_and_position":
            self._on_balance_and_position(rows)
        elif channel == "liquidation-warning":
            self._emit(
                SYSTEM_STREAM,
                LIQUIDATION_WARNING,
                {"positions": [support.position_payload(Position.model_validate(r)) for r in rows]},
            )

    # -- orders (persistence lives in trading_core.execution.order_store) ---------------------------

    async def _order(self, row: Mapping[str, Any], now: datetime) -> None:
        u = await asyncio.to_thread(order_store.upsert_order, self.engine, self.env, row, now)
        if u is None:
            return
        payload = order_store.order_payload(u)
        self._emit(ACCOUNT_STREAM, ORDER, payload)
        fill = order_store.fill_payload(u)
        if fill is not None:
            self._emit(ACCOUNT_STREAM, FILL, fill)
        if u.state_changed:
            self._emit(
                ORDERS_STREAM,
                ORDER_STATE_CHANGED,
                {**payload, "from_state": u.prev_state, "to_state": u.state},
            )

    async def _algo(self, row: Mapping[str, Any], now: datetime) -> None:
        u = await asyncio.to_thread(order_store.upsert_algo, self.engine, self.env, row, now)
        payload = order_store.algo_payload(u)
        self._emit(ACCOUNT_STREAM, ALGO_ORDER, payload)
        if u.state_changed:
            self._emit(
                ORDERS_STREAM,
                ALGO_STATE_CHANGED,
                {**payload, "from_state": u.prev_state, "to_state": u.state},
            )

    # -- positions and balances --------------------------------------------------------------------

    def _on_positions(self, rows: Sequence[Mapping[str, Any]]) -> None:
        parsed = [Position.model_validate(r) for r in rows]
        if not self._positions_ready:
            # The first push after subscribing is the full snapshot (possibly empty).
            self._positions = {}
            self._positions_ready = True
        for p in parsed:
            key = (p.inst_id, p.pos_side or "net")
            if p.pos is None or p.pos == 0:
                self._positions.pop(key, None)
            else:
                self._positions[key] = p
        self._dirty = True
        positions = [support.position_payload(p) for p in parsed]
        self._emit(ACCOUNT_STREAM, POSITION, {"channel": "positions", "positions": positions})

    def _on_account(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        self._balance = AccountBalance.model_validate(rows[-1])
        self._dirty = True
        self._emit(ACCOUNT_STREAM, BALANCE, support.account_payload(self._balance))

    def _on_balance_and_position(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            balance, position = support.balance_and_position_payloads(row)
            self._emit(ACCOUNT_STREAM, BALANCE, balance)
            self._emit(ACCOUNT_STREAM, POSITION, position)

    async def _maybe_snapshot(self, now: datetime, *, force: bool = False) -> bool:
        if not self._dirty or self._balance is None or not self._positions_ready:
            return False
        if (
            not force
            and self._last_snapshot is not None
            and now - self._last_snapshot < timedelta(seconds=self.snapshot_every)
        ):
            return False
        balance, positions = self._balance, list(self._positions.values())
        self._dirty = False
        self._last_snapshot = now
        # The REST sync's writer; reconciled=False keeps "reconciled with OKX" the REST sync's claim,
        # and funding rows are carried over from the newest REST tick (no funding data on this socket).
        await asyncio.to_thread(
            account_sync.write_tick,
            self.engine,
            self.env,
            now,
            balance,
            positions,
            funding_rows=None,
            reconciled=False,
        )
        return True

    # -- freshness ---------------------------------------------------------------------------------

    def _touch(self, now: datetime) -> None:
        self._last_msg = now
        self._write_fresh({md.LAST_MSG_MS: str(md.to_ms(now))})
        if self._stale and self.logged_in:
            self._stale = False
            self._emit(SYSTEM_STREAM, FRESH, {"last_msg_at": _iso(now)})

    def _write_fresh(self, fields: Mapping[str, str]) -> None:
        key = md.private_key(self.env)
        try:
            pipe = self.redis.pipeline()
            pipe.hset(key, mapping=dict(fields))
            pipe.expire(key, self.ttl)
            pipe.execute()
        except Exception as exc:
            logger.warning("private feed freshness write failed", extra={"error": type(exc).__name__})

    def is_stale(self, now: datetime | None = None) -> bool:
        at = now or self.clock.now()
        if not self.logged_in or self._last_msg is None:
            return True
        return at - self._last_msg > timedelta(seconds=self.max_age)

    async def check_stale(self) -> bool:
        now = self.clock.now()
        stale = self.is_stale(now)
        if stale and not self._stale:
            self._stale = True
            self._emit(
                SYSTEM_STREAM,
                STALE,
                {"last_msg_at": _iso(self._last_msg), "max_age_seconds": self.max_age},
            )
        await self._maybe_snapshot(now)
        return stale

    async def _stale_loop(self) -> None:
        while True:
            await self.clock.sleep(self.stale_every)
            try:
                await self.check_stale()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("private feed stale check failed")

    # -- events ------------------------------------------------------------------------------------

    def _emit(self, stream: str, kind: str, payload: dict[str, Any]) -> None:
        envelope = EventEnvelope(type=kind, env=self.env, ts=self.clock.now(), source=SOURCE, payload=payload)
        try:
            self._publish(stream, envelope)
        except Exception as exc:
            logger.warning("private event publish failed", extra={"type": kind, "error": type(exc).__name__})


def default_private_feed(env: str, engine: Engine, app_key: str) -> PrivateFeed:
    return PrivateFeed(
        env, engine, bus.make_client(), credentials=connection_credentials(env, engine, app_key)
    )


__all__ = [
    "ACCOUNT_STREAM",
    "ALGO_ORDER",
    "ALGO_STATE_CHANGED",
    "BALANCE",
    "FILL",
    "LIQUIDATION_WARNING",
    "ORDER",
    "ORDERS_STREAM",
    "ORDER_STATE_CHANGED",
    "POSITION",
    "RECONNECTED",
    "SYSTEM_STREAM",
    "PrivateFeed",
    "connection_credentials",
    "default_private_feed",
    "login_args",
    "private_connector",
    "private_url",
]
