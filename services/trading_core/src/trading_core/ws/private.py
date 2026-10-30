"""OKX private WebSocket for the active env (ATL-142, brief §4.2 private channels, §4.4).

One socket to ``okx.environments.<env>.ws_private_url``. On every connect it logs in with the env's
verified connection (decrypted with APP_KEY, decision #235; the key, secret, passphrase and signature
are never logged or published), then subscribes ``private_ws.channels``: account, positions,
balance_and_position, orders, orders-algo and liquidation-warning. Ping, jittered reconnect and the
30 s staleness rule are the public feed's (:mod:`trading_core.ws.feed`).

Each push:

* ``orders`` — upsert ``orders`` by clOrdId, then (env, ordId); an order Atlas does not know is
  inserted with origin ``external``. A push with ``fillSz > 0`` inserts one ``fills`` row, deduped on
  (env, instId, tradeId). Publishes ``account.order`` (and ``account.fill`` for a new fill) on
  ``atlas.account``, and ``order.state_changed`` on ``atlas.orders`` when the state moved.
* ``orders-algo`` — upsert ``algo_orders`` by algoClOrdId, then (env, algoId); unknown ones are
  external. Publishes ``account.algo_order`` and, on a state move, ``algo_order.state_changed``.
* ``positions`` / ``account`` — kept in memory and written as one full snapshot tick
  (``positions_snapshots`` + ``balances_snapshots`` + ``equity_curve`` + ``sync_status`` stream
  ``account`` last_success_at), the same shape the REST account sync writes, at most once per
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
import base64
import contextlib
import hmac
import inspect
import json
import logging
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

from atlas_shared import bus
from atlas_shared import market_data as md
from atlas_shared.db import (
    AlgoOrder,
    BalanceSnapshot,
    EquityPoint,
    Fill,
    Order,
    PositionSnapshot,
    SyncStatus,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert
from websockets.asyncio.client import connect as ws_connect

from trading_core.okx.client import OkxCredentials
from trading_core.okx.models import AccountBalance, Position
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail
from trading_core.ws.clock import Clock, RealClock
from trading_core.ws.feed import Connector, backoff_seconds, ws_cfg

logger = logging.getLogger("trading_core.ws.private")

# websockets logs every frame at DEBUG, and the login frame carries the API key, passphrase and
# signature. The private socket gets its own protocol logger that never goes below INFO, whatever
# level the process (or a test) sets.
_frames_logger = logging.getLogger("trading_core.ws.private.protocol")


class _NoDebug(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.INFO


_frames_logger.addFilter(_NoDebug())
_frames_logger.setLevel(logging.INFO)


def private_connector(url: str) -> AbstractAsyncContextManager[Any]:
    # OKX's keepalive is the text "ping"/"pong" pair, so the protocol-level ping is off.
    return ws_connect(
        url, ping_interval=None, open_timeout=10, close_timeout=5, max_size=2**22, logger=_frames_logger
    )


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

VERIFY_PATH = "/users/self/verify"
SYNC_STREAM = "account"
TRADING = "trading"
FUNDING = "funding"
ZERO = Decimal(0)

# orders.state CHECK: OKX's order states, with mmp_canceled folded into canceled.
ORDER_STATES = {
    "live": "live",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "canceled",
    "mmp_canceled": "canceled",
}
ALGO_STATES = {
    "live",
    "pause",
    "effective",
    "partially_effective",
    "canceled",
    "order_failed",
    "partially_failed",
}

Publisher = Callable[[str, EventEnvelope], object]
CredentialSource = Callable[[], "OkxCredentials | None"]
ReconnectCallback = Callable[[dict[str, Any]], "Awaitable[None] | None"]


def private_cfg() -> dict[str, Any]:
    value: dict[str, Any] = DEFAULTS["private_ws"]
    return value


def private_url(env: str) -> str:
    envs: Mapping[str, Any] = DEFAULTS["okx"]["environments"]
    if env not in envs:
        raise ValueError(f"unknown OKX environment {env!r}")
    return str(envs[env]["ws_private_url"])


def login_args(creds: OkxCredentials, epoch_seconds: int) -> dict[str, str]:
    """OKX WS login: sign = Base64(HMAC-SHA256(secret, timestamp + "GET" + "/users/self/verify")),
    timestamp in Unix epoch seconds."""
    ts = str(epoch_seconds)
    digest = hmac.new(creds.secret.encode(), f"{ts}GET{VERIFY_PATH}".encode(), sha256).digest()
    return {
        "apiKey": creds.api_key,
        "passphrase": creds.passphrase,
        "timestamp": ts,
        "sign": base64.b64encode(digest).decode("ascii"),
    }


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def _blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dec(value: Any) -> Decimal | None:
    text = _blank(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except ArithmeticError:
        return None


def _pos_dec(value: Decimal | None) -> Decimal | None:
    """OKX sends ``-1`` for "market on trigger" and ``0``/``""`` for "whole position"."""
    return value if value is not None and value > 0 else None


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat().replace("+00:00", "Z")


def _bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


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
                await asyncio.to_thread(self._order, row, now)
        elif channel == "orders-algo":
            for row in rows:
                await asyncio.to_thread(self._algo, row, now)
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
                {"positions": [self._position_payload(Position.model_validate(r)) for r in rows]},
            )

    # -- orders ------------------------------------------------------------------------------------

    def _order(self, row: Mapping[str, Any], now: datetime) -> None:
        detail = OrderDetail.model_validate(row)
        state = ORDER_STATES.get(detail.state)
        cl = detail.cl_ord_id
        fill_sz = _dec(row.get("fillSz"))
        trade_id = _blank(row.get("tradeId"))
        new_fill: dict[str, Any] | None = None
        with self.engine.begin() as conn:
            existing = None
            if cl:
                existing = (
                    conn.execute(select(Order).where(Order.cl_ord_id == cl).with_for_update())
                    .mappings()
                    .first()
                )
            if existing is None:
                existing = (
                    conn.execute(
                        select(Order)
                        .where(Order.env == self.env, Order.ord_id == detail.ord_id)
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
            values: dict[str, Any] = {
                "ord_id": detail.ord_id,
                "avg_px": _pos_dec(detail.avg_px),
                "fee": detail.fee,
                "fee_ccy": detail.fee_ccy,
                "raw": dict(row),
                "updated_at": now,
            }
            if detail.lever is not None and detail.lever >= 1:
                values["lever"] = int(detail.lever)
            if detail.sz is not None and detail.sz > 0:
                values["sz"] = detail.sz
            if detail.acc_fill_sz is not None:
                values["filled_sz"] = detail.acc_fill_sz
            if state is not None:
                values["state"] = state
            if existing is None:
                if detail.sz is None or detail.sz <= 0:
                    logger.warning("order push without a size skipped", extra={"ord_id": detail.ord_id})
                    return
                values.update(
                    env=self.env,
                    cl_ord_id=cl,
                    inst_id=detail.inst_id,
                    side=detail.side,
                    pos_side=detail.pos_side,
                    ord_type=detail.ord_type,
                    td_mode=detail.td_mode or "cash",
                    px=_pos_dec(detail.px),
                    reduce_only=_bool(detail.reduce_only),
                    origin="external",
                    purpose="entry",
                    created_at=now,
                )
                values.setdefault("state", "live")
                order_id = conn.execute(insert(Order).values(values).returning(Order.id)).scalar_one()
                prev_state: str | None = None
                origin = "external"
            else:
                order_id = existing["id"]
                prev_state = existing["state"]
                origin = existing["origin"]
                conn.execute(update(Order).where(Order.id == order_id).values(values))
            cur_state = values.get("state", prev_state)
            filled = values.get("filled_sz", existing["filled_sz"] if existing is not None else ZERO)

            if fill_sz is not None and fill_sz > 0 and trade_id is not None:
                fill_ts = row.get("fillTime") or row.get("uTime")
                fill_values = {
                    "env": self.env,
                    "order_id": order_id,
                    "trade_id": trade_id,
                    "ord_id": detail.ord_id,
                    "cl_ord_id": cl,
                    "inst_id": detail.inst_id,
                    "side": detail.side,
                    "pos_side": detail.pos_side,
                    "fill_px": _dec(row.get("fillPx")) or ZERO,
                    "fill_sz": fill_sz,
                    "fee": _dec(row.get("fillFee")),
                    "fee_ccy": _blank(row.get("fillFeeCcy")),
                    "exec_type": _blank(row.get("execType")),
                    "fill_pnl": _dec(row.get("fillPnl")),
                    "ts": md.from_ms(str(fill_ts)) if _blank(fill_ts) else now,
                    "raw": dict(row),
                    "created_at": now,
                    "updated_at": now,
                }
                inserted = conn.execute(
                    insert(Fill)
                    .values(fill_values)
                    .on_conflict_do_nothing(index_elements=[Fill.env, Fill.inst_id, Fill.trade_id])
                    .returning(Fill.id)
                ).scalar()
                if inserted is not None:
                    new_fill = {**fill_values, "id": inserted}

        order_payload = {
            "order_id": str(order_id),
            "cl_ord_id": cl,
            "ord_id": detail.ord_id,
            "inst_id": detail.inst_id,
            "side": detail.side,
            "pos_side": detail.pos_side,
            "ord_type": detail.ord_type,
            "state": cur_state,
            "okx_state": detail.state,
            "px": _s(_pos_dec(detail.px)),
            "sz": _s(detail.sz),
            "filled_sz": _s(filled),
            "avg_px": _s(_pos_dec(detail.avg_px)),
            "fee": _s(detail.fee),
            "fee_ccy": detail.fee_ccy,
            "origin": origin,
            "u_time": _iso(detail.u_time),
        }
        self._emit(ACCOUNT_STREAM, ORDER, order_payload)
        if new_fill is not None:
            self._emit(
                ACCOUNT_STREAM,
                FILL,
                {
                    "fill_id": str(new_fill["id"]),
                    "order_id": str(order_id),
                    "trade_id": new_fill["trade_id"],
                    "ord_id": detail.ord_id,
                    "cl_ord_id": cl,
                    "inst_id": detail.inst_id,
                    "side": detail.side,
                    "pos_side": detail.pos_side,
                    "fill_px": _s(new_fill["fill_px"]),
                    "fill_sz": _s(new_fill["fill_sz"]),
                    "fee": _s(new_fill["fee"]),
                    "fee_ccy": new_fill["fee_ccy"],
                    "exec_type": new_fill["exec_type"],
                    "fill_pnl": _s(new_fill["fill_pnl"]),
                    "ts": _iso(new_fill["ts"]),
                },
            )
        if cur_state != prev_state:
            self._emit(
                ORDERS_STREAM,
                ORDER_STATE_CHANGED,
                {**order_payload, "from_state": prev_state, "to_state": cur_state},
            )

    # -- algo orders -------------------------------------------------------------------------------

    @staticmethod
    def _algo_shape(d: AlgoOrderDetail) -> dict[str, Any] | None:
        """kind + trigger/order prices per the algo_orders CHECKs; None for types Atlas does not store."""
        sl, tp = d.sl_trigger_px, d.tp_trigger_px
        if d.ord_type == "oco" or (d.ord_type == "conditional" and sl is not None and tp is not None):
            return {
                "kind": "oco",
                "sl_trigger_px": sl,
                "tp_trigger_px": tp,
                "trigger_px": None,
                "ord_px": _pos_dec(d.sl_ord_px),
            }
        if d.ord_type == "conditional" and sl is not None:
            return {
                "kind": "sl",
                "trigger_px": sl,
                "sl_trigger_px": sl,
                "tp_trigger_px": None,
                "ord_px": _pos_dec(d.sl_ord_px),
            }
        if d.ord_type == "conditional" and tp is not None:
            return {
                "kind": "tp",
                "trigger_px": tp,
                "tp_trigger_px": tp,
                "sl_trigger_px": None,
                "ord_px": _pos_dec(d.tp_ord_px),
            }
        if d.ord_type == "trigger" and d.trigger_px is not None:
            return {
                "kind": "trigger",
                "trigger_px": d.trigger_px,
                "sl_trigger_px": None,
                "tp_trigger_px": None,
                "ord_px": _pos_dec(d.ord_px),
            }
        return None

    def _algo(self, row: Mapping[str, Any], now: datetime) -> None:
        d = AlgoOrderDetail.model_validate(row)
        state = d.state if d.state in ALGO_STATES else None
        shape = self._algo_shape(d)
        prev_state: str | None = None
        algo_row_id: Any = None
        origin: str | None = None
        with self.engine.begin() as conn:
            existing = None
            if d.algo_cl_ord_id:
                existing = (
                    conn.execute(
                        select(AlgoOrder)
                        .where(AlgoOrder.algo_cl_ord_id == d.algo_cl_ord_id)
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
            if existing is None:
                existing = (
                    conn.execute(
                        select(AlgoOrder)
                        .where(AlgoOrder.env == self.env, AlgoOrder.algo_id == d.algo_id)
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
            values: dict[str, Any] = {"algo_id": d.algo_id, "raw": dict(row), "updated_at": now}
            if state is not None:
                values["state"] = state
            if existing is not None:
                algo_row_id, prev_state, origin = existing["id"], existing["state"], existing["origin"]
                conn.execute(update(AlgoOrder).where(AlgoOrder.id == algo_row_id).values(values))
            elif shape is not None:
                values.update(
                    shape,
                    env=self.env,
                    algo_cl_ord_id=d.algo_cl_ord_id,
                    inst_id=d.inst_id,
                    side=d.side,
                    pos_side=d.pos_side,
                    sz=_pos_dec(d.sz),
                    reduce_only=_bool(d.reduce_only) if d.reduce_only is not None else True,
                    origin="external",
                    created_at=now,
                )
                values.setdefault("state", "live")
                algo_row_id = conn.execute(
                    insert(AlgoOrder).values(values).returning(AlgoOrder.id)
                ).scalar_one()
                origin = "external"
            else:
                logger.info(
                    "algo order type not stored", extra={"ord_type": d.ord_type, "algo_id": d.algo_id}
                )
        cur_state = values.get("state", prev_state)
        payload = {
            "algo_order_id": _s(algo_row_id),
            "algo_id": d.algo_id,
            "algo_cl_ord_id": d.algo_cl_ord_id,
            "inst_id": d.inst_id,
            "ord_type": d.ord_type,
            "kind": shape["kind"] if shape else None,
            "side": d.side,
            "pos_side": d.pos_side,
            "state": cur_state,
            "okx_state": d.state,
            "sl_trigger_px": _s(d.sl_trigger_px),
            "tp_trigger_px": _s(d.tp_trigger_px),
            "trigger_px": _s(d.trigger_px),
            "sz": _s(_pos_dec(d.sz)),
            "ord_id": d.ord_id,
            "origin": origin,
            "u_time": _iso(d.u_time),
        }
        self._emit(ACCOUNT_STREAM, ALGO_ORDER, payload)
        if algo_row_id is not None and cur_state != prev_state:
            self._emit(
                ORDERS_STREAM,
                ALGO_STATE_CHANGED,
                {**payload, "from_state": prev_state, "to_state": cur_state},
            )

    # -- positions and balances --------------------------------------------------------------------

    @staticmethod
    def _position_payload(p: Position) -> dict[str, Any]:
        return {
            "inst_id": p.inst_id,
            "inst_type": p.inst_type,
            "pos_side": p.pos_side,
            "mgn_mode": p.mgn_mode,
            "pos": _s(p.pos),
            "avg_px": _s(p.avg_px),
            "mark_px": _s(p.mark_px),
            "liq_px": _s(p.liq_px),
            "lever": _s(p.lever),
            "upl": _s(p.upl),
            "upl_ratio": _s(p.upl_ratio),
            "margin": _s(p.margin),
            "mgn_ratio": _s(p.mgn_ratio),
            "u_time": _iso(p.u_time),
        }

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
        self._emit(
            ACCOUNT_STREAM,
            POSITION,
            {"channel": "positions", "positions": [self._position_payload(p) for p in parsed]},
        )

    def _on_account(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        bal = AccountBalance.model_validate(rows[-1])
        self._balance = bal
        self._dirty = True
        self._emit(
            ACCOUNT_STREAM,
            BALANCE,
            {
                "channel": "account",
                "total_eq": _s(bal.total_eq),
                "upl": _s(bal.upl),
                "u_time": _iso(bal.u_time),
                "balances": [
                    {
                        "ccy": d.ccy,
                        "eq": _s(d.eq),
                        "cash_bal": _s(d.cash_bal),
                        "avail_bal": _s(d.avail_bal),
                        "frozen_bal": _s(d.frozen_bal),
                        "eq_usd": _s(d.eq_usd),
                        "upl": _s(d.upl),
                    }
                    for d in bal.details
                ],
            },
        )

    def _on_balance_and_position(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            event_type = _blank(row.get("eventType"))
            bal = [
                {"ccy": _blank(b.get("ccy")), "cash_bal": _s(_dec(b.get("cashBal")))}
                for b in row.get("balData") or []
                if isinstance(b, dict)
            ]
            pos = [
                {
                    "inst_id": _blank(p.get("instId")),
                    "inst_type": _blank(p.get("instType")),
                    "pos_side": _blank(p.get("posSide")),
                    "mgn_mode": _blank(p.get("mgnMode")),
                    "pos": _s(_dec(p.get("pos"))),
                    "avg_px": _s(_dec(p.get("avgPx"))),
                }
                for p in row.get("posData") or []
                if isinstance(p, dict)
            ]
            common = {"channel": "balance_and_position", "event_type": event_type}
            self._emit(ACCOUNT_STREAM, BALANCE, {**common, "balances": bal})
            self._emit(ACCOUNT_STREAM, POSITION, {**common, "positions": pos})

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
        await asyncio.to_thread(self._write_snapshot, balance, positions, now)
        return True

    def _write_snapshot(self, balance: AccountBalance, positions: Sequence[Position], now: datetime) -> None:
        """One tick in the REST sync's shape, so the Portfolio API reads it the same way."""
        stamp = {"env": self.env, "taken_at": now, "created_at": now, "updated_at": now}
        trading = [
            {
                **stamp,
                "ccy": d.ccy,
                "account": TRADING,
                "eq": d.eq if d.eq is not None else ZERO,
                "avail_bal": d.avail_bal,
                "frozen_bal": d.frozen_bal,
                "eq_usd": d.eq_usd,
                "upl": d.upl,
            }
            for d in balance.details
        ]
        pos_rows = [
            {
                **stamp,
                "inst_id": p.inst_id,
                "pos_side": p.pos_side or "net",
                "pos": p.pos if p.pos is not None else ZERO,
                "avg_px": p.avg_px,
                "mark_px": p.mark_px,
                "liq_px": p.liq_px,
                "lever": p.lever,
                "upl": p.upl,
                "upl_ratio": p.upl_ratio,
                "margin": p.margin,
                "mgn_mode": p.mgn_mode,
                "c_time": p.c_time,
            }
            for p in positions
        ]
        with self.engine.begin() as conn:
            funding_at = conn.execute(
                select(BalanceSnapshot.taken_at)
                .where(BalanceSnapshot.env == self.env, BalanceSnapshot.account == FUNDING)
                .order_by(BalanceSnapshot.taken_at.desc())
                .limit(1)
            ).scalar()
            funding: list[dict[str, Any]] = []
            if funding_at is not None:
                for f in conn.execute(
                    select(BalanceSnapshot).where(
                        BalanceSnapshot.env == self.env,
                        BalanceSnapshot.account == FUNDING,
                        BalanceSnapshot.taken_at == funding_at,
                    )
                ).scalars():
                    funding.append(
                        {
                            **stamp,
                            "ccy": f.ccy,
                            "account": FUNDING,
                            "eq": f.eq,
                            "avail_bal": f.avail_bal,
                            "frozen_bal": f.frozen_bal,
                            "eq_usd": f.eq_usd,
                            "upl": None,
                        }
                    )
            rows = trading + funding
            if rows:
                conn.execute(insert(BalanceSnapshot).values(rows))
            if pos_rows:
                conn.execute(insert(PositionSnapshot).values(pos_rows))
            funding_total = sum((r["eq_usd"] for r in funding if r["eq_usd"] is not None), ZERO)
            total = (balance.total_eq if balance.total_eq is not None else ZERO) + funding_total
            upl = balance.upl
            if upl is None:
                upl = sum((p.upl or ZERO for p in positions), ZERO)
            ts = now.replace(second=0, microsecond=0)
            conn.execute(
                insert(EquityPoint)
                .values(env=self.env, ts=ts, total_eq_usd=total, upl_usd=upl, created_at=now, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[EquityPoint.env, EquityPoint.ts],
                    set_={"total_eq_usd": total, "upl_usd": upl, "updated_at": now},
                )
            )
            # last_success_at only: "reconciled" stays the REST sync's claim (brief §13 indicator).
            conn.execute(
                insert(SyncStatus)
                .values(env=self.env, stream=SYNC_STREAM, last_success_at=now, created_at=now, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[SyncStatus.env, SyncStatus.stream],
                    set_={"last_success_at": now, "last_error": None, "updated_at": now},
                )
            )

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


def connection_credentials(env: str, engine: Engine, app_key: str) -> CredentialSource:
    """Read the env's verified connection on every (re)connect, so a new key is picked up and a cleared
    one stops the feed."""
    from trading_core.okx.connection import credentials_from_ciphertexts
    from trading_core.sync.account import verified_connection

    def load() -> OkxCredentials | None:
        if not app_key:
            return None
        row = verified_connection(engine, env)
        if row is None:
            return None
        return credentials_from_ciphertexts(row.api_key, row.api_secret, row.passphrase, app_key)

    return load


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
