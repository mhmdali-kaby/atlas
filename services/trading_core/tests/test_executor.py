"""ATL-143: the Executor sends only approved orders, retries with the same clOrdId after looking the
first attempt up, confirms a server-side stop (attached or OCO) and closes when it cannot.

OKX is mocked with respx; the database and Redis are the real test instances.
"""

from __future__ import annotations

import ast
import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis
import respx
from atlas_shared import bus, testing
from atlas_shared.db import (
    AlgoOrder,
    Connection,
    Instrument,
    Order,
    Proposal,
    RiskCheck,
    TradingState,
    make_engine,
)
from atlas_shared.events import EventEnvelope
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.execution.config import round_stop
from trading_core.execution.executor import derived_id, execute, round_px
from trading_core.okx import OkxClient, OkxCredentials
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

ENV = "demo"
AT = datetime(2031, 3, 12, 15, 0, tzinfo=UTC)
BASE = "https://www.okx.com"
CREDS = OkxCredentials(api_key="test-key-0000", secret="test-only-secret", passphrase="p")  # noqa: S106
NOW_MS = 1_793_207_736_680
TIME = {"code": "0", "msg": "", "data": [{"ts": str(NOW_MS)}]}
SECRET = "atlas-test-internal-secret"  # noqa: S105
STREAMS = ("atlas.orders", "atlas.system", "atlas.warroom")
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


@pytest.fixture
def world(engine: Engine) -> Iterator[dict[str, Any]]:
    inst_id = f"EX{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
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
        c.execute(
            text(
                "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level,"
                " max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at)"
                " VALUES (:id, :e, 7, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = 7, data = EXCLUDED.data"
            ),
            {"id": uuid.uuid4(), "e": ENV, "d": json.dumps({"autonomy_level": 2, "stop_verify_seconds": 3})},
        )
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
                base_ccy="EX",
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
    yield {"inst_id": inst_id}
    with engine.begin() as c:
        c.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == inst_id))
        c.execute(delete(Order).where(Order.inst_id == inst_id))
        c.execute(delete(Proposal).where(Proposal.inst_id == inst_id))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
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


def make_check(
    engine: Engine,
    inst_id: str,
    *,
    result: str = "approved",
    decision: str | None = "send_now",
    order_type: str = "limit",
    status: str = "risk_checked",
    take_profits: list[dict[str, Any]] | None = None,
) -> uuid.UUID:
    pid, rid = uuid.uuid4(), uuid.uuid4()
    approved = result != "rejected"
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
                order_type=order_type,
                entry=D(4000),
                stop=D(3960),
                expected_r=D("2.5"),
                take_profits=[{"price": 4100, "pct": 50}] if take_profits is None else take_profits,
                leverage_suggestion=3,
                size_suggestion_pct=D(5),
                status=status,
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            RiskCheck.__table__.insert().values(
                id=rid,
                env=ENV,
                proposal_id=pid,
                settings_version=7,
                result=result,
                checks=[],
                final_size=D(250) if approved else None,
                final_notional_usd=D(1000) if approved else None,
                leverage=3 if approved else None,
                reason="test",
                decision=decision if approved else None,
                created_at=AT,
                updated_at=AT,
            )
        )
    return rid


# --- OKX mock helpers ------------------------------------------------------------------------------


def ok(rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})


def ack(cl: str, ord_id: str = "900001") -> httpx.Response:
    return ok([{"clOrdId": cl, "ordId": ord_id, "tag": "", "ts": str(NOW_MS), "sCode": "0", "sMsg": ""}])


def refused(cl: str, code: str, msg: str = "refused") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": "1",
            "msg": "",
            "data": [{"clOrdId": cl, "ordId": "", "tag": "", "sCode": code, "sMsg": msg}],
        },
    )


NOT_FOUND = httpx.Response(200, json={"code": "51603", "msg": "Order does not exist", "data": []})


def detail(
    inst_id: str,
    cl: str,
    *,
    state: str = "live",
    filled: str = "0",
    attach_sl: bool = True,
    ord_type: str = "limit",
) -> dict[str, Any]:
    attach = (
        [
            {
                "attachAlgoId": "77001",
                "attachAlgoClOrdId": "",
                "slTriggerPx": "3960",
                "slOrdPx": "-1",
                "tpTriggerPx": "4100",
                "tpOrdPx": "-1",
                "sz": "",
                "state": "live",
                "failCode": "",
                "failReason": "",
            }
        ]
        if attach_sl
        else []
    )
    return {
        "instType": "SWAP",
        "instId": inst_id,
        "ordId": "900001",
        "clOrdId": cl,
        "px": "4000",
        "sz": "250",
        "ordType": ord_type,
        "side": "buy",
        "posSide": "net",
        "tdMode": "isolated",
        "accFillSz": filled,
        "avgPx": "4000" if filled != "0" else "",
        "state": state,
        "lever": "3",
        "attachAlgoOrds": attach,
        "cTime": str(NOW_MS),
        "uTime": str(NOW_MS),
    }


def algo(inst_id: str, algo_cl: str, kind: str = "oco") -> dict[str, Any]:
    return {
        "algoId": "88001",
        "algoClOrdId": algo_cl,
        "instType": "SWAP",
        "instId": inst_id,
        "ordType": kind,
        "side": "sell",
        "posSide": "net",
        "tdMode": "isolated",
        "sz": "250",
        "state": "live",
        "slTriggerPx": "3960",
        "slOrdPx": "-1",
        "tpTriggerPx": "4100",
        "tpOrdPx": "-1",
        "ordId": "",
        "cTime": str(NOW_MS),
        "uTime": str(NOW_MS),
    }


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def mono(self) -> float:
        return self.t


def factory(creds: Any, **kw: Any) -> OkxClient:
    return OkxClient(ENV, CREDS, wall_clock_ms=lambda: NOW_MS, **kw)


@pytest.fixture
def okx() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get("/api/v5/public/time").mock(return_value=httpx.Response(200, json=TIME))
        router.post("/api/v5/account/set-leverage").mock(
            return_value=ok([{"lever": "3", "mgnMode": "isolated", "instId": "X", "posSide": ""}])
        )
        yield router


async def run(
    engine: Engine,
    rds: redis.Redis,
    rid: uuid.UUID,
    approved_by: str | None = None,
    clock: FakeClock | None = None,
) -> Any:
    clock = clock or FakeClock()
    return await execute(
        rid,
        approved_by,
        engine=engine,
        redis_client=rds,
        client_factory=factory,
        active_env=ENV,
        sleep=clock.sleep,
        monotonic=clock.mono,
        now=lambda: AT,
    )


def envelopes(rds: redis.Redis, stream: str) -> list[EventEnvelope]:
    return [EventEnvelope.model_validate_json(f[bus.FIELD]) for _id, f in rds.xrange(stream)]


def trade_posts(router: respx.MockRouter, path: str) -> list[dict[str, Any]]:
    return [
        json.loads(call.request.content)
        for call in router.calls
        if call.request.method == "POST" and call.request.url.path == f"/api/v5/{path}"
    ]


# --- AC1: refusals send nothing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "approved_by", "reason"),
    [
        ({"result": "rejected"}, None, "risk_rejected"),
        ({"decision": "awaiting_owner"}, None, "owner_approval_required"),
        ({"decision": "awaiting_owner"}, "strategist", "owner_approval_required"),
        ({"decision": "advise_only"}, "owner", "advise_only"),
    ],
)
async def test_unapproved_checks_send_no_request(
    engine: Engine,
    rds: redis.Redis,
    world: dict[str, Any],
    kwargs: dict[str, Any],
    approved_by: str | None,
    reason: str,
) -> None:
    rid = make_check(engine, world["inst_id"], **kwargs)
    with respx.mock(assert_all_called=False) as router:
        out = await run(engine, rds, rid, approved_by)
    assert router.calls.call_count == 0
    assert (out.status, out.reason) == ("refused", reason)
    assert out.detail
    with engine.connect() as c:
        assert c.scalar(select(Order.id).where(Order.inst_id == world["inst_id"])) is None


async def test_paused_trading_sends_no_request(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    rid = make_check(engine, world["inst_id"])
    with engine.begin() as c:
        c.execute(
            TradingState.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                paused=True,
                reason="kill switch",
                breakers={},
                created_at=AT,
                updated_at=AT,
            )
        )
    with respx.mock(assert_all_called=False) as router:
        out = await run(engine, rds, rid)
    assert router.calls.call_count == 0
    assert (out.status, out.reason) == ("refused", "trading_paused")
    assert "kill switch" in out.detail


async def test_active_breaker_and_changed_settings_refuse(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    rid = make_check(engine, world["inst_id"])
    with engine.begin() as c:
        c.execute(text("UPDATE settings SET version = 8 WHERE env = :e"), {"e": ENV})
    with respx.mock(assert_all_called=False) as router:
        out = await run(engine, rds, rid)
        assert (out.reason, router.calls.call_count) == ("settings_changed", 0)
        with engine.begin() as c:
            c.execute(text("UPDATE settings SET version = 7 WHERE env = :e"), {"e": ENV})
            c.execute(
                TradingState.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=ENV,
                    paused=False,
                    created_at=AT,
                    updated_at=AT,
                    breakers={"daily_loss": {"active": True, "since": "2031-03-12T14:00:00Z"}},
                )
            )
        out = await run(engine, rds, rid)
        assert (out.reason, router.calls.call_count) == ("breaker_active", 0)


async def test_awaiting_owner_with_owner_approval_is_sent(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    rid = make_check(engine, world["inst_id"], decision="awaiting_owner", status="awaiting_owner")
    cl = derived_id("e", rid)
    okx.post("/api/v5/trade/order").mock(return_value=ack(cl))
    okx.get("/api/v5/trade/order").mock(return_value=ok([detail(world["inst_id"], cl)]))
    okx.get("/api/v5/trade/orders-algo-pending").mock(return_value=ok([]))
    out = await run(engine, rds, rid, "owner")
    assert (out.status, out.stop_verified) == ("sent", True)
    with engine.connect() as c:
        assert (
            c.scalar(
                select(Proposal.status)
                .join(RiskCheck, RiskCheck.proposal_id == Proposal.id)
                .where(RiskCheck.id == rid)
            )
            == "sent"
        )


async def test_a_second_call_does_not_send_again(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    rid = make_check(engine, world["inst_id"])
    cl = derived_id("e", rid)
    okx.post("/api/v5/trade/order").mock(return_value=ack(cl))
    okx.get("/api/v5/trade/order").mock(return_value=ok([detail(world["inst_id"], cl)]))
    okx.get("/api/v5/trade/orders-algo-pending").mock(return_value=ok([]))
    assert (await run(engine, rds, rid)).status == "sent"
    again = await run(engine, rds, rid)
    assert (again.status, again.reason) == ("refused", "already_executed")
    assert len(trade_posts(okx, "trade/order")) == 1


# --- happy path: body, persistence, events -----------------------------------------------------------


async def test_sent_order_carries_the_stop_and_tp1_and_is_recorded(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    inst = world["inst_id"]
    rid = make_check(engine, inst)
    cl = derived_id("e", rid)
    okx.post("/api/v5/trade/order").mock(return_value=ack(cl))
    okx.get("/api/v5/trade/order").mock(return_value=ok([detail(inst, cl)]))
    okx.get("/api/v5/trade/orders-algo-pending").mock(return_value=ok([]))

    out = await run(engine, rds, rid)

    assert (out.status, out.cl_ord_id, out.ord_id, out.stop_verified) == ("sent", cl, "900001", True)
    assert len(cl) == 32 and cl.isalnum()
    [lev] = trade_posts(okx, "account/set-leverage")
    assert lev == {"instId": inst, "lever": "3", "mgnMode": "isolated"}
    [body] = trade_posts(okx, "trade/order")
    assert body["clOrdId"] == cl and body["sz"] == "250" and body["px"] == "4000"
    assert body["tdMode"] == "isolated" and body["side"] == "buy" and body["ordType"] == "limit"
    [attached] = body["attachAlgoOrds"]
    assert attached["slTriggerPx"] == "3960" and attached["tpTriggerPx"] == "4100"
    assert attached["attachAlgoClOrdId"] == derived_id("s", rid)
    with engine.connect() as c:
        order = c.execute(select(Order).where(Order.cl_ord_id == cl)).one()
        assert (order.state, order.ord_id, order.purpose, order.risk_check_id) == (
            "live",
            "900001",
            "entry",
            rid,
        )
        sl = c.execute(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == derived_id("s", rid))).one()
        assert (sl.kind, sl.state, sl.sl_trigger_px, sl.tp_trigger_px) == ("oco", "live", D(3960), D(4100))
    states = [
        (e.payload["from_state"], e.payload["to_state"])
        for e in envelopes(rds, "atlas.orders")
        if e.type == "order.state_changed"
    ]
    assert states == [(None, "new"), ("new", "sending"), ("sending", "live")]
    [msg] = envelopes(rds, "atlas.warroom")
    assert msg.source == "system:executor" and msg.payload["kind"] == "order"
    assert msg.payload["card"]["stop_verified"] is True


# --- AC2: 502 then success, same clOrdId, looked up in between ----------------------------------------


async def test_502_then_success_places_exactly_one_order_with_the_same_cl_ord_id(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    inst = world["inst_id"]
    rid = make_check(engine, inst)
    cl = derived_id("e", rid)
    okx.post("/api/v5/trade/order").mock(side_effect=[httpx.Response(502, text="bad gateway"), ack(cl)])
    okx.get("/api/v5/trade/order").mock(side_effect=[NOT_FOUND, ok([detail(inst, cl)])])
    okx.get("/api/v5/trade/orders-algo-pending").mock(return_value=ok([]))
    clock = FakeClock()

    out = await run(engine, rds, rid, clock=clock)

    assert out.status == "sent"
    posts = trade_posts(okx, "trade/order")
    assert [p["clOrdId"] for p in posts] == [cl, cl]
    assert posts[0] == posts[1]
    sequence = [
        (c.request.method, c.request.url.path, c.request.url.params.get("clOrdId"))
        for c in okx.calls
        if c.request.url.path == "/api/v5/trade/order"
    ]
    assert sequence[:3] == [
        ("POST", "/api/v5/trade/order", None),
        ("GET", "/api/v5/trade/order", cl),
        ("POST", "/api/v5/trade/order", None),
    ]
    assert clock.sleeps[0] == 0.5  # backoff before the retry
    with engine.connect() as c:
        assert c.scalar(select(Order.state).where(Order.cl_ord_id == cl)) == "live"


async def test_a_lost_answer_is_adopted_from_the_lookup_not_resent(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    inst = world["inst_id"]
    rid = make_check(engine, inst)
    cl = derived_id("e", rid)
    okx.post("/api/v5/trade/order").mock(side_effect=[httpx.ConnectError("reset")])
    okx.get("/api/v5/trade/order").mock(return_value=ok([detail(inst, cl)]))
    okx.get("/api/v5/trade/orders-algo-pending").mock(return_value=ok([]))
    out = await run(engine, rds, rid)
    assert (out.status, out.ord_id) == ("sent", "900001")
    assert len(trade_posts(okx, "trade/order")) == 1


async def test_an_okx_refusal_is_recorded_with_the_human_message(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    rid = make_check(engine, world["inst_id"])
    cl = derived_id("e", rid)
    okx.post("/api/v5/trade/order").mock(return_value=refused(cl, "51008", "Insufficient balance"))
    out = await run(engine, rds, rid)
    assert (out.status, out.reason, out.okx_code) == ("failed", "okx_refused", "51008")
    assert "Insufficient balance" in out.detail
    assert len(trade_posts(okx, "trade/order")) == 1
    with engine.connect() as c:
        row = c.execute(select(Order).where(Order.cl_ord_id == cl)).one()
        assert (row.state, row.last_error_code) == ("rejected", "51008")
    [msg] = envelopes(rds, "atlas.warroom")
    assert msg.payload["kind"] == "order" and "Insufficient balance" in msg.payload["content_md"]


# --- AC3: no stop after stop_verify_seconds → reduce-only close + alert -----------------------------


async def test_no_stop_after_the_verify_window_closes_reduce_only_and_alerts(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    inst = world["inst_id"]
    rid = make_check(engine, inst, order_type="market")
    cl, close_cl = derived_id("e", rid), derived_id("c", rid)
    filled = detail(inst, cl, state="filled", filled="250", ord_type="market")
    okx.post("/api/v5/trade/order").mock(side_effect=[ack(cl), ack(close_cl, "900002")])
    okx.get("/api/v5/trade/order").mock(return_value=ok([filled]))
    okx.get("/api/v5/trade/orders-algo-pending").mock(return_value=ok([]))
    clock = FakeClock()

    out = await run(engine, rds, rid, clock=clock)

    assert (out.status, out.reason) == ("unprotected_closed", "stop_unverified")
    assert clock.t >= 3  # waited the whole stop_verify_seconds
    entry, close = trade_posts(okx, "trade/order")
    assert entry["clOrdId"] == cl and "px" not in entry
    assert close == {
        "instId": inst,
        "tdMode": "isolated",
        "side": "sell",
        "ordType": "market",
        "sz": "250",
        "clOrdId": close_cl,
        "reduceOnly": True,
    }
    alerts = envelopes(rds, "atlas.system")
    assert [a.type for a in alerts] == ["system.alert"]
    assert alerts[0].payload["code"] == "stop_unverified" and alerts[0].payload["closed_sz"] == "250"
    kinds = [m.payload["kind"] for m in envelopes(rds, "atlas.warroom")]
    assert kinds == ["system_alert"]
    with engine.connect() as c:
        row = c.execute(select(Order).where(Order.cl_ord_id == close_cl)).one()
        assert (row.purpose, row.reduce_only, row.state, row.sz) == ("close", True, "live", D(250))


# --- AC4: attach refused → bare entry + OCO, then verified ------------------------------------------


async def test_attach_refused_places_an_oco_and_verifies_it(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], okx: respx.MockRouter
) -> None:
    inst = world["inst_id"]
    rid = make_check(engine, inst)
    cl, bare, oco = derived_id("e", rid), derived_id("f", rid), derived_id("o", rid)
    okx.post("/api/v5/trade/order").mock(side_effect=[refused(cl, "51010", "not supported"), ack(bare)])
    okx.post("/api/v5/trade/order-algo").mock(
        return_value=ok([{"algoId": "88001", "algoClOrdId": oco, "clOrdId": "", "sCode": "0", "sMsg": ""}])
    )
    okx.get("/api/v5/trade/order").mock(return_value=ok([detail(inst, bare, attach_sl=False)]))
    okx.get("/api/v5/trade/orders-algo-pending").mock(side_effect=[ok([]), ok([algo(inst, oco)])])
    clock = FakeClock()

    out = await run(engine, rds, rid, clock=clock)

    assert (out.status, out.cl_ord_id, out.stop_verified) == ("sent", bare, True)
    first, second = trade_posts(okx, "trade/order")
    assert "attachAlgoOrds" in first and "attachAlgoOrds" not in second
    assert second["clOrdId"] == bare
    [algo_body] = trade_posts(okx, "trade/order-algo")
    assert algo_body["ordType"] == "oco" and algo_body["algoClOrdId"] == oco
    assert algo_body["slTriggerPx"] == "3960" and algo_body["tpTriggerPx"] == "4100"
    assert algo_body["side"] == "sell" and algo_body["reduceOnly"] is True and algo_body["sz"] == "250"
    algo_calls = [c for c in okx.calls if c.request.url.path.endswith("orders-algo-pending")]
    assert len(algo_calls) == 2  # verified on the second poll
    with engine.connect() as c:
        assert c.scalar(select(AlgoOrder.state).where(AlgoOrder.algo_cl_ord_id == oco)) == "live"
        assert (
            c.scalar(select(AlgoOrder.state).where(AlgoOrder.algo_cl_ord_id == derived_id("s", rid)))
            == "failed"
        )
        assert c.scalar(select(Order.cl_ord_id).where(Order.risk_check_id == rid)) == bare


# --- AC5: only execution/ calls the order wrappers; the route is signed ------------------------------

SERVICES = Path(__file__).resolve().parents[2]
WRAPPERS = {"place_order", "place_batch_orders", "place_algo_order"}
ORDER_PATHS = {"trade/order", "trade/batch-orders", "trade/order-algo"}
TC = SERVICES / "trading_core" / "src" / "trading_core"
# The wrappers' own definitions (trade.py), the read client's GET trade/order (trade_read.py) and the
# generated defaults, whose okx_rest.rate_limits are keyed by endpoint path.
DEFINITIONS = {
    TC / "okx" / "trade.py",
    TC / "okx" / "trade_read.py",
    SERVICES / "shared" / "src" / "atlas_shared" / "defaults.py",
}


def order_calls(path: Path) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if name in WRAPPERS:
                hits.append(f"{name}()")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.removeprefix("/api/v5/") in ORDER_PATHS:
                hits.append(node.value)
    return hits


def test_only_execution_calls_the_order_wrappers() -> None:
    offenders: dict[str, list[str]] = {}
    for path in SERVICES.glob("*/src/**/*.py"):
        if (TC / "execution") in path.parents or path in DEFINITIONS:
            continue
        if hits := order_calls(path):
            offenders[str(path.relative_to(SERVICES))] = hits
    assert offenders == {}
    assert order_calls(TC / "execution" / "okx_ops.py")  # the guard does see the real caller


def test_the_order_guard_catches_a_call(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "async def f(t):\n    await t.place_order(1)\n    await t.place_batch_orders([])\n"
        "    await t.place_algo_order(2)\n    p = '/api/v5/trade/batch-orders'\n",
        encoding="utf-8",
    )
    assert sorted(order_calls(bad)) == sorted(
        ["place_order()", "place_batch_orders()", "place_algo_order()", "/api/v5/trade/batch-orders"]
    )


def app_client(engine: Engine, rds: redis.Redis) -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=8100,
        env=ENV,
        internal_shared_secret=SECRET,
        run_scheduler=False,
        run_market_ws=False,
    )
    application = create_app(settings, scheduler=Scheduler(), engine=engine)
    application.state.executor_redis = rds
    application.state.executor_client_factory = factory
    return TestClient(application)


async def test_execute_route_rejects_a_missing_or_bad_signature(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    rid = make_check(engine, world["inst_id"])
    path = f"/internal/execute/{rid}"
    with respx.mock(assert_all_called=False) as router, app_client(engine, rds) as client:
        assert client.post(path).status_code == 401
        bad = signed_headers("not-the-secret", "POST", path)
        assert client.post(path, headers=bad).status_code == 401
        wrong_path = signed_headers(SECRET, "POST", f"/internal/execute/{uuid.uuid4()}")
        assert client.post(path, headers=wrong_path).status_code == 401
    assert router.calls.call_count == 0
    with engine.connect() as c:
        assert c.scalar(select(Order.id).where(Order.risk_check_id == rid)) is None


async def test_execute_route_with_a_valid_signature_runs_the_executor(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    rid = make_check(engine, world["inst_id"], decision="awaiting_owner")
    path = f"/internal/execute/{rid}"
    body = b'{"approved_by": "nobody"}'
    with respx.mock(assert_all_called=False) as router, app_client(engine, rds) as client:
        resp = client.post(path, content=body, headers=signed_headers(SECRET, "POST", path, body))
    assert resp.status_code == 409
    assert resp.json()["reason"] == "owner_approval_required"
    assert router.calls.call_count == 0


def test_round_px_never_moves_a_buy_up_or_a_sell_down() -> None:
    assert round_px(D("4000.016"), D("0.01"), "buy") == D("4000.01")
    assert round_px(D("4000.011"), D("0.01"), "sell") == D("4000.02")
    assert round_px(D("4000"), None, "buy") == D("4000")


def test_round_stop_moves_toward_the_entry_never_wider() -> None:
    assert round_stop(D("3960.005"), D("0.01"), "buy") == D("3960.01")
    assert round_stop(D("4040.005"), D("0.01"), "sell") == D("4040")
    assert round_stop(D("3960"), D("0.01"), "buy") == D("3960")
