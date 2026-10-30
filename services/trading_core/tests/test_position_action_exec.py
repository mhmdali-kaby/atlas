"""ATL-185: the Executor carries out an approved Portfolio Manager amend_stop, amend_targets,
partial_close or close on OKX.

Each test writes a PM proposal, runs the real Risk Engine on it (``evaluate``, ATL-184), then hands the
risk check to ``execute``. OKX is a stateful respx mock (one position, pending algo orders in a dict that
amend/cancel/place change, orders recorded by clOrdId). The database and Redis are this checkout's test
instances.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
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
    Connection,
    Instrument,
    Order,
    PositionSnapshot,
    Proposal,
    RiskCheck,
    TradingState,
    make_engine,
)
from atlas_shared.events import EventEnvelope
from atlas_shared.market_data import md_key, to_ms
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, select, text, update
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.execution.config import derived_id
from trading_core.execution.executor import execute
from trading_core.execution.lifecycle import EntryLifecycle
from trading_core.okx import OkxClient, OkxCredentials
from trading_core.risk.engine import evaluate
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

ENV = "demo"
AT = datetime(2031, 3, 14, 15, 0, tzinfo=UTC)
BASE = "https://www.okx.com"
CREDS = OkxCredentials(api_key="test-key-0000", secret="test-only-secret", passphrase="p")  # noqa: S106
NOW_MS = 1_793_207_736_680
TIME = {"code": "0", "msg": "", "data": [{"ts": str(NOW_MS)}]}
STREAMS = ("atlas.orders", "atlas.system", "atlas.warroom", "atlas.proposals", "atlas.risk")
STOP_ALGO = "900001"
TEST_DB = testing.db_settings()
# The PM marks the TP1 partial with this invalidation; ATL-169 reads tp1_filled from these statuses.
TP1_FILLED_STATUSES = {"sent", "partially_filled", "filled", "closed"}


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
def world(engine: Engine, rds: redis.Redis) -> Iterator[dict[str, Any]]:
    """An open long of 10 contracts opened by an Atlas entry, protected by one live SL at 3900."""
    inst_id = f"PX{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
    entry_pid, entry_rc, entry_order = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    stop_cl = derived_id("s", entry_rc)
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
            select(TradingState.paused, TradingState.breakers).where(TradingState.env == ENV)
        ).first()
        c.execute(
            text(
                "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level,"
                " max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at)"
                " VALUES (:id, :e, 9, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = 9, data = EXCLUDED.data"
            ),
            {
                "id": uuid.uuid4(),
                "e": ENV,
                "d": json.dumps({"risk_profile": "balanced", "autonomy_level": 2, "order_max_retries": 3}),
            },
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
        else:
            c.execute(update(Connection).where(Connection.id == old_conn.id).values(last_verified_at=AT))
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        c.execute(
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type="SWAP",
                base_ccy=inst_id[:8],
                quote_ccy="USDT",
                settle_ccy="USDT",
                ct_val=D("0.01"),
                ct_mult=D(1),
                lot_sz=D(1),
                min_sz=D(1),
                tick_sz=D("0.01"),
                max_lever=D(50),
                state="live",
                asset_class="commodity",
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            PositionSnapshot.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                pos_side="net",
                pos=D(10),
                avg_px=D(3950),
                mark_px=D(4000),
                mgn_mode="isolated",
                taken_at=AT - timedelta(seconds=30),
                created_at=AT,
                updated_at=AT,
            )
        )
        # The Atlas entry that opened the position, and its stop.
        c.execute(
            insert(Proposal).values(
                id=entry_pid,
                env=ENV,
                version=1,
                kind="entry",
                author="strategist",
                inst_id=inst_id,
                side="buy",
                order_type="limit",
                entry=D(3950),
                stop=D(3900),
                rationale_md="entry",
                invalidation="x",
                status="filled",
                created_at=AT - timedelta(hours=2),
                updated_at=AT - timedelta(hours=2),
            )
        )
        c.execute(
            insert(RiskCheck).values(
                id=entry_rc,
                env=ENV,
                proposal_id=entry_pid,
                settings_version=9,
                result="approved",
                checks=[],
                final_size=D(10),
                leverage=3,
                decision="send_now",
                created_at=AT - timedelta(hours=2),
                updated_at=AT - timedelta(hours=2),
            )
        )
        c.execute(
            insert(Order).values(
                id=entry_order,
                env=ENV,
                proposal_id=entry_pid,
                risk_check_id=entry_rc,
                cl_ord_id=derived_id("e", entry_rc),
                ord_id="800001",
                inst_id=inst_id,
                side="buy",
                pos_side=None,
                ord_type="limit",
                td_mode="isolated",
                px=D(3950),
                sz=D(10),
                filled_sz=D(10),
                state="filled",
                reduce_only=False,
                origin="atlas",
                purpose="entry",
                raw={},
                created_at=AT - timedelta(hours=2),
                updated_at=AT - timedelta(hours=2),
            )
        )
        c.execute(
            AlgoOrder.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                order_id=entry_order,
                algo_id=STOP_ALGO,
                algo_cl_ord_id=stop_cl,
                inst_id=inst_id,
                kind="sl",
                side="sell",
                pos_side=None,
                trigger_px=D(3900),
                sz=D(10),
                state="live",
                origin="atlas",
                created_at=AT,
                updated_at=AT,
            )
        )
    rds.hset(
        md_key(ENV, inst_id),
        mapping={
            "last_msg_ms": to_ms(AT - timedelta(seconds=2)),
            "tickers_ms": to_ms(AT - timedelta(seconds=2)),
            "ticker": json.dumps({"last": "4000"}),
            "mark": json.dumps({"markPx": "4000"}),
        },
    )
    yield {"inst_id": inst_id, "entry_order": entry_order, "stop_cl": stop_cl}
    rds.delete(md_key(ENV, inst_id))
    with engine.begin() as c:
        c.execute(delete(AlgoOrder).where(AlgoOrder.env == ENV, AlgoOrder.inst_id == inst_id))
        c.execute(delete(Order).where(Order.inst_id == inst_id))
        c.execute(delete(Proposal).where(Proposal.inst_id == inst_id))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
        c.execute(
            delete(PositionSnapshot).where(PositionSnapshot.env == ENV, PositionSnapshot.inst_id == inst_id)
        )
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        if old_state is not None:
            c.execute(
                TradingState.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=ENV,
                    paused=old_state.paused,
                    breakers=old_state.breakers,
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


# --- the recorded OKX ------------------------------------------------------------------------------


def ok(rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})


class FakeOkx:
    """One net long on ``inst_id``; pending algos and orders change with every write."""

    def __init__(
        self, router: respx.MockRouter, inst_id: str, *, stop_cl: str, stop_px: str = "3900"
    ) -> None:
        self.router = router
        self.inst_id = inst_id
        self.pos = D(10)
        self.fail_orders = 0
        self.algos: dict[str, dict[str, Any]] = {
            STOP_ALGO: self.algo_row(STOP_ALGO, stop_cl, sl=stop_px, sz="10"),
        }
        self.orders: dict[str, dict[str, Any]] = {}
        self.next_id = 700000
        router.get("/api/v5/public/time").mock(return_value=httpx.Response(200, json=TIME))
        router.get("/api/v5/account/positions").mock(side_effect=self._positions)
        router.get("/api/v5/trade/orders-algo-pending").mock(side_effect=self._algos_pending)
        router.get("/api/v5/trade/order").mock(side_effect=self._get_order)
        router.post("/api/v5/trade/amend-algos").mock(side_effect=self._amend)
        router.post("/api/v5/trade/order").mock(side_effect=self._order)
        router.post("/api/v5/trade/close-position").mock(side_effect=self._close)
        router.post("/api/v5/trade/cancel-algos").mock(side_effect=self._cancel)
        router.post("/api/v5/trade/order-algo").mock(side_effect=self._place_algo)

    def algo_row(
        self, algo_id: str, cl: str, *, sl: str = "", tp: str = "", sz: str, ord_type: str = "conditional"
    ) -> dict[str, Any]:
        return {
            "algoId": algo_id,
            "algoClOrdId": cl,
            "instType": "SWAP",
            "instId": self.inst_id,
            "ordType": ord_type,
            "side": "sell",
            "posSide": "net",
            "tdMode": "isolated",
            "sz": sz,
            "state": "live",
            "slTriggerPx": sl,
            "slOrdPx": "-1" if sl else "",
            "tpTriggerPx": tp,
            "tpOrdPx": "-1" if tp else "",
            "cTime": str(NOW_MS),
            "uTime": str(NOW_MS),
        }

    def _positions(self, request: httpx.Request) -> httpx.Response:
        if self.pos == 0:
            return ok([])
        return ok(
            [
                {
                    "instType": "SWAP",
                    "instId": self.inst_id,
                    "mgnMode": "isolated",
                    "posId": "p1",
                    "posSide": "net",
                    "pos": format(self.pos, "f"),
                    "avgPx": "3950",
                    "markPx": "4000",
                    "cTime": str(NOW_MS),
                    "uTime": str(NOW_MS),
                }
            ]
        )

    def _algos_pending(self, request: httpx.Request) -> httpx.Response:
        types = request.url.params.get("ordType", "").split(",")
        return ok([a for a in self.algos.values() if a["ordType"] in types])

    def _get_order(self, request: httpx.Request) -> httpx.Response:
        found = self.orders.get(request.url.params.get("clOrdId", ""))
        if found is None:
            return httpx.Response(200, json={"code": "51603", "msg": "Order does not exist", "data": []})
        return ok([found])

    def _amend(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        row = self.algos[body["algoId"]]
        if "newSz" in body:
            row["sz"] = body["newSz"]
        if "newSlTriggerPx" in body:
            row["slTriggerPx"] = body["newSlTriggerPx"]
        if "newTpTriggerPx" in body:
            row["tpTriggerPx"] = body["newTpTriggerPx"]
        return ok([{"algoId": body["algoId"], "algoClOrdId": row["algoClOrdId"], "sCode": "0", "sMsg": ""}])

    def _record(self, cl: str, sz: D, ord_type: str = "market") -> str:
        self.next_id += 1
        ord_id = str(self.next_id)
        self.orders[cl] = {
            "instType": "SWAP",
            "instId": self.inst_id,
            "ordId": ord_id,
            "clOrdId": cl,
            "sz": format(sz, "f"),
            "ordType": ord_type,
            "side": "sell",
            "posSide": "net",
            "tdMode": "isolated",
            "accFillSz": format(sz, "f"),
            "avgPx": "4000",
            "state": "filled",
            "reduceOnly": "true",
            "cTime": str(NOW_MS),
            "uTime": str(NOW_MS),
        }
        self.pos -= sz
        return ord_id

    def _order(self, request: httpx.Request) -> httpx.Response:
        if self.fail_orders > 0:
            self.fail_orders -= 1
            return httpx.Response(503, text="Service Unavailable")
        body = json.loads(request.content)
        ord_id = self._record(body["clOrdId"], D(body["sz"]))
        return ok([{"ordId": ord_id, "clOrdId": body["clOrdId"], "sCode": "0", "sMsg": ""}])

    def _close(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self._record(body["clOrdId"], self.pos)
        return ok([{"instId": body["instId"], "posSide": "", "clOrdId": body["clOrdId"]}])

    def _cancel(self, request: httpx.Request) -> httpx.Response:
        rows = json.loads(request.content)
        for r in rows:
            self.algos.pop(r["algoId"], None)
        return ok([{"algoId": r["algoId"], "algoClOrdId": "", "sCode": "0", "sMsg": ""} for r in rows])

    def _place_algo(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.next_id += 1
        algo_id = str(self.next_id)
        self.algos[algo_id] = self.algo_row(
            algo_id,
            body["algoClOrdId"],
            sl=body.get("slTriggerPx", ""),
            tp=body.get("tpTriggerPx", ""),
            sz=body["sz"],
        )
        return ok([{"algoId": algo_id, "algoClOrdId": body["algoClOrdId"], "sCode": "0", "sMsg": ""}])

    def posts(self, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(c.request.content)
            for c in self.router.calls
            if c.request.method == "POST" and c.request.url.path == f"/api/v5/{path}"
        ]

    def post_paths(self) -> list[str]:
        return [
            c.request.url.path.removeprefix("/api/v5/")
            for c in self.router.calls
            if c.request.method == "POST"
        ]


class FakeSleep:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def factory(creds: Any, **kw: Any) -> OkxClient:
    return OkxClient(ENV, CREDS, wall_clock_ms=lambda: NOW_MS, **kw)


# --- helpers ----------------------------------------------------------------------------------------


def pm_proposal(engine: Engine, inst_id: str, kind: str, **kw: Any) -> uuid.UUID:
    pid = uuid.uuid4()
    values: dict[str, Any] = {
        "id": pid,
        "env": ENV,
        "version": 1,
        "kind": kind,
        "author": "portfolio_manager",
        "inst_id": inst_id,
        "rationale_md": "rule fired",
        "invalidation": "breakeven_at_1r",
        "status": "passed",
        "created_at": AT,
        "updated_at": AT,
    }
    values.update(kw)
    with engine.begin() as c:
        c.execute(insert(Proposal).values(**values))
    return pid


def approve(engine: Engine, rds: redis.Redis, pid: uuid.UUID) -> uuid.UUID:
    decision = evaluate(pid, engine=engine, redis_client=rds, now=lambda: AT)
    assert decision.result == "approved", decision.reason
    assert decision.decision == "awaiting_owner"
    return decision.risk_check_id


async def run(engine: Engine, rds: redis.Redis, rc_id: uuid.UUID, sleep: FakeSleep | None = None) -> Any:
    return await execute(
        rc_id,
        "owner",
        engine=engine,
        redis_client=rds,
        client_factory=factory,
        active_env=ENV,
        sleep=sleep or FakeSleep(),
        now=lambda: AT,
    )


def proposal_status(engine: Engine, pid: uuid.UUID) -> tuple[str, str | None]:
    with engine.connect() as c:
        row = c.execute(select(Proposal.status, Proposal.status_reason).where(Proposal.id == pid)).one()
    return row.status, row.status_reason


def stored_stop(engine: Engine, cl: str) -> Any:
    with engine.connect() as c:
        return c.execute(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == cl)).one()


def envelopes(rds: redis.Redis, stream: str) -> list[EventEnvelope]:
    return [EventEnvelope.model_validate_json(f[bus.FIELD]) for _id, f in rds.xrange(stream)]


# --- AC1: amend_stop ---------------------------------------------------------------------------------


async def test_amend_stop_amends_the_live_stop_and_places_no_second_one(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "amend_stop", stop=D("3950.004"))
    rc_id = approve(engine, rds, pid)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        result = await run(engine, rds, rc_id)

        assert result.status == "sent", result.detail
        amends = okx.posts("trade/amend-algos")
        assert amends == [
            {"instId": inst, "algoId": STOP_ALGO, "newSlTriggerPx": "3950.01", "newSlOrdPx": "-1"}
        ]  # tick-rounded toward the position, never wider than the checked stop
        assert okx.posts("trade/order-algo") == []
        assert okx.posts("trade/order") == []
        assert okx.posts("trade/close-position") == []
        assert [a["slTriggerPx"] for a in okx.algos.values()] == ["3950.01"]
    assert stored_stop(engine, world["stop_cl"]).trigger_px == D("3950.01")
    assert proposal_status(engine, pid) == ("filled", None)
    cards = [e.payload["card"] for e in envelopes(rds, "atlas.warroom") if e.payload.get("kind") == "order"]
    assert cards and cards[-1]["type"] == "position_action" and cards[-1]["kind"] == "amend_stop"


async def test_amend_targets_moves_the_take_profit_and_never_touches_the_stop(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "amend_targets", take_profits=[{"price": "4100", "pct": "100"}])
    rc_id = approve(engine, rds, pid)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        result = await run(engine, rds, rc_id)

        assert result.status == "sent", result.detail
        assert okx.posts("trade/amend-algos") == []
        placed = okx.posts("trade/order-algo")
        assert len(placed) == 1 and placed[0]["tpTriggerPx"] == "4100" and "slTriggerPx" not in placed[0]
        assert placed[0]["reduceOnly"] is True and placed[0]["sz"] == "10"
        assert okx.algos[STOP_ALGO]["slTriggerPx"] == "3900"
    assert proposal_status(engine, pid) == ("filled", None)


# --- AC2: partial_close --------------------------------------------------------------------------------


async def test_partial_close_sends_one_reduce_only_order_for_half_and_resizes_the_stop(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "partial_close", close_pct=D(50), invalidation="tp1_partial")
    rc_id = approve(engine, rds, pid)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        result = await run(engine, rds, rc_id)

        assert result.status == "sent", result.detail
        orders = okx.posts("trade/order")
        assert len(orders) == 1
        assert orders[0] == {
            "instId": inst,
            "tdMode": "isolated",
            "side": "sell",
            "ordType": "market",
            "sz": "5",
            "clOrdId": derived_id("x", rc_id),
            "reduceOnly": True,
        }
        assert okx.posts("trade/amend-algos") == [{"instId": inst, "algoId": STOP_ALGO, "newSz": "5"}]
        assert okx.posts("trade/order-algo") == []
        assert okx.post_paths().index("trade/order") < okx.post_paths().index("trade/amend-algos")
        assert okx.pos == D(5) and okx.algos[STOP_ALGO]["sz"] == "5"

    stop = stored_stop(engine, world["stop_cl"])
    assert stop.sz == D(5) and stop.trigger_px == D(3900) and stop.state == "live"
    with engine.connect() as c:
        close = c.execute(select(Order).where(Order.cl_ord_id == derived_id("x", rc_id))).one()
    assert close.purpose == "close" and close.reduce_only and close.state == "filled"
    assert close.filled_sz == D(5) and close.raw["reduces_order_id"] == str(world["entry_order"])
    # The entry's own lifecycle now sizes its stop to what is left, so its sweep does not undo the resize.
    lifecycle = EntryLifecycle(engine, ENV, redis_client=rds, client_factory=factory, now=lambda: AT)
    assert world["entry_order"] not in lifecycle._unsettled_entries()
    # AC4 (second half): the TP1 partial is now what the PM reads as tp1_filled (ATL-169).
    status, _ = proposal_status(engine, pid)
    assert status in TP1_FILLED_STATUSES and status == "filled"


# --- AC3: close ----------------------------------------------------------------------------------------


async def test_close_sends_a_reduce_only_close_then_cancels_the_positions_algos(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "close", invalidation="time_stop")
    rc_id = approve(engine, rds, pid)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        okx.algos["900002"] = okx.algo_row("900002", "", tp="4200", sz="10")
        result = await run(engine, rds, rc_id)

        assert result.status == "sent", result.detail
        closes = okx.posts("trade/close-position")
        assert closes == [
            {"instId": inst, "mgnMode": "isolated", "autoCxl": True, "clOrdId": derived_id("x", rc_id)}
        ]
        cancels = okx.posts("trade/cancel-algos")
        assert len(cancels) == 1
        assert sorted(r["algoId"] for r in cancels[0]) == [STOP_ALGO, "900002"]
        paths = okx.post_paths()
        assert paths.index("trade/close-position") < paths.index("trade/cancel-algos")
        assert okx.posts("trade/order") == [] and okx.pos == 0 and okx.algos == {}
    assert stored_stop(engine, world["stop_cl"]).state == "canceled"
    assert proposal_status(engine, pid) == ("filled", None)
    lifecycle = EntryLifecycle(engine, ENV, redis_client=rds, client_factory=factory, now=lambda: AT)
    assert world["entry_order"] not in lifecycle._unsettled_entries()


async def test_a_close_okx_refuses_keeps_every_stop(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "close", invalidation="time_stop")
    rc_id = approve(engine, rds, pid)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        router.post("/api/v5/trade/close-position").mock(
            return_value=httpx.Response(200, json={"code": "51000", "msg": "Parameter error", "data": []})
        )
        result = await run(engine, rds, rc_id)

        assert result.status == "failed"
        assert okx.posts("trade/cancel-algos") == []
        assert STOP_ALGO in okx.algos
    assert stored_stop(engine, world["stop_cl"]).state == "live"


# --- AC4: retries reuse the clOrdId --------------------------------------------------------------------


async def test_a_failed_send_is_retried_with_the_same_cl_ord_id(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "partial_close", close_pct=D(50), invalidation="tp1_partial")
    rc_id = approve(engine, rds, pid)
    sleep = FakeSleep()
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        okx.fail_orders = 1
        result = await run(engine, rds, rc_id, sleep)

        assert result.status == "sent", result.detail
        sent = okx.posts("trade/order")
        assert len(sent) == 2
        assert sent[0] == sent[1] and sent[0]["clOrdId"] == derived_id("x", rc_id)
        assert list(okx.orders) == [derived_id("x", rc_id)] and okx.pos == D(5)
        assert sleep.sleeps  # backed off before the retry
    status, _ = proposal_status(engine, pid)
    assert status in TP1_FILLED_STATUSES


async def test_a_run_that_crashed_after_sending_resumes_with_the_same_cl_ord_id_and_sends_nothing_new(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "partial_close", close_pct=D(50), invalidation="tp1_partial")
    rc_id = approve(engine, rds, pid)
    cl = derived_id("x", rc_id)
    with engine.begin() as c:
        c.execute(
            insert(Order).values(
                id=uuid.uuid4(),
                env=ENV,
                proposal_id=pid,
                risk_check_id=rc_id,
                cl_ord_id=cl,
                inst_id=inst,
                side="sell",
                ord_type="market",
                td_mode="isolated",
                sz=D(5),
                state="sending",
                reduce_only=True,
                origin="atlas",
                purpose="close",
                raw={"position_action": "partial_close", "position_contracts": "10"},
                created_at=AT,
                updated_at=AT,
            )
        )
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        okx._record(cl, D(5))  # the crashed run's order did land on OKX
        result = await run(engine, rds, rc_id)

        assert result.status == "sent", result.detail
        assert okx.posts("trade/order") == []
        assert okx.posts("trade/amend-algos") == [{"instId": inst, "algoId": STOP_ALGO, "newSz": "5"}]
    status, _ = proposal_status(engine, pid)
    assert status == "filled"


# --- AC5: the re-check right before sending ------------------------------------------------------------


async def test_a_stop_that_would_now_widen_against_the_live_okx_stop_is_not_sent_and_is_rejected(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "amend_stop", stop=D(3920))
    rc_id = approve(engine, rds, pid)  # against the stored stop 3900 it tightens
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        # On OKX the stop has since moved to 3950: 3920 would now widen it.
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"], stop_px="3950")
        result = await run(engine, rds, rc_id)

        assert result.status == "refused" and result.reason == "risk_rejected"
        assert okx.posts("trade/amend-algos") == []
        assert okx.posts("trade/order-algo") == []
        assert okx.posts("trade/order") == []
        assert okx.algos[STOP_ALGO]["slTriggerPx"] == "3950"
    assert proposal_status(engine, pid) == ("rejected", "risk_rejected")
    with engine.connect() as c:
        latest = c.execute(
            select(RiskCheck).where(RiskCheck.proposal_id == pid, RiskCheck.result == "rejected")
        ).first()
        checks = c.scalars(select(RiskCheck.result).where(RiskCheck.proposal_id == pid)).all()
    assert sorted(checks) == ["approved", "rejected"]
    assert latest is not None
    failed = next(ch for ch in latest.checks if ch["status"] == "failed")
    assert failed["check"] == "stop_tightens" and failed["code"] == "stop_widens"
    rejected = [e for e in envelopes(rds, "atlas.proposals") if e.type == "proposal.rejected"]
    assert rejected and rejected[-1].payload["proposal_id"] == str(pid)
    # A second attempt is refused before OKX: the proposal is closed.
    again = await run(engine, rds, rc_id)
    assert again.status == "refused"


async def test_an_amend_stop_that_now_crosses_the_okx_mark_is_rejected(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "amend_stop", stop=D(3980))
    rc_id = approve(engine, rds, pid)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        router.get("/api/v5/account/positions").mock(
            return_value=ok(
                [
                    {
                        "instType": "SWAP",
                        "instId": inst,
                        "mgnMode": "isolated",
                        "posId": "p1",
                        "posSide": "net",
                        "pos": "10",
                        "avgPx": "3950",
                        "markPx": "3970",
                        "cTime": str(NOW_MS),
                        "uTime": str(NOW_MS),
                    }
                ]
            )
        )
        result = await run(engine, rds, rc_id)
        assert result.status == "refused" and okx.posts("trade/amend-algos") == []
    assert proposal_status(engine, pid) == ("rejected", "risk_rejected")


# --- the owner's approval route ----------------------------------------------------------------------

SECRET = "atlas-test-only-internal-secret"  # noqa: S105


def approval_client(engine: Engine, rds: redis.Redis) -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=8100,
        env=ENV,
        internal_shared_secret=SECRET,
        run_scheduler=False,
        run_market_ws=False,
        run_private_ws=False,
    )
    app = create_app(settings, scheduler=Scheduler(), engine=engine)
    app.state.risk_redis = rds
    app.state.clock = lambda: AT
    app.state.executor_client_factory = factory
    return TestClient(app)


def awaiting_owner(engine: Engine, rds: redis.Redis, pid: uuid.UUID) -> None:
    """What ATL-169's routing does with an approved PM action at autonomy 2."""
    approve(engine, rds, pid)
    with engine.begin() as c:
        c.execute(
            update(Proposal)
            .where(Proposal.id == pid)
            .values(status="awaiting_owner", approval_expires_at=AT + timedelta(minutes=10))
        )


def owner_approves(tc: TestClient, pid: uuid.UUID) -> Any:
    path = f"/internal/proposals/{pid}/execute"
    body = json.dumps({"approved_by": "owner"}).encode()
    return tc.post(path, content=body, headers=signed_headers(SECRET, "POST", path, body))


def test_owner_approval_of_a_pm_amend_stop_reaches_okx(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "amend_stop", stop=D(3920))
    awaiting_owner(engine, rds, pid)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"])
        res = owner_approves(approval_client(engine, rds), pid)

        assert res.status_code == 200, res.json()
        assert res.json()["risk_check"]["result"] == "approved"
        assert [a["newSlTriggerPx"] for a in okx.posts("trade/amend-algos")] == ["3920"]
    assert proposal_status(engine, pid) == ("filled", None)


def test_owner_approval_of_a_stop_that_now_widens_sends_nothing_and_rejects(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    inst = world["inst_id"]
    pid = pm_proposal(engine, inst, "amend_stop", stop=D(3920))
    awaiting_owner(engine, rds, pid)
    with engine.begin() as c:  # the stop was tightened to 3950 while the owner was deciding
        c.execute(
            update(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == world["stop_cl"]).values(trigger_px=D(3950))
        )
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, inst, stop_cl=world["stop_cl"], stop_px="3950")
        res = owner_approves(approval_client(engine, rds), pid)

        assert res.status_code == 409 and res.json()["reason"] == "risk_rejected"
        assert okx.posts("trade/amend-algos") == []
    assert proposal_status(engine, pid) == ("rejected", "risk_rejected")
