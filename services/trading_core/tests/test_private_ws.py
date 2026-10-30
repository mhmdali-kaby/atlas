"""ATL-142: the private OKX WebSocket, against a fake WebSocket server (never OKX).

Same harness as test_market_ws: Redis on REDIS_TEST_DB, this checkout's test DB (migrated by
`cd backend && php artisan test`), and a simulated clock for pings, staleness and backoff.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

import pytest
import redis
from atlas_shared import bus, testing
from atlas_shared import market_data as md
from atlas_shared.db import (
    AlgoOrder,
    BalanceSnapshot,
    EquityPoint,
    Fill,
    Order,
    PositionSnapshot,
    SyncStatus,
    make_engine,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import OperationalError
from trading_core.okx.client import OkxCredentials
from trading_core.ws.private import PrivateFeed, login_args, private_url
from websockets.asyncio.server import ServerConnection, serve

ENV = "demo"
T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
TEST_DB = testing.db_settings()
STREAMS = ("atlas.account", "atlas.orders", "atlas.system")

# Fake credentials, distinctive so a leak into any log record is easy to spot.
CREDS = OkxCredentials(
    api_key="pk-test-0001-keyvalue",
    secret="S3CRET-never-log-me-9f8e7d",  # noqa: S106 - a fake test value
    passphrase="Pa55-phrase-xyz",  # noqa: S106 - a fake test value
)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._waiters: list[tuple[datetime, asyncio.Future[None]]] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self._now + timedelta(seconds=seconds), fut))
        await fut

    async def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        due = [(d, f) for d, f in self._waiters if d <= self._now]
        self._waiters = [(d, f) for d, f in self._waiters if d > self._now]
        for _, fut in due:
            if not fut.done():
                fut.set_result(None)
        await _settle()


async def _settle(rounds: int = 20) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0.005)


async def _until(cond: Any, within: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + within
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


class FakePrivateOkx:
    """Answers login (success unless told otherwise) and records every frame."""

    def __init__(self) -> None:
        self.received: list[tuple[int, str]] = []
        self.conns: list[ServerConnection] = []
        self.connections = 0
        self.url = ""
        self.login_code = "0"

    async def handler(self, ws: ServerConnection) -> None:
        self.connections += 1
        n = self.connections
        self.conns.append(ws)
        try:
            async for msg in ws:
                text = msg if isinstance(msg, str) else msg.decode()
                self.received.append((n, text))
                if text == "ping":
                    await ws.send("pong")
                    continue
                frame = json.loads(text)
                if frame.get("op") == "login":
                    await ws.send(
                        json.dumps({"event": "login", "code": self.login_code, "msg": "", "connId": "c1"})
                    )
                elif frame.get("op") == "subscribe":
                    for arg in frame["args"]:
                        await ws.send(json.dumps({"event": "subscribe", "arg": arg, "connId": "c1"}))
        finally:
            self.conns.remove(ws)

    async def push(self, channel: str, data: list[dict[str, Any]], **arg: str) -> None:
        payload = {"arg": {"channel": channel, "uid": "77001234", **arg}, "data": data}
        for ws in list(self.conns):
            await ws.send(json.dumps(payload))

    async def drop(self) -> None:
        for ws in list(self.conns):
            await ws.close()

    def frames(self, conn: int, op: str) -> list[dict[str, Any]]:
        out = []
        for n, text in self.received:
            if n == conn and text != "ping":
                frame = json.loads(text)
                if frame.get("op") == op:
                    out.append(frame)
        return out


@pytest.fixture
async def okx() -> AsyncIterator[FakePrivateOkx]:
    fake = FakePrivateOkx()
    # The fake server's own frame log is test plumbing, not the product: keep it out of caplog.
    quiet = logging.getLogger("tests.fake_private_okx")
    quiet.propagate = False
    quiet.disabled = True
    server = await serve(fake.handler, "127.0.0.1", 0, logger=quiet)
    fake.url = f"ws://127.0.0.1:{next(iter(server.sockets)).getsockname()[1]}"
    try:
        yield fake
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture
def rds() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete(*STREAMS, md.private_key(ENV), md.private_key("live"))
    yield conn
    conn.delete(*STREAMS, md.private_key(ENV), md.private_key("live"))
    conn.close()


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    return eng


def _inst() -> str:
    return f"PW{uuid.uuid4().hex[:8].upper()}-USDT-SWAP"


def _events(rds: redis.Redis, stream: str, kind: str | None = None) -> list[EventEnvelope]:
    out = []
    for _, fields in rds.xrange(stream):
        env = EventEnvelope.model_validate_json(fields[bus.FIELD])
        if kind is None or env.type == kind:
            out.append(env)
    return out


def _feed(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx, clock: FakeClock, env: str = ENV
) -> PrivateFeed:
    return PrivateFeed(
        env, engine, rds, credentials=lambda: CREDS, clock=clock, url=okx.url, rand=lambda: 0.0
    )


async def _started(feed: PrivateFeed, okx: FakePrivateOkx, conn: int = 1) -> None:
    await feed.start()
    await _until(lambda: feed.logged_in and len(okx.frames(conn, "subscribe")) == 1)


def _order_push(
    inst: str,
    *,
    cl: str | None,
    ord_id: str,
    state: str,
    sz: str = "1",
    acc: str = "0",
    fill_sz: str = "0",
    trade_id: str = "",
    fill_px: str = "",
) -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst,
        "ordId": ord_id,
        "clOrdId": cl or "",
        "tag": "",
        "px": "100.5",
        "sz": sz,
        "ordType": "limit",
        "side": "buy",
        "posSide": "net",
        "tdMode": "isolated",
        "accFillSz": acc,
        "fillPx": fill_px,
        "tradeId": trade_id,
        "fillSz": fill_sz,
        "fillTime": str(md.to_ms(T0)) if trade_id else "",
        "fillFee": "-0.005" if trade_id else "0",
        "fillFeeCcy": "USDT" if trade_id else "",
        "fillPnl": "0",
        "execType": "M" if trade_id else "",
        "avgPx": fill_px or "0",
        "state": state,
        "lever": "3",
        "fee": "-0.005" if trade_id else "0",
        "feeCcy": "USDT",
        "reduceOnly": "false",
        "cTime": str(md.to_ms(T0)),
        "uTime": str(md.to_ms(T0)),
    }


def _cleanup(engine: Engine, inst: str) -> None:
    with engine.begin() as conn:
        conn.execute(delete(Fill).where(Fill.inst_id == inst))
        conn.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == inst))
        conn.execute(delete(Order).where(Order.inst_id == inst))


# -- AC1: the login signature, and the secret never reaches a log record -----------------------------


async def test_login_carries_a_valid_okx_signature_and_secrets_never_reach_the_logs(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(T0)
    feed = _feed(engine, rds, okx, clock)
    with caplog.at_level(logging.DEBUG):
        try:
            await _started(feed, okx)
            await okx.push("orders", [{"broken": "row"}], instType="ANY")  # a failing push logs too
            await _settle()
            okx.login_code = "60009"
            await okx.drop()
            await clock.advance(float(DEFAULTS["market_ws"]["reconnect_base_seconds"]))
            await _until(lambda: okx.connections == 2 and len(okx.frames(2, "login")) == 1)
            await _settle()
        finally:
            await feed.stop()

    login = okx.frames(1, "login")
    assert len(login) == 1
    args = login[0]["args"][0]
    ts = str(int(T0.timestamp()))
    expected = base64.b64encode(
        hmac.new(CREDS.secret.encode(), f"{ts}GET/users/self/verify".encode(), sha256).digest()
    ).decode()
    assert args == {
        "apiKey": CREDS.api_key,
        "passphrase": CREDS.passphrase,
        "timestamp": ts,
        "sign": expected,
    }
    assert login_args(CREDS, int(T0.timestamp()))["sign"] == expected
    # Subscribe only after the login answer, with exactly the configured channels.
    subs = okx.frames(1, "subscribe")
    assert subs[0]["args"] == DEFAULTS["private_ws"]["channels"]
    assert {a["channel"] for a in subs[0]["args"]} >= {
        "orders",
        "orders-algo",
        "positions",
        "balance_and_position",
        "liquidation-warning",
    }
    # A refused login does not subscribe.
    assert okx.frames(2, "subscribe") == []

    assert caplog.records, "the feed logged nothing; the check below would be vacuous"
    for record in caplog.records:
        dumped = " ".join(_text(v) for v in record.__dict__.values()) + record.getMessage()
        for secret in (CREDS.secret, CREDS.passphrase, CREDS.api_key, expected):
            assert secret not in dumped, f"{record.getMessage()!r} leaked a credential"


def _text(value: Any) -> str:
    try:
        return str(value)
    except Exception:  # a dead weakref proxy in a websockets record
        return ""


def test_private_url_comes_from_config() -> None:
    assert private_url("demo") == DEFAULTS["okx"]["environments"]["demo"]["ws_private_url"]
    assert private_url("demo") == "wss://wspap.okx.com:8443/ws/v5/private"


# -- AC2: partial fill updates filled_sz and inserts one fill, idempotently ---------------------------


async def test_partial_fill_updates_order_and_inserts_one_fill_even_when_replayed(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx
) -> None:
    inst = _inst()
    cl = f"atl{uuid.uuid4().hex[:20]}"
    with engine.begin() as conn:
        order_id = conn.execute(
            insert(Order)
            .values(
                env=ENV,
                cl_ord_id=cl,
                inst_id=inst,
                side="buy",
                pos_side="net",
                ord_type="limit",
                td_mode="isolated",
                px=Decimal("100.5"),
                sz=Decimal("1"),
                state="live",
                origin="atlas",
                purpose="protect",
                created_at=T0,
                updated_at=T0,
            )
            .returning(Order.id)
        ).scalar_one()
    clock = FakeClock(T0)
    feed = _feed(engine, rds, okx, clock)
    push = _order_push(
        inst,
        cl=cl,
        ord_id="900001",
        state="partially_filled",
        acc="0.1",
        fill_sz="0.1",
        trade_id="T-1",
        fill_px="100.4",
    )
    try:
        await _started(feed, okx)
        await okx.push("orders", [push], instType="ANY")
        await _until(lambda: len(_events(rds, "atlas.account", "account.fill")) == 1)
        await okx.push("orders", [push], instType="ANY")
        await _until(lambda: len(_events(rds, "atlas.account", "account.order")) == 2)
        await _settle()

        with engine.connect() as conn:
            order = conn.execute(select(Order).where(Order.id == order_id)).mappings().one()
            fills = conn.execute(select(Fill).where(Fill.inst_id == inst)).mappings().all()
        assert order["filled_sz"] == Decimal("0.1")
        assert order["state"] == "partially_filled" and order["ord_id"] == "900001"
        assert order["origin"] == "atlas"
        assert len(fills) == 1
        fill = fills[0]
        assert fill["order_id"] == order_id and fill["trade_id"] == "T-1"
        assert fill["fill_sz"] == Decimal("0.1") and fill["fill_px"] == Decimal("100.4")
        assert fill["fee"] == Decimal("-0.005") and fill["exec_type"] == "M" and fill["ts"] == T0
        assert len(_events(rds, "atlas.account", "account.fill")) == 1
        # One state change (live -> partially_filled), not two.
        changes = _events(rds, "atlas.orders", "order.state_changed")
        assert [(c.payload["from_state"], c.payload["to_state"]) for c in changes] == [
            ("live", "partially_filled")
        ]
    finally:
        await feed.stop()
        _cleanup(engine, inst)


# -- AC3: an unknown order is imported as external ----------------------------------------------------


async def test_unknown_order_is_inserted_with_origin_external(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx
) -> None:
    inst = _inst()
    cl = f"ext{uuid.uuid4().hex[:20]}"
    clock = FakeClock(T0)
    feed = _feed(engine, rds, okx, clock)
    try:
        await _started(feed, okx)
        await okx.push(
            "orders", [_order_push(inst, cl=cl, ord_id="900002", state="live", sz="2")], instType="ANY"
        )
        await _until(lambda: len(_events(rds, "atlas.account", "account.order")) == 1)
        with engine.connect() as conn:
            rows = conn.execute(select(Order).where(Order.inst_id == inst)).mappings().all()
        assert len(rows) == 1
        row = rows[0]
        assert row["origin"] == "external" and row["cl_ord_id"] == cl and row["ord_id"] == "900002"
        assert row["state"] == "live" and row["sz"] == Decimal("2") and row["px"] == Decimal("100.5")
        assert row["proposal_id"] is None and row["env"] == ENV
        # An order with no clOrdId (placed on the OKX website) is matched by ordId afterwards.
        no_cl = _order_push(inst, cl=None, ord_id="900003", state="live")
        await okx.push("orders", [no_cl], instType="ANY")
        await okx.push("orders", [{**no_cl, "state": "canceled"}], instType="ANY")
        await _until(lambda: len(_events(rds, "atlas.account", "account.order")) == 3)
        with engine.connect() as conn:
            rows = (
                conn.execute(select(Order).where(Order.inst_id == inst, Order.ord_id == "900003"))
                .mappings()
                .all()
            )
        assert len(rows) == 1 and rows[0]["cl_ord_id"] is None and rows[0]["state"] == "canceled"
    finally:
        await feed.stop()
        _cleanup(engine, inst)


# -- AC4: reconnect -> fresh login, the same subscriptions, one ws.private.reconnected -----------------


async def test_closed_socket_reconnects_logs_in_again_resubscribes_and_emits_one_reconnected(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx
) -> None:
    clock = FakeClock(T0)
    feed = _feed(engine, rds, okx, clock)
    seen: list[dict[str, Any]] = []

    async def on_reconnect(payload: dict[str, Any]) -> None:
        seen.append(payload)

    feed.on_reconnected(on_reconnect)
    try:
        await _started(feed, okx)
        assert _events(rds, "atlas.system", "ws.private.reconnected") == []
        assert md.read_private(rds, ENV).connected

        await okx.drop()
        await _until(lambda: feed.socket.ws is None)
        assert not md.read_private(rds, ENV).connected
        base = float(DEFAULTS["market_ws"]["reconnect_base_seconds"])
        assert feed.socket.backoffs == [base]
        await clock.advance(base)
        await _until(lambda: okx.connections == 2 and len(okx.frames(2, "subscribe")) == 1)
        await _until(lambda: len(seen) == 1)

        assert len(okx.frames(2, "login")) == 1
        assert okx.frames(2, "subscribe")[0]["args"] == okx.frames(1, "subscribe")[0]["args"]
        events = _events(rds, "atlas.system", "ws.private.reconnected")
        assert len(events) == 1
        assert events[0].payload["connects"] == 2 and seen == [events[0].payload]
        assert events[0].source == "service:trading_core.private_ws" and events[0].env == ENV
        assert md.read_private(rds, ENV).connected
    finally:
        await feed.stop()


async def test_private_feed_freshness_goes_stale_and_pings_keep_it_fresh(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx
) -> None:
    clock = FakeClock(T0)
    feed = _feed(engine, rds, okx, clock)
    try:
        await _started(feed, okx)
        snap = md.read_private(rds, ENV)
        assert snap.connected and snap.login_at == T0 and not snap.is_stale(clock.now())
        assert not await feed.check_stale()
        # Pings every 25 s get a pong, so a quiet account stays fresh.
        await clock.advance(25)
        await _until(lambda: md.read_private(rds, ENV).last_msg_at == T0 + timedelta(seconds=25))
        assert not await feed.check_stale()
        # Nothing at all for > 30 s (the server stops answering) -> stale, published once.
        feed._last_msg = clock.now() - timedelta(seconds=31)
        assert await feed.check_stale()
        assert await feed.check_stale()
        assert len(_events(rds, "atlas.system", "ws.private.stale")) == 1
    finally:
        await feed.stop()


# -- AC5: every push type publishes its envelope on atlas.account (and liquidation on atlas.system) -----


def _position(inst: str, pos: str = "2") -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst,
        "mgnMode": "isolated",
        "posId": "p1",
        "posSide": "net",
        "pos": pos,
        "avgPx": "100",
        "markPx": "101",
        "upl": "2",
        "uplRatio": "0.02",
        "lever": "3",
        "liqPx": "70",
        "margin": "66.7",
        "mgnRatio": "12",
        "ccy": "USDT",
        "cTime": str(md.to_ms(T0)),
        "uTime": str(md.to_ms(T0)),
    }


def _account() -> dict[str, Any]:
    return {
        "totalEq": "5000123.5",
        "upl": "2",
        "uTime": str(md.to_ms(T0)),
        "details": [
            {
                "ccy": "USDT",
                "eq": "5000123.5",
                "cashBal": "5000000",
                "availBal": "4999000",
                "frozenBal": "1000",
                "eqUsd": "5000123.5",
                "upl": "2",
            }
        ],
    }


async def test_each_push_type_publishes_the_matching_envelope(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx
) -> None:
    inst = _inst()
    clock = FakeClock(T0)
    feed = _feed(engine, rds, okx, clock, env="live")
    algo_cl = f"alg{uuid.uuid4().hex[:20]}"
    with engine.connect() as conn:
        prior = (
            conn.execute(select(SyncStatus).where(SyncStatus.env == "live", SyncStatus.stream == "account"))
            .mappings()
            .first()
        )
    try:
        await _started(feed, okx)
        await okx.push(
            "orders",
            [
                _order_push(
                    inst,
                    cl=None,
                    ord_id="900010",
                    state="filled",
                    acc="1",
                    fill_sz="1",
                    trade_id="T-9",
                    fill_px="100",
                )
            ],
            instType="ANY",
        )
        await okx.push(
            "orders-algo",
            [
                {
                    "instType": "SWAP",
                    "instId": inst,
                    "algoId": "a-1",
                    "algoClOrdId": algo_cl,
                    "ordType": "conditional",
                    "side": "sell",
                    "posSide": "net",
                    "sz": "1",
                    "state": "live",
                    "slTriggerPx": "95",
                    "slOrdPx": "-1",
                    "tpTriggerPx": "",
                    "tpOrdPx": "",
                    "reduceOnly": "true",
                    "cTime": str(md.to_ms(T0)),
                    "uTime": str(md.to_ms(T0)),
                }
            ],
            instType="ANY",
        )
        await okx.push("positions", [_position(inst)], instType="ANY")
        await okx.push("account", [_account()])
        await okx.push(
            "balance_and_position",
            [
                {
                    "pTime": str(md.to_ms(T0)),
                    "eventType": "filled",
                    "balData": [{"ccy": "USDT", "cashBal": "4999900", "uTime": str(md.to_ms(T0))}],
                    "posData": [
                        {
                            "instId": inst,
                            "instType": "SWAP",
                            "mgnMode": "isolated",
                            "posSide": "net",
                            "pos": "2",
                            "avgPx": "100",
                            "ccy": "USDT",
                            "posId": "p1",
                        }
                    ],
                }
            ],
        )
        await okx.push("liquidation-warning", [_position(inst)], instType="ANY")
        await _until(lambda: len(_events(rds, "atlas.system", "system.liquidation_warning")) == 1)
        await _settle()

        account = _events(rds, "atlas.account")
        kinds = [e.type for e in account]
        assert kinds.count("account.order") == 1 and kinds.count("account.fill") == 1
        assert kinds.count("account.algo_order") == 1
        assert kinds.count("account.position") == 2 and kinds.count("account.balance") == 2
        for e in account + _events(rds, "atlas.orders") + _events(rds, "atlas.system"):
            # Brief §3.2 envelope: id, type, ts, env, source, correlation_id, payload.
            body = e.model_dump(mode="json")
            assert {"id", "type", "ts", "env", "source", "payload"} <= set(body)
            assert e.env == "live" and e.source == "service:trading_core.private_ws"
            assert "77001234" not in json.dumps(body)  # the OKX uid is never forwarded

        by = {e.type: e.payload for e in account}
        assert by["account.order"]["ord_id"] == "900010" and by["account.order"]["origin"] == "external"
        assert by["account.fill"]["trade_id"] == "T-9" and by["account.fill"]["fill_sz"] == "1"
        assert by["account.algo_order"]["kind"] == "sl" and by["account.algo_order"]["state"] == "live"
        positions = [e.payload for e in account if e.type == "account.position"]
        assert positions[0]["channel"] == "positions" and positions[0]["positions"][0]["pos"] == "2"
        assert positions[1]["channel"] == "balance_and_position"
        balances = [e.payload for e in account if e.type == "account.balance"]
        assert balances[0]["total_eq"] == "5000123.5" and balances[1]["balances"][0]["cash_bal"] == "4999900"
        assert [e.type for e in _events(rds, "atlas.orders")] == [
            "order.state_changed",
            "algo_order.state_changed",
        ]
        liq = _events(rds, "atlas.system", "system.liquidation_warning")[0]
        assert liq.payload["positions"][0]["inst_id"] == inst

        # The algo row and the snapshot tick (positions + account) landed in the DB.
        with engine.connect() as conn:
            algo = conn.execute(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == algo_cl)).mappings().one()
            pos = (
                conn.execute(
                    select(PositionSnapshot).where(
                        PositionSnapshot.env == "live", PositionSnapshot.inst_id == inst
                    )
                )
                .mappings()
                .all()
            )
            bal = (
                conn.execute(
                    select(BalanceSnapshot).where(
                        BalanceSnapshot.env == "live",
                        BalanceSnapshot.taken_at == T0,
                        BalanceSnapshot.account == "trading",
                    )
                )
                .mappings()
                .all()
            )
            status = (
                conn.execute(
                    select(SyncStatus).where(SyncStatus.env == "live", SyncStatus.stream == "account")
                )
                .mappings()
                .one()
            )
        assert algo["kind"] == "sl" and algo["trigger_px"] == Decimal("95") and algo["ord_px"] is None
        assert algo["origin"] == "external" and algo["reduce_only"] is True
        assert len(pos) == 1 and pos[0]["pos"] == Decimal("2") and pos[0]["taken_at"] == T0
        assert [(b["ccy"], b["eq"]) for b in bal] == [("USDT", Decimal("5000123.5"))]
        assert status["last_success_at"] == T0
    finally:
        await feed.stop()
        _cleanup(engine, inst)
        with engine.begin() as conn:
            conn.execute(
                delete(PositionSnapshot).where(
                    PositionSnapshot.env == "live", PositionSnapshot.taken_at == T0
                )
            )
            conn.execute(
                delete(BalanceSnapshot).where(BalanceSnapshot.env == "live", BalanceSnapshot.taken_at == T0)
            )
            conn.execute(delete(EquityPoint).where(EquityPoint.env == "live", EquityPoint.ts == T0))
            conn.execute(delete(SyncStatus).where(SyncStatus.env == "live", SyncStatus.stream == "account"))
            if prior is not None:
                conn.execute(insert(SyncStatus).values(dict(prior)))


async def test_no_credentials_keeps_the_socket_closed_and_marks_it_disconnected(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx
) -> None:
    clock = FakeClock(T0)
    feed = PrivateFeed(ENV, engine, rds, credentials=lambda: None, clock=clock, url=okx.url)
    try:
        await feed.start()
        await _until(lambda: rds.hget(md.private_key(ENV), md.CONNECTED) == "0")
        assert okx.connections == 0
        assert md.read_private(rds, ENV).is_stale(clock.now())
    finally:
        await feed.stop()


# -- the snapshot tick is the REST sync's writer (review round 1) -------------------------------------


async def test_snapshot_tick_unrealised_pnl_matches_the_rest_sync_for_a_coin_margined_position(
    engine: Engine, rds: redis.Redis, okx: FakePrivateOkx
) -> None:
    """Futures mode: the account push has no upl, one position is USDT-settled (upl 2 USDT) and one is
    coin-margined (upl 0.01 BTC). Only the USD-settled one counts, exactly as sync.account.upl_usd."""
    from trading_core.okx.models import AccountBalance, Position
    from trading_core.sync.account import upl_usd

    t1 = T0 + timedelta(days=1)
    usdt_inst = _inst()
    coin_inst = f"PW{uuid.uuid4().hex[:6].upper()}-USD-SWAP"
    coin_pos = {**_position(coin_inst, pos="5"), "ccy": "BTC", "upl": "0.01"}
    account = {**_account(), "upl": "", "uTime": str(md.to_ms(t1))}
    with engine.connect() as conn:
        prior = (
            conn.execute(select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == "account"))
            .mappings()
            .first()
        )
    clock = FakeClock(t1)
    feed = _feed(engine, rds, okx, clock)
    try:
        await _started(feed, okx)
        await okx.push("positions", [_position(usdt_inst), coin_pos], instType="ANY")
        await okx.push("account", [account])
        await _until(lambda: len(_events(rds, "atlas.account", "account.balance")) == 1)
        await _settle()
        with engine.connect() as conn:
            point = (
                conn.execute(select(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts == t1))
                .mappings()
                .one()
            )
            status = (
                conn.execute(select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == "account"))
                .mappings()
                .one()
            )
            pos = (
                conn.execute(
                    select(PositionSnapshot).where(
                        PositionSnapshot.env == ENV, PositionSnapshot.taken_at == t1
                    )
                )
                .mappings()
                .all()
            )
        rest = upl_usd(
            AccountBalance.model_validate(account),
            [Position.model_validate(_position(usdt_inst)), Position.model_validate(coin_pos)],
        )
        assert rest == Decimal("2")
        assert point["upl_usd"] == rest  # the coin-margined 0.01 BTC is not added to USD
        assert {p["inst_id"] for p in pos} == {usdt_inst, coin_inst}
        assert status["last_success_at"] == t1
        # The feed never claims "reconciled with OKX": that stays the REST sync's stamp.
        assert status["reconciled_at"] == (prior["reconciled_at"] if prior is not None else None)
    finally:
        await feed.stop()
        with engine.begin() as conn:
            conn.execute(
                delete(PositionSnapshot).where(PositionSnapshot.env == ENV, PositionSnapshot.taken_at == t1)
            )
            conn.execute(
                delete(BalanceSnapshot).where(BalanceSnapshot.env == ENV, BalanceSnapshot.taken_at == t1)
            )
            conn.execute(delete(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts == t1))
            conn.execute(delete(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == "account"))
            if prior is not None:
                conn.execute(insert(SyncStatus).values(dict(prior)))
