"""ATL-147: kill switch (decision #251), pause/resume, one-position close and one-order cancel.

OKX is mocked with respx; the database and Redis are the real test instances.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
import redis
import respx
from atlas_shared import bus, testing
from atlas_shared.db import AlgoOrder, Connection, Order, Proposal, RiskCheck, TradingState, make_engine
from atlas_shared.events import EventEnvelope
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.execution import kill as control
from trading_core.okx import OkxClient, OkxCredentials
from trading_core.okx.error_messages import human_message
from trading_core.risk.breakers import Breakers
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
    tag = uuid.uuid4().hex[:6].upper()
    insts = [f"KA{tag}-USDT-SWAP", f"KB{tag}-USDT-SWAP"]
    with engine.begin() as c:
        old_settings = c.execute(
            text("SELECT version, data FROM settings WHERE env = :e"), {"e": ENV}
        ).first()
        old_conn = c.execute(
            select(Connection.id, Connection.last_verified_at).where(
                Connection.exchange == "okx", Connection.env == ENV
            )
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
                " ON CONFLICT (env) DO UPDATE SET data = EXCLUDED.data"
            ),
            {"id": uuid.uuid4(), "e": ENV, "d": json.dumps({"order_max_retries": 3})},
        )
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
                    last_verified_at=AT,
                    created_at=AT,
                    updated_at=AT,
                )
            )
        c.execute(delete(TradingState).where(TradingState.env == ENV))
    yield {"insts": insts}
    with engine.begin() as c:
        for inst in insts:
            c.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == inst))
            c.execute(delete(Order).where(Order.inst_id == inst))
            c.execute(delete(Proposal).where(Proposal.inst_id == inst))
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
                .values(last_verified_at=old_conn.last_verified_at)
            )
        if old_settings is None:
            c.execute(text("DELETE FROM settings WHERE env = :e"), {"e": ENV})
        else:
            c.execute(
                text("UPDATE settings SET version = :v, data = CAST(:d AS jsonb) WHERE env = :e"),
                {"v": old_settings[0], "d": json.dumps(old_settings[1]), "e": ENV},
            )


# --- OKX mock helpers ------------------------------------------------------------------------------


def ok(rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})


def refused_row(code: str, **ids: str) -> httpx.Response:
    return httpx.Response(
        200, json={"code": "1", "msg": "", "data": [{**ids, "sCode": code, "sMsg": "nope"}]}
    )


def pending_order(inst_id: str, ord_id: str) -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst_id,
        "ordId": ord_id,
        "clOrdId": f"cl{ord_id}",
        "px": "100",
        "sz": "5",
        "ordType": "limit",
        "side": "buy",
        "posSide": "net",
        "tdMode": "isolated",
        "accFillSz": "0",
        "state": "live",
        "cTime": str(NOW_MS),
        "uTime": str(NOW_MS),
    }


def algo(inst_id: str, algo_id: str, kind: str, *, pos_side: str = "net") -> dict[str, Any]:
    """kind: ``tp`` (take-profit-only conditional), ``sl`` (stop-loss conditional) or ``oco``."""
    row: dict[str, Any] = {
        "algoId": algo_id,
        "algoClOrdId": "",
        "instType": "SWAP",
        "instId": inst_id,
        "ordType": "oco" if kind == "oco" else "conditional",
        "side": "sell",
        "posSide": pos_side,
        "tdMode": "isolated",
        "sz": "5",
        "state": "live",
        "slTriggerPx": "",
        "slOrdPx": "",
        "tpTriggerPx": "",
        "tpOrdPx": "",
        "cTime": str(NOW_MS),
        "uTime": str(NOW_MS),
    }
    if kind in ("sl", "oco"):
        row |= {"slTriggerPx": "90", "slOrdPx": "-1"}
    if kind in ("tp", "oco"):
        row |= {"tpTriggerPx": "120", "tpOrdPx": "-1"}
    return row


def position(inst_id: str, pos: str = "5") -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst_id,
        "mgnMode": "isolated",
        "posId": f"p{inst_id}",
        "posSide": "net",
        "pos": pos,
        "avgPx": "100",
        "cTime": str(NOW_MS),
        "uTime": str(NOW_MS),
    }


class FakeSleep:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def factory(creds: Any, **kw: Any) -> OkxClient:
    return OkxClient(ENV, CREDS, wall_clock_ms=lambda: NOW_MS, **kw)


def posts(router: respx.MockRouter, path: str) -> list[Any]:
    return [
        json.loads(call.request.content)
        for call in router.calls
        if call.request.method == "POST" and call.request.url.path == f"/api/v5/{path}"
    ]


def envelopes(rds: redis.Redis, stream: str) -> list[EventEnvelope]:
    return [EventEnvelope.model_validate_json(f[bus.FIELD]) for _id, f in rds.xrange(stream)]


def paused(engine: Engine) -> bool:
    with engine.connect() as c:
        return bool(c.scalar(select(TradingState.paused).where(TradingState.env == ENV)))


def mock_okx(
    router: respx.MockRouter,
    insts: list[str],
    *,
    orders: list[dict[str, Any]],
    algos: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> None:
    router.get("/api/v5/public/time").mock(return_value=httpx.Response(200, json=TIME))
    router.get("/api/v5/trade/orders-pending").mock(return_value=ok(orders))

    def algo_pending(request: httpx.Request) -> httpx.Response:
        ord_type = request.url.params.get("ordType", "")
        inst = request.url.params.get("instId")
        rows = [
            a for a in algos if a["ordType"] in ord_type.split(",") and (inst is None or a["instId"] == inst)
        ]
        return ok(rows)

    router.get("/api/v5/trade/orders-algo-pending").mock(side_effect=algo_pending)

    def positions_answer(request: httpx.Request) -> httpx.Response:
        inst = request.url.params.get("instId")
        return ok([p for p in positions if inst is None or p["instId"] == inst])

    router.get("/api/v5/account/positions").mock(side_effect=positions_answer)

    def cancel_algos(request: httpx.Request) -> httpx.Response:
        rows = json.loads(request.content)
        return ok([{"algoId": r["algoId"], "algoClOrdId": "", "sCode": "0", "sMsg": ""} for r in rows])

    router.post("/api/v5/trade/cancel-algos").mock(side_effect=cancel_algos)

    def close(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return ok(
            [{"instId": body["instId"], "posSide": body.get("posSide", ""), "clOrdId": body["clOrdId"]}]
        )

    router.post("/api/v5/trade/close-position").mock(side_effect=close)


async def run_kill(engine: Engine, rds: redis.Redis, flatten: bool, sleep: FakeSleep | None = None) -> Any:
    return await control.kill(
        ENV,
        flatten=flatten,
        engine=engine,
        redis_client=rds,
        client_factory=factory,
        sleep=sleep or FakeSleep(),
        now=lambda: AT,
    )


# --- kill ---------------------------------------------------------------------------------------------


async def test_kill_pauses_first_cancels_entries_and_tp_only_keeps_stops_and_closes_nothing(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    a, b = world["insts"]
    paused_at_cancel: list[bool] = []
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_okx(
            router,
            world["insts"],
            orders=[pending_order(a, "1001"), pending_order(a, "1002"), pending_order(b, "1003")],
            algos=[algo(a, "7001", "tp"), algo(a, "7002", "sl"), algo(b, "7003", "oco")],
            positions=[position(a), position(b)],
        )

        def cancel(request: httpx.Request) -> httpx.Response:
            paused_at_cancel.append(paused(engine))
            body = json.loads(request.content)
            return ok([{"ordId": body["ordId"], "clOrdId": "", "sCode": "0", "sMsg": ""}])

        router.post("/api/v5/trade/cancel-order").mock(side_effect=cancel)
        report = await run_kill(engine, rds, flatten=False)

        assert paused_at_cancel == [True, True, True]
        assert sorted(b["ordId"] for b in posts(router, "trade/cancel-order")) == ["1001", "1002", "1003"]
        cancelled_algos = [r["algoId"] for batch in posts(router, "trade/cancel-algos") for r in batch]
        assert cancelled_algos == ["7001"]
        assert posts(router, "trade/close-position") == []
        assert not any(c.request.url.path == "/api/v5/account/positions" for c in router.calls)

    assert report["ok"] is True
    assert report["cancelled"] == 4
    assert report["cancelled_orders"] == 3
    assert report["cancelled_algos"] == 1
    assert report["stops_kept"] == 2
    assert report["closed"] == 0
    assert report["failed"] == []
    kept = sorted(i["ref"] for i in report["items"] if i["action"] == "kept")
    assert kept == ["7002", "7003"]
    with engine.connect() as c:
        reason = c.scalar(select(TradingState.reason).where(TradingState.env == ENV))
    assert paused(engine) and reason == "Kill switch."

    system = envelopes(rds, "atlas.system")
    assert [e.type for e in system] == ["system.trading_state", "system.kill"]
    assert system[0].payload["paused"] is True
    assert system[1].payload["stops_kept"] == 2
    room = [e for e in envelopes(rds, "atlas.warroom") if e.type == "warroom.message"]
    assert len(room) == 1
    assert room[0].payload["kind"] == "system_alert"
    assert "kept 2 stop-loss order(s)" in room[0].payload["content_md"]
    assert room[0].payload["card"]["type"] == "kill_switch"


async def test_kill_with_flatten_closes_each_position_reduce_only_then_its_algos(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    a, b = world["insts"]
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_okx(
            router,
            world["insts"],
            orders=[pending_order(a, "1001")],
            algos=[algo(a, "7001", "tp"), algo(a, "7002", "sl"), algo(b, "7003", "oco")],
            positions=[position(a), position(b, "-3"), position(f"ZZ{a}", "0")],
        )
        router.post("/api/v5/trade/cancel-order").mock(
            return_value=ok([{"ordId": "1001", "clOrdId": "", "sCode": "0", "sMsg": ""}])
        )
        report = await run_kill(engine, rds, flatten=True)

        closes = posts(router, "trade/close-position")
        assert sorted(c["instId"] for c in closes) == sorted([a, b])
        for c in closes:
            assert c["mgnMode"] == "isolated"
            assert c["autoCxl"] is True
            assert "posSide" not in c  # net mode
            assert len(c["clOrdId"]) == 32
        # Each position's remaining stops go only after its close was accepted.
        order = [
            (c.request.url.path, c.request.content)
            for c in router.calls
            if c.request.method == "POST" and c.request.url.path != "/api/v5/trade/cancel-order"
        ]
        paths = [p for p, _ in order]
        assert paths == [
            "/api/v5/trade/cancel-algos",
            "/api/v5/trade/close-position",
            "/api/v5/trade/cancel-algos",
            "/api/v5/trade/close-position",
            "/api/v5/trade/cancel-algos",
        ]
        cancelled = [r["algoId"] for batch in posts(router, "trade/cancel-algos") for r in batch]
        assert cancelled == ["7001", "7002", "7003"]

    assert report["closed"] == 2
    assert report["stops_kept"] == 0
    assert report["failed"] == []
    with engine.connect() as c:
        reason = c.scalar(select(TradingState.reason).where(TradingState.env == ENV))
    assert reason == "Kill switch (positions closed)."


async def test_a_cancel_retried_into_success_is_cancelled_and_one_that_never_succeeds_is_failed(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    a, _ = world["insts"]
    attempts: dict[str, int] = {}
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_okx(
            router,
            world["insts"],
            orders=[pending_order(a, "2001"), pending_order(a, "2002")],
            algos=[],
            positions=[],
        )

        def cancel(request: httpx.Request) -> httpx.Response:
            ord_id = json.loads(request.content)["ordId"]
            attempts[ord_id] = attempts.get(ord_id, 0) + 1
            if ord_id == "2001" and attempts[ord_id] <= 2:
                return httpx.Response(502)
            if ord_id == "2002":
                return refused_row("51410", ordId=ord_id, clOrdId="")
            return ok([{"ordId": ord_id, "clOrdId": "", "sCode": "0", "sMsg": ""}])

        router.post("/api/v5/trade/cancel-order").mock(side_effect=cancel)
        sleep = FakeSleep()
        report = await run_kill(engine, rds, flatten=False, sleep=sleep)

    assert attempts == {"2001": 3, "2002": 3}
    assert len(sleep.sleeps) == 4  # two backoffs per retried order
    items = {i["ref"]: i for i in report["items"]}
    assert items["2001"]["action"] == "cancelled"
    assert items["2001"]["attempts"] == 3
    assert report["cancelled"] == 1
    assert report["ok"] is False
    assert len(report["failed"]) == 1
    failed = report["failed"][0]
    assert failed["ref"] == "2002"
    assert failed["code"] == "51410"
    assert failed["message"] == human_message("51410", "nope")
    room = [e for e in envelopes(rds, "atlas.warroom") if e.type == "warroom.message"]
    assert human_message("51410", "nope") in room[0].payload["content_md"]


async def test_an_order_okx_already_closed_is_not_a_failure(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    a, _ = world["insts"]
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_okx(router, world["insts"], orders=[pending_order(a, "3001")], algos=[], positions=[])
        router.post("/api/v5/trade/cancel-order").mock(
            return_value=refused_row("51401", ordId="3001", clOrdId="")
        )
        report = await run_kill(engine, rds, flatten=False)
        assert len(posts(router, "trade/cancel-order")) == 1
    assert report["failed"] == []
    assert report["already_done"] == 1


# --- pause / resume -----------------------------------------------------------------------------------


def test_resume_clears_an_okx_errors_breaker_and_unpauses(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    breakers = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    assert breakers.trip("okx_errors", "3 OKX errors in the last 5 minutes.", {"count": 3})
    control.pause(engine, ENV, reason="Owner pause", redis_client=rds, now=lambda: AT)
    assert paused(engine)

    payload = control.resume(engine, ENV, redis_client=rds, now=lambda: AT)

    assert payload["paused"] is False
    assert payload["breakers_cleared"] == ["okx_errors"]
    assert payload["active_breakers"] == []
    assert not paused(engine)
    assert breakers.active() == {}
    assert breakers.entries()["okx_errors"]["cleared_by"] == "owner"
    states = [e.payload for e in envelopes(rds, "atlas.system") if e.type == "system.trading_state"]
    assert [s["paused"] for s in states] == [True, False]


def test_resume_leaves_the_self_clearing_stale_data_breaker_to_the_monitor(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    breakers = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    breakers.trip("data_stale", "No market data.", {})
    payload = control.resume(engine, ENV, redis_client=rds, now=lambda: AT)
    assert payload["breakers_cleared"] == []
    assert payload["active_breakers"] == ["data_stale"]


# --- one position, one order ------------------------------------------------------------------------


async def test_closing_one_position_closes_only_that_instrument_and_cancels_its_algos(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    a, b = world["insts"]
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_okx(
            router,
            world["insts"],
            orders=[],
            algos=[algo(a, "7101", "sl"), algo(a, "7102", "tp"), algo(b, "7103", "oco")],
            positions=[position(a), position(b)],
        )
        report = await control.close_position(
            ENV, a, None, engine=engine, redis_client=rds, client_factory=factory, sleep=FakeSleep()
        )
        closes = posts(router, "trade/close-position")
        assert [c["instId"] for c in closes] == [a]
        cancelled = sorted(r["algoId"] for batch in posts(router, "trade/cancel-algos") for r in batch)
        assert cancelled == ["7101", "7102"]
        assert posts(router, "trade/cancel-order") == []
    assert report["status"] == "closed"
    assert report["closed"] == 1
    assert report["cancelled_algos"] == 2
    assert not paused(engine)


async def test_closing_a_position_that_is_not_open_is_404(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    a, b = world["insts"]
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_okx(router, world["insts"], orders=[], algos=[], positions=[position(b)])
        with pytest.raises(control.ControlError) as exc:
            await control.close_position(
                ENV, a, None, engine=engine, redis_client=rds, client_factory=factory, sleep=FakeSleep()
            )
        assert posts(router, "trade/close-position") == []
    assert exc.value.status == 404


def seed_entry(engine: Engine, inst_id: str) -> uuid.UUID:
    """An Atlas entry order (ord_id 4001) with an attached SL/TP row and a stand-alone OCO row."""
    pid, rid, oid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
                entry=D(100),
                stop=D(90),
                expected_r=D("2.5"),
                take_profits=[{"price": 120, "pct": 50}],
                leverage_suggestion=3,
                size_suggestion_pct=D(5),
                status="sent",
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
                result="approved",
                checks=[],
                final_size=D(5),
                final_notional_usd=D(500),
                leverage=3,
                reason="test",
                decision="send_now",
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            Order.__table__.insert().values(
                id=oid,
                env=ENV,
                proposal_id=pid,
                risk_check_id=rid,
                cl_ord_id="e" + rid.hex[1:],
                ord_id="4001",
                inst_id=inst_id,
                side="buy",
                pos_side="net",
                ord_type="limit",
                td_mode="isolated",
                px=D(100),
                sz=D(5),
                state="live",
                origin="atlas",
                purpose="entry",
                created_at=AT,
                updated_at=AT,
            )
        )
        for cl, algo_id in (("s" + rid.hex[1:], "8801"), ("o" + rid.hex[1:], "8802")):
            c.execute(
                AlgoOrder.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=ENV,
                    order_id=oid,
                    algo_id=algo_id,
                    algo_cl_ord_id=cl,
                    inst_id=inst_id,
                    kind="oco",
                    side="sell",
                    pos_side="net",
                    sl_trigger_px=D(90),
                    tp_trigger_px=D(120),
                    sz=D(5),
                    state="live",
                    origin="atlas",
                    created_at=AT,
                    updated_at=AT,
                )
            )
    return oid


def order_detail(inst_id: str, filled: str) -> dict[str, Any]:
    return {**pending_order(inst_id, "4001"), "state": "canceled", "accFillSz": filled}


@pytest.mark.parametrize(("filled", "cancelled_algos"), [("0", ["8802"]), ("2", [])])
async def test_cancelling_an_entry_cancels_its_protection_only_when_nothing_filled(
    engine: Engine, rds: redis.Redis, world: dict[str, Any], filled: str, cancelled_algos: list[str]
) -> None:
    a, _ = world["insts"]
    oid = seed_entry(engine, a)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_okx(router, world["insts"], orders=[], algos=[], positions=[])
        router.post("/api/v5/trade/cancel-order").mock(
            return_value=ok([{"ordId": "4001", "clOrdId": "", "sCode": "0", "sMsg": ""}])
        )
        router.get("/api/v5/trade/order").mock(return_value=ok([order_detail(a, filled)]))
        report = await control.cancel_order(
            ENV, str(oid), engine=engine, redis_client=rds, client_factory=factory, sleep=FakeSleep()
        )
        assert posts(router, "trade/cancel-order") == [{"instId": a, "ordId": "4001"}]
        sent = [r["algoId"] for batch in posts(router, "trade/cancel-algos") for r in batch]
        assert sent == cancelled_algos
    assert report["status"] == "cancelled"
    assert report["filled_sz"] == filled
    with engine.connect() as c:
        order = c.execute(select(Order.state, Order.filled_sz).where(Order.id == oid)).one()
        algo_states = dict(
            (r.algo_id, r.state)
            for r in c.execute(select(AlgoOrder.algo_id, AlgoOrder.state).where(AlgoOrder.order_id == oid))
        )
    assert order.state == "canceled"
    assert order.filled_sz == D(filled)
    if filled == "0":
        assert algo_states == {"8801": "canceled", "8802": "canceled"}
    else:
        assert algo_states == {"8801": "live", "8802": "live"}
        assert report["stops_kept"] == 2


async def test_cancelling_an_unknown_or_closed_order_sends_nothing(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    a, _ = world["insts"]
    oid = seed_entry(engine, a)
    with engine.begin() as c:
        c.execute(update(Order).where(Order.id == oid).values(state="filled", filled_sz=D(5)))
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        for target, status in ((str(oid), 409), (str(uuid.uuid4()), 404), ("nope", 404)):
            with pytest.raises(control.ControlError) as exc:
                await control.cancel_order(
                    ENV, target, engine=engine, redis_client=rds, client_factory=factory, sleep=FakeSleep()
                )
            assert exc.value.status == status
        assert router.calls.call_count == 0


# --- routes -------------------------------------------------------------------------------------------


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


ROUTES = (
    "/internal/system/pause",
    "/internal/system/resume",
    "/internal/system/kill",
    "/internal/positions/BTC-USDT-SWAP/close",
    f"/internal/orders/{uuid.uuid4()}/cancel",
)


def test_every_control_route_is_401_without_a_valid_signature(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    with respx.mock(assert_all_called=False) as router, app_client(engine, rds) as client:
        for path in ROUTES:
            body = b'{"flatten": true}'
            assert client.post(path, content=body).status_code == 401, path
            bad = signed_headers("not-the-secret", "POST", path, body)
            assert client.post(path, content=body, headers=bad).status_code == 401, path
            other = signed_headers(SECRET, "POST", "/internal/system/pause", body)
            if path != "/internal/system/pause":
                assert client.post(path, content=body, headers=other).status_code == 401, path
        assert router.calls.call_count == 0
    assert not paused(engine)
    assert envelopes(rds, "atlas.system") == []


def test_signed_pause_resume_and_kill_routes(engine: Engine, rds: redis.Redis, world: dict[str, Any]) -> None:
    a, _ = world["insts"]
    with respx.mock(base_url=BASE, assert_all_called=False) as router, app_client(engine, rds) as client:
        mock_okx(router, world["insts"], orders=[], algos=[algo(a, "7201", "sl")], positions=[])

        def call(path: str, body: bytes = b"") -> httpx.Response:
            return client.post(path, content=body, headers=signed_headers(SECRET, "POST", path, body))

        resp = call("/internal/system/pause", b'{"reason": "lunch"}')
        assert resp.status_code == 200 and resp.json()["paused"] is True
        assert resp.json()["reason"] == "lunch"
        assert call("/internal/system/kill", b'{"flatten": "yes"}').status_code == 422
        resp = call("/internal/system/kill", b'{"flatten": false}')
        assert resp.status_code == 200
        assert resp.json()["stops_kept"] == 1
        assert resp.json()["paused"] is True
        resp = call("/internal/system/resume")
        assert resp.status_code == 200 and resp.json()["paused"] is False
        assert call(f"/internal/positions/{a}/close", b'{"pos_side": "sideways"}').status_code == 422
        assert call(f"/internal/positions/{a}/close", b"{}").status_code == 404
        assert call(f"/internal/orders/{uuid.uuid4()}/cancel").status_code == 404
    assert not paused(engine)
