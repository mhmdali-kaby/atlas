"""ATL-146: reconciliation makes the DB match OKX, protects every open position with a server-side stop,
trips reconciliation_mismatch on an unexplained size, and gates the Executor at startup (rule 8).

OKX is mocked with respx; the database and Redis are the real test instances.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
import redis
import respx
from atlas_shared import bus, testing
from atlas_shared.db import (
    AlgoOrder,
    BalanceSnapshot,
    Candle,
    Connection,
    EquityPoint,
    Fill,
    Instrument,
    Order,
    PositionSnapshot,
    Proposal,
    RiskCheck,
    SyncStatus,
    TradingState,
    make_engine,
)
from atlas_shared.events import EventEnvelope
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app, default_scheduler
from trading_core.config import Settings
from trading_core.execution import recorder as recorder_mod
from trading_core.execution.executor import derived_id, execute
from trading_core.execution.reconcile import (
    JOB,
    ReconcileGate,
    protect_id,
    reconcile,
    reconcile_job,
)
from trading_core.okx import OkxClient, OkxCredentials
from trading_core.okx.models import Position
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

ENV = "demo"
AT = datetime(2032, 7, 14, 10, 0, tzinfo=UTC)
AT_MS = int(AT.timestamp() * 1000)
OPENED_MS = AT_MS - 3_600_000  # the position opened an hour ago
BASE = "https://www.okx.com"
CREDS = OkxCredentials(api_key="test-key-0000", secret="test-only-secret", passphrase="p")  # noqa: S106
SECRET = "atlas-test-internal-secret"  # noqa: S105
STREAMS = ("atlas.orders", "atlas.system", "atlas.warroom", "atlas.account")
TEST_DB = testing.db_settings()


# --- fixtures ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    return eng


@pytest.fixture
def rds() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete(*STREAMS)
    yield conn
    conn.delete(*STREAMS)
    conn.close()


def _set_settings(engine: Engine, data: dict[str, Any]) -> None:
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level,"
                " max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at)"
                " VALUES (:id, :e, 7, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = 7, data = EXCLUDED.data"
            ),
            {"id": uuid.uuid4(), "e": ENV, "d": json.dumps(data)},
        )


@pytest.fixture
def world(engine: Engine) -> Iterator[dict[str, Any]]:
    inst_id = f"RC{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
    with engine.begin() as c:
        old_settings = c.execute(
            text("SELECT version, data FROM settings WHERE env = :e"), {"e": ENV}
        ).first()
        old_conn = c.execute(
            select(
                Connection.id, Connection.last_verified_at, Connection.permissions, Connection.position_mode
            ).where(Connection.exchange == "okx", Connection.env == ENV)
        ).first()
        old_state = c.execute(
            select(TradingState.paused, TradingState.breakers, TradingState.reason).where(
                TradingState.env == ENV
            )
        ).first()
        old_status = [
            dict(r._mapping)
            for r in c.execute(select(SyncStatus.__table__).where(SyncStatus.env == ENV)).all()
        ]
        values = {"last_verified_at": AT, "permissions": ["read_only", "trade"], "position_mode": "net_mode"}
        if old_conn is None:
            c.execute(
                Connection.__table__.insert().values(
                    id=uuid.uuid4(),
                    exchange="okx",
                    env=ENV,
                    label="t",
                    api_key="x",
                    api_secret="x",  # noqa: S106 - never decrypted; the test factory supplies keys
                    passphrase="x",  # noqa: S106
                    created_at=AT,
                    updated_at=AT,
                    **values,
                )
            )
        else:
            c.execute(update(Connection).where(Connection.id == old_conn.id).values(**values))
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        c.execute(
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type="SWAP",
                base_ccy="RC",
                quote_ccy="USDT",
                settle_ccy="USDT",
                ct_val=D("0.001"),
                ct_mult=D(1),
                lot_sz=D(1),
                min_sz=D(1),
                tick_sz=D("0.01"),
                max_lever=D(50),
                state="live",
                asset_class="crypto",
                created_at=AT,
                updated_at=AT,
            )
        )
    _set_settings(engine, {"autonomy_level": 2, "stop_verify_seconds": 3})
    yield {"inst_id": inst_id}
    with engine.begin() as c:
        c.execute(delete(Fill).where(Fill.inst_id == inst_id))
        c.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == inst_id))
        c.execute(delete(Order).where(Order.inst_id == inst_id))
        c.execute(delete(Proposal).where(Proposal.inst_id == inst_id))
        c.execute(delete(Candle).where(Candle.env == ENV, Candle.inst_id == inst_id))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
        c.execute(
            delete(PositionSnapshot).where(PositionSnapshot.env == ENV, PositionSnapshot.taken_at >= AT)
        )
        c.execute(delete(BalanceSnapshot).where(BalanceSnapshot.env == ENV, BalanceSnapshot.taken_at >= AT))
        c.execute(delete(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts >= AT))
        c.execute(delete(SyncStatus).where(SyncStatus.env == ENV))
        for row in old_status:
            c.execute(SyncStatus.__table__.insert().values(**row))
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        if old_state is not None:
            c.execute(
                TradingState.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=ENV,
                    paused=old_state.paused,
                    breakers=old_state.breakers,
                    reason=old_state.reason,
                    created_at=AT,
                    updated_at=AT,
                )
            )
        if old_conn is None:
            c.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))
        else:
            c.execute(
                update(Connection)
                .where(Connection.id == old_conn.id)
                .values(
                    last_verified_at=old_conn.last_verified_at,
                    permissions=old_conn.permissions,
                    position_mode=old_conn.position_mode,
                )
            )
        if old_settings is None:
            c.execute(text("DELETE FROM settings WHERE env = :e"), {"e": ENV})
        else:
            c.execute(
                text("UPDATE settings SET version = :v, data = CAST(:d AS jsonb) WHERE env = :e"),
                {"v": old_settings[0], "d": json.dumps(old_settings[1]), "e": ENV},
            )


def make_check(engine: Engine, inst_id: str, *, stop: str = "3960") -> tuple[uuid.UUID, uuid.UUID]:
    """A buy proposal (entry 4000, stop ``stop``, TP1 4100) and its approved send_now risk check."""
    pid, rid = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            Proposal.__table__.insert().values(
                id=pid,
                env=ENV,
                version=1,
                kind="entry",
                author="strategist",
                inst_id=inst_id,
                side="buy",
                order_type="limit",
                entry=D(4000),
                stop=D(stop),
                expected_r=D("2.5"),
                take_profits=[{"price": 4100, "pct": 50}],
                leverage_suggestion=3,
                size_suggestion_pct=D(5),
                status="risk_checked",
                created_at=AT - timedelta(hours=2),
                updated_at=AT - timedelta(hours=2),
            )
        )
        c.execute(
            RiskCheck.__table__.insert().values(
                id=rid,
                env=ENV,
                proposal_id=pid,
                settings_version=7,
                result="approved",
                checks=[],
                final_size=D(250),
                final_notional_usd=D(1000),
                leverage=3,
                reason="test",
                decision="send_now",
                created_at=AT - timedelta(hours=2),
                updated_at=AT - timedelta(hours=2),
            )
        )
    return pid, rid


def atlas_entry(engine: Engine, inst_id: str, pid: uuid.UUID, rid: uuid.UUID, ord_id: str, sz: str) -> str:
    """An Atlas entry the Executor sent an hour ago and that filled."""
    cl = derived_id("e", rid)
    with engine.begin() as c:
        c.execute(
            Order.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                proposal_id=pid,
                risk_check_id=rid,
                cl_ord_id=cl,
                ord_id=ord_id,
                inst_id=inst_id,
                side="buy",
                pos_side="net",
                ord_type="limit",
                td_mode="isolated",
                px=D(4000),
                sz=D(sz),
                filled_sz=D(sz),
                state="filled",
                origin="atlas",
                purpose="entry",
                created_at=AT - timedelta(hours=1, minutes=5),
                updated_at=AT - timedelta(hours=1),
            )
        )
    return cl


def hourly_candles(engine: Engine, inst_id: str, rng: str = "20") -> None:
    """30 closed 1H bars, each with high - low = ``rng`` and no gaps, so ATR(1H) = ``rng``."""
    with engine.begin() as c:
        for i in range(30):
            ts = AT - timedelta(hours=30 - i)
            c.execute(
                Candle.__table__.insert().values(
                    env=ENV,
                    inst_id=inst_id,
                    bar="1H",
                    ts=ts,
                    o=D(4040),
                    h=D(4040) + D(rng) / 2,
                    l=D(4040) - D(rng) / 2,
                    c=D(4040),
                    vol=D(10),
                    confirmed=True,
                )
            )


# --- OKX mock ---------------------------------------------------------------------------------------


def ok(rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})


NOT_FOUND = httpx.Response(200, json={"code": "51603", "msg": "Order does not exist", "data": []})


def position(inst_id: str, pos: str = "10", mark: str = "4050", opened_ms: int = OPENED_MS) -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst_id,
        "mgnMode": "isolated",
        "posId": "77" + inst_id[2:8],
        "posSide": "net",
        "pos": pos,
        "avgPx": "4000",
        "markPx": mark,
        "upl": "500",
        "lever": "3",
        "ccy": "USDT",
        "cTime": str(opened_ms),
        "uTime": str(AT_MS),
    }


def order_row(
    inst_id: str, cl: str, ord_id: str, *, state: str = "live", filled: str = "0"
) -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst_id,
        "ordId": ord_id,
        "clOrdId": cl,
        "px": "4000",
        "sz": "250",
        "ordType": "limit",
        "side": "buy",
        "posSide": "net",
        "tdMode": "isolated",
        "accFillSz": filled,
        "avgPx": "4000" if filled != "0" else "",
        "state": state,
        "lever": "3",
        "fee": "0",
        "feeCcy": "USDT",
        "cTime": str(AT_MS - 60_000),
        "uTime": str(AT_MS - 30_000),
    }


def fill_row(
    inst_id: str, ord_id: str, sz: str, trade_id: str, cl: str = "", side: str = "buy"
) -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst_id,
        "tradeId": trade_id,
        "ordId": ord_id,
        "clOrdId": cl,
        "billId": "b" + trade_id,
        "side": side,
        "posSide": "net",
        "fillPx": "4000",
        "fillSz": sz,
        "fee": "-0.1",
        "feeCcy": "USDT",
        "execType": "M",
        "fillPnl": "0",
        "ts": str(OPENED_MS),
    }


def algo_row(inst_id: str, algo_cl: str, sz: str = "250") -> dict[str, Any]:
    return {
        "algoId": "88001",
        "algoClOrdId": algo_cl,
        "instType": "SWAP",
        "instId": inst_id,
        "ordType": "oco",
        "side": "sell",
        "posSide": "net",
        "tdMode": "isolated",
        "sz": sz,
        "state": "live",
        "slTriggerPx": "3960",
        "slOrdPx": "-1",
        "tpTriggerPx": "4100",
        "tpOrdPx": "-1",
        "ordId": "",
        "cTime": str(AT_MS),
        "uTime": str(AT_MS),
    }


class Okx:
    """What OKX holds; respx answers every reconciliation read from it."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.positions: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.algos: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.orders: dict[str, dict[str, Any]] = {}  # by ordId and clOrdId, for GET trade/order
        self.router = router
        router.get("/api/v5/public/time").mock(return_value=ok([{"ts": str(AT_MS)}]))
        router.get("/api/v5/account/balance").mock(
            return_value=ok(
                [
                    {
                        "totalEq": "100000",
                        "upl": "0",
                        "uTime": str(AT_MS),
                        "details": [
                            {
                                "ccy": "USDT",
                                "eq": "100000",
                                "eqUsd": "100000",
                                "cashBal": "100000",
                                "availBal": "90000",
                                "frozenBal": "0",
                                "upl": "0",
                            }
                        ],
                    }
                ]
            )
        )
        router.get("/api/v5/asset/balances").mock(return_value=ok([]))
        router.get("/api/v5/account/positions").mock(side_effect=lambda r: ok(self.positions))
        router.get("/api/v5/trade/orders-pending").mock(side_effect=lambda r: ok(self.pending))
        router.get("/api/v5/trade/orders-algo-pending").mock(side_effect=self._algos)
        router.get("/api/v5/trade/fills-history").mock(side_effect=self._fills)
        router.get("/api/v5/trade/order").mock(side_effect=self._order)
        router.get("/api/v5/trade/order-algo").mock(return_value=NOT_FOUND)
        self.place_algo = router.post("/api/v5/trade/order-algo").mock(side_effect=self._place_algo)

    def _algos(self, request: httpx.Request) -> httpx.Response:
        kinds = request.url.params.get("ordType", "").split(",")
        return ok([a for a in self.algos if a["ordType"] in kinds])

    def _fills(self, request: httpx.Request) -> httpx.Response:
        return ok([f for f in self.fills if f["instType"] == request.url.params.get("instType")])

    def _order(self, request: httpx.Request) -> httpx.Response:
        key = request.url.params.get("ordId") or request.url.params.get("clOrdId") or ""
        row = self.orders.get(key)
        return ok([row]) if row is not None else NOT_FOUND

    def _place_algo(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return ok([{"algoId": "99001", "algoClOrdId": body["algoClOrdId"], "sCode": "0", "sMsg": ""}])

    def placed(self) -> list[dict[str, Any]]:
        return [json.loads(c.request.content) for c in self.place_algo.calls]


@pytest.fixture
def okx() -> Iterator[Okx]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield Okx(router)


def client() -> OkxClient:
    return OkxClient(ENV, CREDS, wall_clock_ms=lambda: AT_MS)


def factory(_creds: Any, *_a: Any, **kw: Any) -> OkxClient:
    return OkxClient(ENV, CREDS, wall_clock_ms=lambda: AT_MS, **kw)


async def run(engine: Engine, rds: redis.Redis, at: datetime = AT) -> Any:
    async with client() as c:
        return await reconcile(ENV, okx=c, engine=engine, redis_client=rds, now=lambda: at)


def envelopes(rds: redis.Redis, stream: str) -> list[EventEnvelope]:
    return [EventEnvelope.model_validate_json(f[bus.FIELD]) for _id, f in rds.xrange(stream)]


def posts(router: respx.MockRouter, path: str) -> list[dict[str, Any]]:
    return [
        json.loads(call.request.content)
        for call in router.calls
        if call.request.method == "POST" and call.request.url.path == f"/api/v5/{path}"
    ]


# --- AC1: an unprotected Atlas position gets a stop at its proposal's stop ---------------------------


async def test_unprotected_position_gets_a_stop_at_its_proposals_stop(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    pid, rid = make_check(engine, inst, stop="3960.004")
    cl = atlas_entry(engine, inst, pid, rid, "500001", "10")
    okx.positions = [position(inst)]
    okx.fills = [fill_row(inst, "500001", "10", "t1", cl)]

    result = await run(engine, rds)

    placed = okx.placed()
    assert len(placed) == 1
    body = placed[0]
    assert body["instId"] == inst
    assert body["ordType"] == "conditional"
    assert body["side"] == "sell"
    assert body["slTriggerPx"] == "3960.01"  # the proposal's stop, rounded to tickSz toward the mark
    assert body["sz"] == "10"
    assert body["reduceOnly"] is True
    assert body["algoClOrdId"] == protect_id(ENV, Position.model_validate(position(inst)), 0)
    assert result.protected[0]["stop_source"] == "proposal"
    assert result.protected[0]["proposal_id"] == str(pid)
    assert not result.mismatches

    with engine.connect() as c:
        row = c.execute(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == body["algoClOrdId"])).one()
    assert (row.state, row.kind, row.origin, row.algo_id) == ("live", "sl", "atlas", "99001")
    assert row.trigger_px == D("3960.01")

    alerts = [e for e in envelopes(rds, "atlas.system") if e.type == "system.alert"]
    assert len(alerts) == 1
    assert alerts[0].payload["code"] == "position_unprotected"
    assert alerts[0].payload["stop"] == "3960.01"
    room = [e for e in envelopes(rds, "atlas.warroom") if e.payload.get("kind") == "system_alert"]
    assert len(room) == 1
    assert "had no stop-loss on OKX" in room[0].payload["content_md"]


async def test_a_protected_position_is_left_alone_and_a_second_run_places_nothing(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    pid, rid = make_check(engine, inst)
    cl = atlas_entry(engine, inst, pid, rid, "500002", "10")
    okx.positions = [position(inst)]
    okx.fills = [fill_row(inst, "500002", "10", "t2", cl)]
    okx.algos = [algo_row(inst, derived_id("s", rid), sz="10")]

    result = await run(engine, rds)
    await run(engine, rds, AT + timedelta(seconds=5))

    assert okx.placed() == []
    assert result.protected == []
    assert [e for e in envelopes(rds, "atlas.system") if e.type == "system.alert"] == []



def _sending_entry(engine: Engine, inst_id: str, age: timedelta) -> str:
    """An Atlas entry stuck in ``sending`` (never reached OKX, never marked failed) since ``AT - age``."""
    pid, rid = make_check(engine, inst_id)
    cl = derived_id("e", rid)
    with engine.begin() as c:
        c.execute(
            Order.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                proposal_id=pid,
                risk_check_id=rid,
                cl_ord_id=cl,
                ord_id=None,
                inst_id=inst_id,
                side="buy",
                pos_side="net",
                ord_type="limit",
                td_mode="isolated",
                px=D(4000),
                sz=D(5),
                filled_sz=D(0),
                state="sending",
                origin="atlas",
                purpose="entry",
                created_at=AT - age,
                updated_at=AT - age,
            )
        )
    return cl


async def test_an_old_stuck_sending_order_does_not_block_protection(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    pid, rid = make_check(engine, inst, stop="3960.004")
    cl = atlas_entry(engine, inst, pid, rid, "500003", "10")
    _sending_entry(engine, inst, timedelta(hours=2))
    okx.positions = [position(inst)]
    okx.fills = [fill_row(inst, "500003", "10", "t3", cl)]

    result = await run(engine, rds)

    placed = okx.placed()
    assert len(placed) == 1
    assert placed[0]["slTriggerPx"] == "3960.01"
    assert result.protected[0]["stop_source"] == "proposal"


async def test_a_fresh_sending_order_leaves_protection_to_the_executor(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    pid, rid = make_check(engine, inst)
    cl = atlas_entry(engine, inst, pid, rid, "500004", "10")
    _sending_entry(engine, inst, timedelta(seconds=5))
    okx.positions = [position(inst)]
    okx.fills = [fill_row(inst, "500004", "10", "t4", cl)]

    result = await run(engine, rds)

    assert okx.placed() == []
    assert result.protected == []


# --- AC2: no Atlas proposal → emergency stop, never beyond max_stop_pct -----------------------------


async def test_position_without_a_proposal_gets_the_emergency_atr_stop(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    hourly_candles(engine, inst, rng="20")  # ATR(1H) = 20
    okx.positions = [position(inst, mark="4050")]
    okx.fills = [fill_row(inst, "600001", "10", "t3")]
    okx.orders["600001"] = order_row(inst, "", "600001", state="filled", filled="10")

    result = await run(engine, rds)

    # emergency_stop_atr_mult 2.0 x ATR 20 = 40 below the mark 4050; 8% cap (324) does not bind.
    assert [p["slTriggerPx"] for p in okx.placed()] == ["4010"]
    assert result.protected[0]["stop_source"] == "emergency_atr"
    assert result.protected[0]["proposal_id"] is None
    with engine.connect() as c:
        imported = c.execute(select(Order.origin).where(Order.ord_id == "600001")).scalar_one()
    assert imported == "external"


async def test_emergency_stop_is_capped_at_max_stop_pct(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    _set_settings(engine, {"max_stop_pct": "0.5", "emergency_stop_atr_mult": "2.0"})
    hourly_candles(engine, inst, rng="20")
    okx.positions = [position(inst, pos="-10", mark="4050")]  # a short
    okx.fills = [fill_row(inst, "600002", "10", "t4", side="sell")]
    okx.orders["600002"] = order_row(inst, "", "600002", state="filled", filled="10")

    result = await run(engine, rds)

    # 2 x 20 = 40 above the mark, but 0.5% of 4050 = 20.25 is the cap: 4070.25.
    body = okx.placed()[0]
    assert (body["side"], body["slTriggerPx"], body["sz"]) == ("buy", "4070.25", "10")
    assert result.protected[0]["stop_source"] == "emergency_max_stop_pct"
    assert D(body["slTriggerPx"]) - D(4050) <= D(4050) * D("0.005")


async def test_without_candles_the_emergency_stop_is_the_max_stop_pct(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    okx.positions = [position(inst, mark="4000")]
    okx.fills = [fill_row(inst, "600003", "10", "t5")]
    okx.orders["600003"] = order_row(inst, "", "600003", state="filled", filled="10")

    await run(engine, rds)

    assert [p["slTriggerPx"] for p in okx.placed()] == ["3680"]  # 8% below 4000


# --- AC3: pending orders unknown to the DB are imported as external; finished ones are closed --------


async def test_unknown_pending_order_is_imported_as_external(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    okx.pending = [order_row(inst, "manual1", "700001")]

    result = await run(engine, rds)

    with engine.connect() as c:
        row = c.execute(select(Order).where(Order.ord_id == "700001")).one()
    assert (row.origin, row.state, row.inst_id, row.sz) == ("external", "live", inst, D(250))
    assert result.orders_imported == ["700001"]
    changed = [e for e in envelopes(rds, "atlas.orders") if e.type == "order.state_changed"]
    assert changed[0].payload["origin"] == "external"
    assert changed[0].payload["to_state"] == "live"


async def test_db_order_okx_no_longer_lists_takes_its_final_state(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    pid, rid = make_check(engine, inst)
    cl = derived_id("e", rid)
    with engine.begin() as c:
        c.execute(
            Order.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                proposal_id=pid,
                risk_check_id=rid,
                cl_ord_id=cl,
                ord_id="700002",
                inst_id=inst,
                side="buy",
                pos_side="net",
                ord_type="limit",
                td_mode="isolated",
                px=D(4000),
                sz=D(250),
                state="live",
                origin="atlas",
                purpose="entry",
                created_at=AT,
                updated_at=AT,
            )
        )
    okx.orders["700002"] = order_row(inst, cl, "700002", state="canceled")

    result = await run(engine, rds)

    with engine.connect() as c:
        assert c.scalar(select(Order.state).where(Order.ord_id == "700002")) == "canceled"
    assert result.orders_closed == ["700002"]


# --- AC4 (chaos): a crash after the entry was sent leaves exactly one order --------------------------


async def test_crash_after_send_reconciles_to_one_order_and_no_second_send(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = world["inst_id"]
    _pid, rid = make_check(engine, inst)
    cl = derived_id("e", rid)
    okx.router.post("/api/v5/account/set-leverage").mock(
        return_value=ok([{"lever": "3", "mgnMode": "isolated", "instId": inst, "posSide": ""}])
    )
    okx.router.post("/api/v5/trade/order").mock(
        return_value=ok(
            [{"clOrdId": cl, "ordId": "900001", "tag": "", "ts": str(AT_MS), "sCode": "0", "sMsg": ""}]
        )
    )

    real_order = recorder_mod._Recorder.order

    def crash_on_live(self: Any, order_id: uuid.UUID, to_state: str, **fields: Any) -> None:
        if to_state == "live":
            raise RuntimeError("process killed before the ordId was stored")
        real_order(self, order_id, to_state, **fields)

    async def no_sleep(_s: float) -> None:
        return None

    def do_execute() -> Any:
        return execute(
            rid,
            None,
            engine=engine,
            redis_client=rds,
            client_factory=factory,
            active_env=ENV,
            sleep=no_sleep,
            monotonic=lambda: 0.0,
            now=lambda: AT,
        )

    monkeypatch.setattr(recorder_mod._Recorder, "order", crash_on_live)
    with pytest.raises(RuntimeError, match="process killed"):
        await do_execute()
    monkeypatch.setattr(recorder_mod._Recorder, "order", real_order)
    with engine.connect() as c:
        crashed = c.execute(select(Order.state, Order.ord_id).where(Order.cl_ord_id == cl)).one()
    assert (crashed.state, crashed.ord_id) == ("sending", None)
    assert len(posts(okx.router, "trade/order")) == 1

    # Restart: OKX holds the entry (partly filled) with its attached stop now live as an algo order.
    okx.pending = [order_row(inst, cl, "900001", state="partially_filled", filled="100")]
    okx.algos = [algo_row(inst, derived_id("s", rid))]
    okx.positions = [position(inst, pos="100")]
    okx.fills = [fill_row(inst, "900001", "100", "t9", cl)]
    gate = ReconcileGate()
    job = reconcile_job(
        ENV, engine, "app-key", gate, client_factory=factory, redis_client=rds, now=lambda: AT
    )
    await job()
    assert gate.ready

    again = await do_execute()

    assert again.status == "refused"
    assert again.reason == "already_executed"
    assert len(posts(okx.router, "trade/order")) == 1  # no second entry was sent
    assert okx.placed() == []  # the attached stop protects it; no extra stop
    with engine.connect() as c:
        rows = c.execute(
            select(Order.state, Order.ord_id, Order.filled_sz).where(Order.cl_ord_id == cl)
        ).all()
        total = c.scalar(select(func.count()).select_from(Order).where(Order.inst_id == inst))
    assert [(r.state, r.ord_id, r.filled_sz) for r in rows] == [("partially_filled", "900001", D(100))]
    assert total == 1


# --- AC5: an unexplained size trips the breaker; startup gates /execute ------------------------------


async def test_position_size_the_fills_cannot_explain_trips_the_breaker(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    pid, rid = make_check(engine, inst)
    cl = atlas_entry(engine, inst, pid, rid, "800001", "4")
    okx.positions = [position(inst, pos="10")]
    okx.fills = [fill_row(inst, "800001", "4", "t8", cl)]
    okx.algos = [algo_row(inst, derived_id("s", rid), sz="10")]

    result = await run(engine, rds)

    assert result.breaker_tripped
    assert result.mismatches == [
        {
            "inst_id": inst,
            "pos_id": position(inst)["posId"],
            "pos_side": "net",
            "okx_pos": "10",
            "db_pos": "4",
            "opened_at": result.mismatches[0]["opened_at"],
        }
    ]
    with engine.connect() as c:
        breakers = c.scalar(select(TradingState.breakers).where(TradingState.env == ENV))
    assert breakers["reconciliation_mismatch"]["active"] is True
    tripped = [e for e in envelopes(rds, "atlas.system") if e.type == "system.circuit_breaker"]
    assert tripped[0].payload["name"] == "reconciliation_mismatch"

    # The same mismatch on the next run does not trip it again.
    again = await run(engine, rds, AT + timedelta(seconds=5))
    assert again.mismatches and not again.breaker_tripped


async def test_a_matched_position_does_not_trip_and_stamps_reconciled_at(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    inst = world["inst_id"]
    pid, rid = make_check(engine, inst)
    cl = atlas_entry(engine, inst, pid, rid, "800002", "10")
    okx.positions = [position(inst, pos="10")]
    okx.fills = [fill_row(inst, "800002", "6", "t10", cl), {**fill_row(inst, "800002", "4", "t11", cl)}]
    okx.algos = [algo_row(inst, derived_id("s", rid), sz="10")]

    result = await run(engine, rds)

    assert not result.mismatches and not result.breaker_tripped
    assert result.fills_new == 2
    with engine.connect() as c:
        stamped = c.scalar(
            select(SyncStatus.reconciled_at).where(SyncStatus.env == ENV, SyncStatus.stream == "reconcile")
        )
        breakers = c.scalar(select(TradingState.breakers).where(TradingState.env == ENV))
    assert stamped == AT
    assert not (breakers or {}).get("reconciliation_mismatch")


def _settings() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8100,
        env=ENV,
        internal_shared_secret=SECRET,
        run_scheduler=False,
        run_market_ws=False,
        run_private_ws=False,
    )


async def test_execute_is_refused_until_the_startup_reconciliation_has_run(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: Okx
) -> None:
    _pid, rid = make_check(engine, world["inst_id"])
    gate = ReconcileGate()
    scheduler = Scheduler()
    scheduler.add(
        JOB,
        300,
        reconcile_job(ENV, engine, "app-key", gate, client_factory=factory, redis_client=rds, now=lambda: AT),
    )
    application = create_app(_settings(), scheduler=scheduler, engine=engine, reconcile_gate=gate)
    application.state.executor_redis = rds
    application.state.executor_client_factory = factory
    path = f"/internal/execute/{rid}"
    with TestClient(application) as http:
        before = http.post(path, headers=signed_headers(SECRET, "POST", path))
        assert before.status_code == 409
        assert before.json()["reason"] == "not_reconciled"
        assert posts(okx.router, "trade/order") == []

        await scheduler.run_now(JOB)
        assert gate.ready

        other = f"/internal/execute/{uuid.uuid4()}"
        after = http.post(other, headers=signed_headers(SECRET, "POST", other))
    assert after.json()["reason"] == "risk_check_not_found"



async def test_owner_approval_execute_is_refused_until_the_startup_reconciliation_has_run(
    engine: Engine, rds: redis.Redis, okx: Okx
) -> None:
    gate = ReconcileGate()
    scheduler = Scheduler()
    scheduler.add(
        JOB,
        300,
        reconcile_job(ENV, engine, "app-key", gate, client_factory=factory, redis_client=rds, now=lambda: AT),
    )
    application = create_app(_settings(), scheduler=scheduler, engine=engine, reconcile_gate=gate)
    calls: list[Any] = []

    async def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("the Executor must not be called before reconciliation")

    application.state.approval_execute = spy
    application.state.risk_redis = rds
    pid = uuid.uuid4()
    path = f"/internal/proposals/{pid}/execute"
    body = json.dumps({"approved_by": "owner"}).encode()
    with TestClient(application) as http:
        before = http.post(path, content=body, headers=signed_headers(SECRET, "POST", path, body))
        assert before.status_code == 409
        assert before.json()["reason"] == "not_reconciled"
        assert before.json()["proposal_id"] == str(pid)
        assert calls == []
        assert posts(okx.router, "trade/order") == []

        await scheduler.run_now(JOB)
        assert gate.ready

        after = http.post(path, content=body, headers=signed_headers(SECRET, "POST", path, body))
    assert after.json()["reason"] != "not_reconciled"
    assert calls == []


def test_the_real_app_registers_reconciliation_first_and_starts_closed(engine: Engine) -> None:
    gate = ReconcileGate()
    jobs = list(default_scheduler(_settings(), engine, gate).jobs)
    assert jobs[0] == JOB
    application = create_app(_settings(), engine=engine)
    assert application.state.reconcile_gate.ready is False
    assert JOB in application.state.scheduler.jobs


def test_an_injected_scheduler_without_the_job_leaves_execute_open(engine: Engine) -> None:
    application = create_app(_settings(), scheduler=Scheduler(), engine=engine)
    assert application.state.reconcile_gate.ready is True


async def test_a_private_ws_reconnect_triggers_reconciliation(engine: Engine) -> None:
    class StubFeed:
        def __init__(self) -> None:
            self.callbacks: list[Callable[[dict[str, Any]], Any]] = []

        def on_reconnected(self, cb: Callable[[dict[str, Any]], Any]) -> None:
            self.callbacks.append(cb)

    runs: list[int] = []

    async def fake_job() -> None:
        runs.append(1)

    scheduler = Scheduler()
    scheduler.add(JOB, 300, fake_job)
    feed = StubFeed()
    create_app(_settings(), scheduler=scheduler, engine=engine, private_feed=feed)  # type: ignore[arg-type]
    assert len(feed.callbacks) == 1
    feed.callbacks[0]({"connects": 2, "logged_in_at": "2032-07-14T10:00:00Z"})
    await asyncio.gather(*scheduler._triggered)
    assert runs == [1]
